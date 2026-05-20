"""Unit tests for the cron_event_span context manager (#650).

Verifies that wrapping a cron's main() emits cron_started + cron_finished
events via log_event, with status=succeeded on clean exit and
status=failed on exception, AND that the exception still propagates.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from findajob.audit import cron_event_span


@pytest.fixture()
def log_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect log_event writes to a tmp_path pipeline.jsonl.

    Mirrors the existing pattern in tests/test_actions.py:91 — LOG_PATH
    is a module-level str, not Path, so str() the tmp file.
    """
    from findajob import audit

    p = tmp_path / "logs" / "pipeline.jsonl"
    p.parent.mkdir(parents=True)
    monkeypatch.setattr(audit, "LOG_PATH", str(p))
    return p


def _read_events(log_path: Path) -> list[dict]:
    if not log_path.exists():
        return []
    return [json.loads(line) for line in log_path.read_text().splitlines() if line.strip()]


def test_cron_event_span_happy_path_emits_started_and_finished_succeeded(log_path: Path) -> None:
    with cron_event_span("notify-stats"):
        pass

    events = _read_events(log_path)
    assert len(events) == 2
    assert events[0]["event"] == "cron_started"
    assert events[0]["cron"] == "notify-stats"
    assert events[1]["event"] == "cron_finished"
    assert events[1]["cron"] == "notify-stats"
    assert events[1]["status"] == "succeeded"


def test_cron_event_span_exception_path_emits_finished_failed_and_reraises(log_path: Path) -> None:
    with pytest.raises(RuntimeError, match="boom"):
        with cron_event_span("triage"):
            raise RuntimeError("boom")

    events = _read_events(log_path)
    assert len(events) == 2
    assert events[0]["event"] == "cron_started"
    assert events[1]["event"] == "cron_finished"
    assert events[1]["status"] == "failed"
