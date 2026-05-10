"""Staging green-check predicate tests (#565)."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

from findajob.staging import green


def _ts(offset_hours: float) -> str:
    return (dt.datetime.now(dt.UTC) - dt.timedelta(hours=offset_hours)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_jsonl(path: Path, events: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n")


def test_pipeline_complete_recent_passes(tmp_path: Path) -> None:
    log = tmp_path / "pipeline.jsonl"
    _write_jsonl(log, [{"ts": _ts(2), "event": "pipeline_complete"}])
    assert green._predicate_triage_recent(log, max_age_hours=26) is True


def test_pipeline_complete_too_old_fails(tmp_path: Path) -> None:
    log = tmp_path / "pipeline.jsonl"
    _write_jsonl(log, [{"ts": _ts(48), "event": "pipeline_complete"}])
    assert green._predicate_triage_recent(log, max_age_hours=26) is False


def test_no_errors_during_triage_passes(tmp_path: Path) -> None:
    log = tmp_path / "pipeline.jsonl"
    _write_jsonl(
        log,
        [
            {"ts": _ts(3), "event": "pipeline_started"},
            {"ts": _ts(2.5), "event": "fetched_jobs", "level": "INFO"},
            {"ts": _ts(2), "event": "pipeline_complete"},
        ],
    )
    assert green._predicate_no_errors_during_last_triage(log) is True


def test_errors_during_triage_fails(tmp_path: Path) -> None:
    log = tmp_path / "pipeline.jsonl"
    _write_jsonl(
        log,
        [
            {"ts": _ts(3), "event": "pipeline_started"},
            {"ts": _ts(2.5), "event": "adapter_failure", "level": "ERROR"},
            {"ts": _ts(2), "event": "pipeline_complete"},
        ],
    )
    assert green._predicate_no_errors_during_last_triage(log) is False


def test_clicker_sentinel_zero_passes(tmp_path: Path) -> None:
    sentinel = tmp_path / ".staging_clicker_last_status"
    sentinel.write_text(json.dumps({"exit_code": 0, "mode": "prep", "timestamp": _ts(1)}))
    assert green._predicate_clicker_last_zero(sentinel) is True


def test_clicker_sentinel_nonzero_fails(tmp_path: Path) -> None:
    sentinel = tmp_path / ".staging_clicker_last_status"
    sentinel.write_text(json.dumps({"exit_code": 1, "mode": "prep", "timestamp": _ts(1)}))
    assert green._predicate_clicker_last_zero(sentinel) is False


def test_clicker_sentinel_missing_fails(tmp_path: Path) -> None:
    sentinel = tmp_path / ".staging_clicker_last_status"
    assert green._predicate_clicker_last_zero(sentinel) is False


def test_main_all_pass_returns_zero(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    log = tmp_path / "pipeline.jsonl"
    _write_jsonl(
        log,
        [
            {"ts": _ts(3), "event": "pipeline_started"},
            {"ts": _ts(2), "event": "pipeline_complete"},
        ],
    )
    sentinel = tmp_path / ".staging_clicker_last_status"
    sentinel.write_text(json.dumps({"exit_code": 0, "mode": "prep", "timestamp": _ts(1)}))
    monkeypatch.setattr(green, "_predicate_verify_auth_zero", lambda: True)
    rc = green.main(["--log", str(log), "--sentinel", str(sentinel)])
    assert rc == 0


def test_main_any_fail_returns_nonzero(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    log = tmp_path / "pipeline.jsonl"
    _write_jsonl(log, [{"ts": _ts(48), "event": "pipeline_complete"}])  # too old
    sentinel = tmp_path / ".staging_clicker_last_status"
    sentinel.write_text(json.dumps({"exit_code": 0, "mode": "prep", "timestamp": _ts(1)}))
    monkeypatch.setattr(green, "_predicate_verify_auth_zero", lambda: True)
    rc = green.main(["--log", str(log), "--sentinel", str(sentinel)])
    assert rc != 0


def test_predicates_match_real_audit_log_event_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression for #611 — predicates must match what `findajob.audit.log_event`
    actually emits (`pipeline_started` / `pipeline_complete`), not synthetic event
    names invented in this test file. Catches future event-name drift between
    triage.py emission and green-check expectation.
    """
    from findajob import audit

    log_path = tmp_path / "pipeline.jsonl"
    monkeypatch.setattr(audit, "LOG_PATH", str(log_path))

    audit.log_event("pipeline_started")
    audit.log_event("scoring_started", total=100, workers=6)
    audit.log_event("scoring_complete", total=100, scored=100, errors=0)
    audit.log_event("pipeline_complete", new=100, dupes=0, scored=100, noise_skipped=0)

    assert green._predicate_triage_recent(log_path, max_age_hours=26) is True
    assert green._predicate_no_errors_during_last_triage(log_path) is True
