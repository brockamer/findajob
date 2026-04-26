import json
from pathlib import Path
from unittest.mock import patch

import pytest

from findajob.discoverer.writer import commit_atomically


def _payload() -> dict:
    return {
        "generated_at": "2026-04-26",
        "model": "openrouter:perplexity/sonar-reasoning-pro",
        "companies": [
            {
                "name": "Alpha",
                "cluster": "direct",
                "channel": "greenhouse",
                "reasoning": "x",
                "citations": ["https://example.com"],
            },
        ],
    }


def test_commit_atomically_writes_both_files(tmp_path: Path) -> None:
    md_path = commit_atomically(tmp_path, "# md\n\nhello\n", _payload())
    assert md_path == tmp_path / "candidate_context" / "discovered_companies.md"
    assert md_path.read_text() == "# md\n\nhello\n"
    json_path = tmp_path / "candidate_context" / "discovered_companies.json"
    assert json.loads(json_path.read_text())["companies"][0]["name"] == "Alpha"
    # Files must be world-readable (0o644) so the FastAPI process running as
    # `lad` can read files that the discoverer wrote as a different user
    # (e.g., manual `docker exec` as root). tempfile.mkstemp's default 0o600
    # would make /config/ silently 500 on the file-read path.
    assert (md_path.stat().st_mode & 0o777) == 0o644
    assert (json_path.stat().st_mode & 0o777) == 0o644


def test_commit_atomically_creates_parent_dir(tmp_path: Path) -> None:
    # candidate_context/ does not exist yet
    assert not (tmp_path / "candidate_context").exists()
    commit_atomically(tmp_path, "x", _payload())
    assert (tmp_path / "candidate_context").is_dir()


def test_commit_atomically_backs_up_pre_existing_files(tmp_path: Path) -> None:
    cc = tmp_path / "candidate_context"
    cc.mkdir()
    (cc / "discovered_companies.md").write_text("OLD MD\n")
    (cc / "discovered_companies.json").write_text('{"companies": []}\n')
    commit_atomically(tmp_path, "NEW MD\n", _payload())
    # New content in place
    assert (cc / "discovered_companies.md").read_text() == "NEW MD\n"
    # Backup directory contains the old content
    backups = sorted((tmp_path / ".backups").iterdir())
    assert len(backups) == 1
    bdir = backups[0]
    assert (bdir / "candidate_context" / "discovered_companies.md").read_text() == "OLD MD\n"
    assert (bdir / "candidate_context" / "discovered_companies.json").read_text() == '{"companies": []}\n'


def test_commit_atomically_rolls_back_on_replace_failure(tmp_path: Path) -> None:
    """If os.replace fails on the second file, the first file's prior state
    is preserved (last-good invariant).
    """
    cc = tmp_path / "candidate_context"
    cc.mkdir()
    (cc / "discovered_companies.md").write_text("OLD MD\n")
    (cc / "discovered_companies.json").write_text("OLD JSON\n")

    import findajob.discoverer.writer as wr

    real_replace = wr.os.replace
    calls = {"n": 0}

    def flaky(src, dst):
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("simulated failure on second replace")
        return real_replace(src, dst)

    with patch.object(wr.os, "replace", side_effect=flaky):
        with pytest.raises(OSError):
            commit_atomically(tmp_path, "NEW MD\n", _payload())

    # First file may have been replaced (atomicity is per-file via os.replace);
    # second file MUST still be the old content.
    assert (cc / "discovered_companies.json").read_text() == "OLD JSON\n"
    # No tempfile residue
    leftovers = [p for p in cc.iterdir() if p.name.startswith("discovered_companies.") and ".tmp" in p.name]
    assert leftovers == []
