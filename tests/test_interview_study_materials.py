"""Tests for the standalone study-guide / flashcard generators (#873).

These back the on-demand materials-page buttons: each artifact can be
(re)generated individually without re-running the whole interview-prep
pipeline. The standalone functions mirror ``generate_podcast_for_job`` —
they read existing prep artifacts as input, run a single LLM role, write a
timestamped output, and raise on empty/short output so callers decide how
to surface the failure.

The bundled auto-generate path (``_generate_study_materials``, run after
interview prep) is refactored to delegate to these functions; its
characterization test pins that both artifacts are still produced, and that
a study-guide failure no longer blocks flashcard generation (#873 decoupling).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from findajob import audit
from findajob.interview import orchestrator


def _study_guide_md() -> str:
    # Must clear the >= 200 char floor the generator enforces.
    return "# Study Guide\n\n" + ("Key theme paragraph. " * 30)


def _flashcards_json() -> str:
    return '[{"front": "Q1", "back": "A1"}, {"front": "Q2", "back": "A2"}]'


@pytest.fixture()
def prep_folder(tmp_path: Path, monkeypatch) -> str:
    monkeypatch.setattr(audit, "LOG_PATH", str(tmp_path / "events.jsonl"))
    folder = tmp_path / "Acme_Eng_2026-05-20_120000"
    folder.mkdir()
    return str(folder)


# ── generate_study_guide_for_job ─────────────────────────────────────────


def test_generate_study_guide_writes_md_and_returns_path(prep_folder):
    with (
        patch.object(orchestrator, "run_role", return_value=_study_guide_md()),
        patch.object(orchestrator, "read_file_prefix", return_value="Tester"),
        patch.object(orchestrator, "ntfy_send"),
    ):
        path = orchestrator.generate_study_guide_for_job(
            prep_folder=prep_folder,
            company="Acme",
            title="Sr Ops",
            job_id="jid",
            jd_text="JD body",
            briefing="Briefing body",
            resume="Resume body",
            cover="Cover body",
            critique="Critique body",
            interview_prep="Interview prep body",
            cached_prefix="PROFILE\n\nMASTER",
        )

    assert path is not None
    p = Path(path)
    assert p.is_file()
    assert " Study Guide - " in p.name
    assert p.suffix == ".md"
    assert p.read_text() == _study_guide_md()


def test_generate_study_guide_raises_on_short_output(prep_folder):
    with (
        patch.object(orchestrator, "run_role", return_value="too short"),
        patch.object(orchestrator, "read_file_prefix", return_value="Tester"),
        patch.object(orchestrator, "ntfy_send"),
    ):
        with pytest.raises(RuntimeError):
            orchestrator.generate_study_guide_for_job(
                prep_folder=prep_folder,
                company="Acme",
                title="Sr Ops",
                job_id="jid",
                jd_text="JD",
                briefing="B",
                resume="R",
                cover="",
                critique="",
                interview_prep="IP",
                cached_prefix="",
            )

    # No partial artifact left behind.
    assert not list(Path(prep_folder).glob("* Study Guide *.md"))


# ── generate_flashcards_for_job ──────────────────────────────────────────


def test_generate_flashcards_builds_and_returns_paths(prep_folder):
    fake_paths = {"apkg": "deck.apkg", "csv": "deck.csv", "json": "deck.json"}
    with (
        patch.object(orchestrator, "run_role", return_value=_flashcards_json()),
        patch.object(orchestrator, "build_flashcards", return_value=fake_paths) as mock_build,
        patch.object(orchestrator, "read_file_prefix", return_value="Tester"),
        patch.object(orchestrator, "ntfy_send"),
    ):
        paths = orchestrator.generate_flashcards_for_job(
            prep_folder=prep_folder,
            company="Acme",
            title="Sr Ops",
            job_id="jid",
            jd_text="JD body",
            briefing="Briefing body",
            resume="Resume body",
            interview_prep="Interview prep body",
            cached_prefix="PROFILE\n\nMASTER",
        )

    assert paths == fake_paths
    mock_build.assert_called_once()
    # base_name must carry the Flashcards marker the materials page greps for.
    _, kwargs = mock_build.call_args
    assert " Flashcards - " in kwargs["base_name"]


def test_generate_flashcards_raises_on_empty_output(prep_folder):
    with (
        patch.object(orchestrator, "run_role", return_value=""),
        patch.object(orchestrator, "build_flashcards") as mock_build,
        patch.object(orchestrator, "read_file_prefix", return_value="Tester"),
        patch.object(orchestrator, "ntfy_send"),
    ):
        with pytest.raises(RuntimeError):
            orchestrator.generate_flashcards_for_job(
                prep_folder=prep_folder,
                company="Acme",
                title="Sr Ops",
                job_id="jid",
                jd_text="JD",
                briefing="B",
                resume="R",
                interview_prep="IP",
                cached_prefix="",
            )

    mock_build.assert_not_called()


# ── _generate_study_materials (bundled auto-generate path) ────────────────


def test_bundled_path_writes_both_artifacts(prep_folder):
    """Characterization: the post-interview-prep bundled path produces both
    a study guide .md and a flashcard deck."""
    fake_paths = {"apkg": "deck.apkg", "csv": "deck.csv", "json": "deck.json"}
    with (
        patch.object(orchestrator, "run_role", side_effect=[_study_guide_md(), _flashcards_json()]),
        patch.object(orchestrator, "build_flashcards", return_value=fake_paths) as mock_build,
        patch.object(orchestrator, "read_file_prefix", return_value="Tester"),
        patch.object(orchestrator, "ntfy_send"),
    ):
        orchestrator._generate_study_materials(
            prep_folder=prep_folder,
            company="Acme",
            title="Sr Ops",
            job_id="jid",
            jd_text="JD body",
            briefing="Briefing body",
            resume="Resume body",
            cover="Cover body",
            critique="Critique body",
            interview_prep="Interview prep body",
            cached_prefix="PROFILE\n\nMASTER",
            conn=None,
        )

    assert list(Path(prep_folder).glob("* Study Guide *.md")), "study guide .md not written"
    mock_build.assert_called_once()


def test_bundled_path_flashcards_run_even_if_study_guide_fails(prep_folder):
    """#873 decoupling: a study-guide failure must not block flashcard
    generation in the bundled path (they were coupled by an early return
    before the standalone extraction)."""
    fake_paths = {"apkg": "deck.apkg", "csv": "deck.csv", "json": "deck.json"}
    with (
        # First run_role call (study guide) returns junk → fails; second
        # (flashcards) returns valid JSON.
        patch.object(orchestrator, "run_role", side_effect=["short", _flashcards_json()]),
        patch.object(orchestrator, "build_flashcards", return_value=fake_paths) as mock_build,
        patch.object(orchestrator, "read_file_prefix", return_value="Tester"),
        patch.object(orchestrator, "ntfy_send"),
    ):
        orchestrator._generate_study_materials(
            prep_folder=prep_folder,
            company="Acme",
            title="Sr Ops",
            job_id="jid",
            jd_text="JD body",
            briefing="Briefing body",
            resume="Resume body",
            cover="",
            critique="",
            interview_prep="Interview prep body",
            cached_prefix="",
            conn=None,
        )

    assert not list(Path(prep_folder).glob("* Study Guide *.md")), "study guide should have failed"
    mock_build.assert_called_once(), "flashcards must still be built despite study-guide failure"
