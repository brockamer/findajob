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


def test_triage_completed_recent_passes(tmp_path: Path) -> None:
    log = tmp_path / "pipeline.jsonl"
    _write_jsonl(log, [{"ts": _ts(2), "event": "triage_completed"}])
    assert green._predicate_triage_recent(log, max_age_hours=26) is True


def test_triage_completed_too_old_fails(tmp_path: Path) -> None:
    log = tmp_path / "pipeline.jsonl"
    _write_jsonl(log, [{"ts": _ts(48), "event": "triage_completed"}])
    assert green._predicate_triage_recent(log, max_age_hours=26) is False


def test_no_errors_during_triage_passes(tmp_path: Path) -> None:
    log = tmp_path / "pipeline.jsonl"
    _write_jsonl(
        log,
        [
            {"ts": _ts(3), "event": "triage_started"},
            {"ts": _ts(2.5), "event": "fetched_jobs", "level": "INFO"},
            {"ts": _ts(2), "event": "triage_completed"},
        ],
    )
    assert green._predicate_no_errors_during_last_triage(log) is True


def test_errors_during_triage_fails(tmp_path: Path) -> None:
    log = tmp_path / "pipeline.jsonl"
    _write_jsonl(
        log,
        [
            {"ts": _ts(3), "event": "triage_started"},
            {"ts": _ts(2.5), "event": "adapter_failure", "level": "ERROR"},
            {"ts": _ts(2), "event": "triage_completed"},
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
            {"ts": _ts(3), "event": "triage_started"},
            {"ts": _ts(2), "event": "triage_completed"},
        ],
    )
    sentinel = tmp_path / ".staging_clicker_last_status"
    sentinel.write_text(json.dumps({"exit_code": 0, "mode": "prep", "timestamp": _ts(1)}))
    monkeypatch.setattr(green, "_predicate_verify_auth_zero", lambda: True)
    rc = green.main(["--log", str(log), "--sentinel", str(sentinel)])
    assert rc == 0


def test_main_any_fail_returns_nonzero(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    log = tmp_path / "pipeline.jsonl"
    _write_jsonl(log, [{"ts": _ts(48), "event": "triage_completed"}])  # too old
    sentinel = tmp_path / ".staging_clicker_last_status"
    sentinel.write_text(json.dumps({"exit_code": 0, "mode": "prep", "timestamp": _ts(1)}))
    monkeypatch.setattr(green, "_predicate_verify_auth_zero", lambda: True)
    rc = green.main(["--log", str(log), "--sentinel", str(sentinel)])
    assert rc != 0
