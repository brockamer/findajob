"""Tests for findajob.loose_ends.surface_map (#572).

Each test writes a synthetic source tree into tmp_path and asserts the
walker finds the expected user-input file references.
"""

from pathlib import Path

from findajob.loose_ends.surface_map import CallSite, walk_surface_map


def test_extracts_config_path_literal(tmp_path: Path) -> None:
    """A module that references 'config/foo.yaml' as a string literal is
    detected as a consumer of that file."""
    src = tmp_path / "src" / "findajob"
    src.mkdir(parents=True)
    (src / "consumer.py").write_text(
        'from pathlib import Path\nPATH = "config/foo.yaml"\ndef load():\n    return Path(PATH).read_text()\n'
    )
    result = walk_surface_map(repo_root=tmp_path)
    assert "config/foo.yaml" in result
    sites = result["config/foo.yaml"]
    assert len(sites) >= 1
    assert isinstance(sites[0], CallSite)
    assert sites[0].file.endswith("consumer.py")


def test_walks_scripts_directory(tmp_path: Path) -> None:
    """Shims under scripts/ are walked too — many cron entry points live there."""
    src = tmp_path / "scripts"
    src.mkdir(parents=True)
    (src / "shim.py").write_text('PATH = "candidate_context/profile.md"\n')
    result = walk_surface_map(repo_root=tmp_path)
    assert "candidate_context/profile.md" in result
    assert any(s.file.endswith("shim.py") for s in result["candidate_context/profile.md"])


def test_quoted_literal_matches_even_in_docstring(tmp_path: Path) -> None:
    """Documents that the regex doesn't distinguish docstring-quoted literals
    from real code references. False positives are operator-correctable via
    audit_exclusions.yaml — per spec, the bias is deliberate."""
    src = tmp_path / "src" / "findajob"
    src.mkdir(parents=True)
    (src / "consumer.py").write_text('"""See config/foo.yaml for details."""\nPATH = "config/bar.yaml"\n')
    result = walk_surface_map(repo_root=tmp_path)
    # Both register — this is intentional. Spec error-handling section
    # treats false-positives as cheaper than false-negatives.
    assert "config/foo.yaml" in result
    assert "config/bar.yaml" in result
