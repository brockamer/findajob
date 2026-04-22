"""Tests for _run_sync_sheet() exit-code handling in triage.py (#145).

Previously triage.py called sync_sheet.py with subprocess.run(..., check=False)
and discarded the return code, so a crashed sync was invisible from the
triage side. _run_sync_sheet() now logs triage_sync_failed on non-zero exit.
"""

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

SCRIPTS_DIR = Path(__file__).parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))


def _load_triage():
    spec = importlib.util.spec_from_file_location("triage", SCRIPTS_DIR / "triage.py")
    mod = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {"findajob.scoring": MagicMock()}):
        try:
            spec.loader.exec_module(mod)
        except Exception:
            pass
    return mod


def test_sync_failure_logs_triage_sync_failed():
    """Non-zero exit from sync_sheet.py emits a triage_sync_failed event."""
    triage = _load_triage()
    events: list[dict] = []

    def fake_log_event(event, **kwargs):
        events.append({"event": event, **kwargs})

    fake_result = MagicMock(returncode=2)
    with (
        patch.object(triage.subprocess, "run", return_value=fake_result) as mock_run,
        patch.object(triage, "log_event", fake_log_event),
    ):
        rc = triage._run_sync_sheet()

    assert mock_run.call_count == 1
    assert rc == 2
    failed = [e for e in events if e["event"] == "triage_sync_failed"]
    assert len(failed) == 1
    assert failed[0]["returncode"] == 2


def test_sync_success_emits_no_failure_event():
    """Zero exit from sync_sheet.py emits no triage_sync_failed event."""
    triage = _load_triage()
    events: list[dict] = []

    def fake_log_event(event, **kwargs):
        events.append({"event": event, **kwargs})

    fake_result = MagicMock(returncode=0)
    with (
        patch.object(triage.subprocess, "run", return_value=fake_result),
        patch.object(triage, "log_event", fake_log_event),
    ):
        rc = triage._run_sync_sheet()

    assert rc == 0
    assert not any(e["event"] == "triage_sync_failed" for e in events)
