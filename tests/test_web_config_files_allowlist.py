"""Unit tests for the /config/ editor allowlist module."""

from __future__ import annotations

from pathlib import Path

import pytest

from findajob.web.config_files import (
    EDITABLE_CATEGORIES,
    is_editable,
    list_editable,
    resolve_editable,
)

# ---- is_editable ----------------------------------------------------------


@pytest.mark.parametrize(
    "relpath",
    [
        "candidate_context/profile.md",
        "candidate_context/master_resume.md",
        "config/prefilter_rules.yaml",
        "config/in_domain_patterns.yaml",
        "config/jsearch_queries.txt",
        "config/feed_urls.txt",
        "config/company_aliases.yaml",
        "config/roles/job_scorer.md",
        "config/roles/cover_letter_writer.md",
        "config/roles/onboarding_interviewer.md",
        # #150 — tool prompts powering /tools/ tiles.
        "config/tool_prompts/profile_refresh.md",
        "config/tool_prompts/exclusion_tuning.md",
        "config/tool_prompts/cover_letter_voice.md",
    ],
)
def test_is_editable_allows_whitelisted(relpath: str) -> None:
    assert is_editable(relpath) is True


@pytest.mark.parametrize(
    "relpath",
    [
        "",
        "/",
        "config",
        "config/",
        "config/roles",
        "config/roles/",
        "config/roles/anything.txt",  # wrong extension under roles/
        "config/roles/nested/file.md",  # no subdir recursion
        "config/tool_prompts",  # bare dir, no file
        "config/tool_prompts/",
        "config/tool_prompts/anything.txt",  # wrong extension under tool_prompts/
        "config/tool_prompts/nested/file.md",  # no subdir recursion
        "config/other.yaml",  # not in flat allowlist
        "config/roles.md",  # not under roles/
        "config/tool_prompts.md",  # not under tool_prompts/
        "candidate_context/voice_samples/a.md",  # voice_samples not editable
        "data/pipeline.db",
        "secrets.env",
    ],
)
def test_is_editable_rejects_unlisted(relpath: str) -> None:
    assert is_editable(relpath) is False


@pytest.mark.parametrize(
    "relpath",
    [
        "../etc/passwd",
        "config/../secrets.env",
        "config/roles/../../etc/passwd",
        "config/roles/./job_scorer.md",  # dot components rejected
        "/etc/passwd",  # absolute path rejected
        "config/roles/job_scorer.md/..",  # trailing traversal
    ],
)
def test_is_editable_rejects_traversal(relpath: str) -> None:
    assert is_editable(relpath) is False


# ---- resolve_editable -----------------------------------------------------


def test_resolve_editable_returns_absolute_path(tmp_path: Path) -> None:
    target = tmp_path / "config" / "roles" / "job_scorer.md"
    target.parent.mkdir(parents=True)
    target.write_text("original content")

    resolved = resolve_editable("config/roles/job_scorer.md", tmp_path)

    assert resolved == target.resolve()


def test_resolve_editable_returns_none_for_unlisted(tmp_path: Path) -> None:
    assert resolve_editable("config/random.txt", tmp_path) is None


def test_resolve_editable_returns_none_for_traversal(tmp_path: Path) -> None:
    assert resolve_editable("../etc/passwd", tmp_path) is None


def test_resolve_editable_returns_path_even_if_file_missing(tmp_path: Path) -> None:
    # Allowlisted but not yet created on disk — still resolves, caller handles
    # the missing-file case (GET renders empty, POST creates).
    resolved = resolve_editable("candidate_context/profile.md", tmp_path)

    assert resolved == (tmp_path / "candidate_context" / "profile.md").resolve()


def test_resolve_editable_blocks_symlink_escape(tmp_path: Path) -> None:
    # An allowlisted path that, on disk, symlinks out of base_root must be rejected.
    outside = tmp_path.parent / "outside.md"
    outside.write_text("leaked")
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "roles").mkdir()
    (tmp_path / "config" / "roles" / "job_scorer.md").symlink_to(outside)

    assert resolve_editable("config/roles/job_scorer.md", tmp_path) is None


# ---- list_editable --------------------------------------------------------


def test_list_editable_groups_by_category(tmp_path: Path) -> None:
    (tmp_path / "candidate_context").mkdir()
    (tmp_path / "candidate_context" / "profile.md").write_text("x")
    (tmp_path / "candidate_context" / "master_resume.md").write_text("x")
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "prefilter_rules.yaml").write_text("x")
    (tmp_path / "config" / "roles").mkdir()
    (tmp_path / "config" / "roles" / "job_scorer.md").write_text("x")
    (tmp_path / "config" / "roles" / "cover_letter_writer.md").write_text("x")
    (tmp_path / "config" / "tool_prompts").mkdir()
    (tmp_path / "config" / "tool_prompts" / "profile_refresh.md").write_text("x")
    (tmp_path / "config" / "tool_prompts" / "exclusion_tuning.md").write_text("x")

    groups = list_editable(tmp_path)

    names = [g["name"] for g in groups]
    assert names == ["Candidate context", "Search config", "Role prompts", "Tool prompts"]

    candidate = next(g for g in groups if g["name"] == "Candidate context")
    candidate_paths = [f["relpath"] for f in candidate["files"]]
    assert "candidate_context/profile.md" in candidate_paths
    assert "candidate_context/master_resume.md" in candidate_paths

    roles = next(g for g in groups if g["name"] == "Role prompts")
    role_paths = sorted(f["relpath"] for f in roles["files"])
    assert role_paths == [
        "config/roles/cover_letter_writer.md",
        "config/roles/job_scorer.md",
    ]

    tools = next(g for g in groups if g["name"] == "Tool prompts")
    tool_paths = sorted(f["relpath"] for f in tools["files"])
    assert tool_paths == [
        "config/tool_prompts/exclusion_tuning.md",
        "config/tool_prompts/profile_refresh.md",
    ]


def test_list_editable_flags_missing_files(tmp_path: Path) -> None:
    # An allowlisted file that doesn't exist on disk shows up with exists=False.
    groups = list_editable(tmp_path)

    candidate = next(g for g in groups if g["name"] == "Candidate context")
    profile = next(f for f in candidate["files"] if f["relpath"] == "candidate_context/profile.md")
    assert profile["exists"] is False


def test_editable_categories_constant_shape() -> None:
    assert set(EDITABLE_CATEGORIES.keys()) == {
        "Candidate context",
        "Search config",
        "Role prompts",
        "Tool prompts",
    }
    assert "candidate_context/profile.md" in EDITABLE_CATEGORIES["Candidate context"]
    assert "config/jsearch_queries.txt" in EDITABLE_CATEGORIES["Search config"]
    assert EDITABLE_CATEGORIES["Role prompts"] == "config/roles/*.md"
    assert EDITABLE_CATEGORIES["Tool prompts"] == "config/tool_prompts/*.md"


def test_discovered_companies_md_is_editable() -> None:
    assert is_editable("candidate_context/discovered_companies.md") is True


def test_discovered_companies_json_is_NOT_editable() -> None:
    """JSON sidecar is machine-managed; only the markdown is operator-editable."""
    assert is_editable("candidate_context/discovered_companies.json") is False
