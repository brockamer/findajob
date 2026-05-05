"""Tests for findajob.cost_tracking."""

from __future__ import annotations

from pathlib import Path

from findajob.cost_tracking import role_model


def test_role_model_resolves_known_role(tmp_path: Path) -> None:
    """A role file with model: in frontmatter returns that model string."""
    roles_dir = tmp_path / "roles"
    roles_dir.mkdir()
    (roles_dir / "scorer.md").write_text(
        "---\nmodel: openrouter:deepseek/deepseek-v3.2\nmax_tokens: 1024\n---\n\nbody\n"
    )
    assert role_model("scorer", roles_dir=roles_dir) == "openrouter:deepseek/deepseek-v3.2"


def test_role_model_missing_file_returns_unknown(tmp_path: Path) -> None:
    """Missing role file returns 'unknown' rather than raising."""
    roles_dir = tmp_path / "roles"
    roles_dir.mkdir()
    assert role_model("nonexistent", roles_dir=roles_dir) == "unknown"


def test_role_model_no_frontmatter_returns_unknown(tmp_path: Path) -> None:
    """Role file without model: field falls back to 'unknown'."""
    roles_dir = tmp_path / "roles"
    roles_dir.mkdir()
    (roles_dir / "broken.md").write_text("# A role with no frontmatter at all.\n")
    assert role_model("broken", roles_dir=roles_dir) == "unknown"


def test_role_model_frontmatter_without_model_key_returns_unknown(tmp_path: Path) -> None:
    """Frontmatter present but no model: line falls back to 'unknown'."""
    roles_dir = tmp_path / "roles"
    roles_dir.mkdir()
    (roles_dir / "partial.md").write_text("---\nmax_tokens: 1024\n---\n\nbody\n")
    assert role_model("partial", roles_dir=roles_dir) == "unknown"
