"""Tests for #371 — graceful no-op for cron-fired scripts when the stack
hasn't completed onboarding.

Was: triage.py crashed on missing profile.md (`pipeline_crash`) and
discover_companies failed similarly. Each cron tick on a deployed but
never-onboarded tester stack emitted noise into pipeline.jsonl.

Now: each script checks the onboarding sentinel and emits a structured
`*_skipped` event before returning 0.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

SCRIPTS_DIR = Path(__file__).parent.parent / "scripts"


def _load_script(name: str):
    """Load a script module by file path. Mock heavy deps that the import
    chain pulls in but the guard tests don't exercise."""
    spec = importlib.util.spec_from_file_location(name, SCRIPTS_DIR / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(SCRIPTS_DIR))
    try:
        with patch.dict(sys.modules, {"findajob.scoring": MagicMock()}):
            spec.loader.exec_module(mod)
    finally:
        sys.path.remove(str(SCRIPTS_DIR))
    return mod


def test_triage_skipped_when_not_onboarded(tmp_path: Path, monkeypatch):
    """Cron fires triage.py on a tester stack with no profile.md/sentinel —
    must emit `triage_skipped` and return cleanly instead of raising
    FileNotFoundError on PROFILE_PATH."""
    triage = _load_script("triage")
    events: list[dict] = []
    monkeypatch.setattr(triage, "BASE", str(tmp_path))
    monkeypatch.setattr(triage, "log_event", lambda event, **kw: events.append({"event": event, **kw}))

    triage.main()

    skipped = [e for e in events if e["event"] == "triage_skipped"]
    assert len(skipped) == 1
    assert skipped[0]["reason"] == "not_onboarded"
    assert not any(e["event"] == "pipeline_started" for e in events)


def test_triage_runs_when_onboarded(tmp_path: Path, monkeypatch):
    """Sentinel present → guard passes through; the rest of main() should
    run (we mock far enough to confirm the guard doesn't short-circuit)."""
    (tmp_path / "data").mkdir(parents=True)
    (tmp_path / "data" / ".onboarding-complete").write_text("2026-05-01T00:00:00Z\n")

    triage = _load_script("triage")
    events: list[dict] = []
    monkeypatch.setattr(triage, "BASE", str(tmp_path))
    monkeypatch.setattr(triage, "log_event", lambda event, **kw: events.append({"event": event, **kw}))
    # Stop main() right after the guard by exploding on the first DB op.
    monkeypatch.setattr(triage.shutil, "copy2", MagicMock())
    monkeypatch.setattr(triage.sqlite3, "connect", MagicMock(side_effect=RuntimeError("stop here")))

    try:
        triage.main()
    except RuntimeError as e:
        assert "stop here" in str(e)

    assert any(e["event"] == "pipeline_started" for e in events)
    assert not any(e["event"] == "triage_skipped" for e in events)


def test_discover_companies_skipped_when_not_onboarded(tmp_path: Path, monkeypatch, capsys):
    """The weekly discoverer cron must not exit 1 on never-onboarded
    stacks — the failed-cron noise was misleading."""
    discover = _load_script("discover_companies")
    events: list[dict] = []
    monkeypatch.setattr(discover, "BASE", str(tmp_path))
    monkeypatch.setattr(discover, "log_event", lambda event, **kw: events.append({"event": event, **kw}))
    monkeypatch.setattr(sys, "argv", ["discover_companies.py"])

    rc = discover.main()

    assert rc == 0
    skipped = [e for e in events if e["event"] == "discovery_skipped"]
    assert len(skipped) == 1
    assert skipped[0]["reason"] == "not_onboarded"
