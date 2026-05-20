"""Size-based rotation for logs/pipeline.jsonl (#8).

The hot path (`log_event`) opens the file per call, so we can drive
rotation by hammering small writes against a monkeypatched
`_MAX_BYTES`. Each event serializes to ~80 B; the tiny threshold below
triggers a rotation every ~4 events so a short batch covers
ring-fill + eviction in one test.
"""

from __future__ import annotations

import gzip
import json
import os
import time
from pathlib import Path

import pytest

from findajob import audit


@pytest.fixture(autouse=True)
def _isolated_log_path(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(audit, "LOG_PATH", str(tmp_path / "logs" / "pipeline.jsonl"))
    return tmp_path / "logs" / "pipeline.jsonl"


@pytest.fixture()
def _tiny_threshold(monkeypatch):
    monkeypatch.setattr(audit, "_MAX_BYTES", 256)


def _write_n(n: int, **extra) -> None:
    for i in range(n):
        audit.log_event("tick", i=i, **extra)


def _read_jsonl(p: Path) -> list[dict]:
    return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]


def _read_gz_jsonl(p: Path) -> list[dict]:
    with gzip.open(p, "rt") as f:
        return [json.loads(line) for line in f if line.strip()]


def _collect_all_events(log_path: Path) -> list[dict]:
    """Aggregate events across rotated backups + current, oldest-first."""
    events: list[dict] = []
    for i in range(audit._BACKUP_COUNT, 0, -1):
        gz = Path(str(log_path) + f".{i}.gz")
        if gz.exists():
            events.extend(_read_gz_jsonl(gz))
    if log_path.exists():
        events.extend(_read_jsonl(log_path))
    return events


class TestUnderThreshold:
    def test_no_rotation_below_threshold(self, _isolated_log_path: Path) -> None:
        audit.log_event("hello", who="world")
        assert _isolated_log_path.exists()
        events = _read_jsonl(_isolated_log_path)
        assert len(events) == 1
        assert events[0]["event"] == "hello"
        assert not list(_isolated_log_path.parent.glob("*.gz"))

    def test_monkeypatched_log_path_redirects_writes(self, tmp_path: Path, monkeypatch) -> None:
        """Regression-guard: existing tests monkeypatch findajob.audit.LOG_PATH
        to redirect log_event during fixtures. log_event must read LOG_PATH
        fresh each call rather than caching it from import time."""
        custom = tmp_path / "elsewhere" / "events.jsonl"
        monkeypatch.setattr(audit, "LOG_PATH", str(custom))
        audit.log_event("redirected")
        assert custom.exists()
        assert _read_jsonl(custom)[0]["event"] == "redirected"


class TestRotation:
    def test_rotation_preserves_all_events_until_eviction(self, _isolated_log_path: Path, _tiny_threshold) -> None:
        _write_n(20)
        # At least one rotation must have happened.
        assert Path(str(_isolated_log_path) + ".1.gz").exists()
        events = _collect_all_events(_isolated_log_path)
        # 20 events ≈ 5 rotations × ~4 events per slot. The ring has
        # capacity for 6 backups so nothing should have been evicted yet.
        assert len(events) == 20
        assert [e["i"] for e in events] == list(range(20))

    def test_older_backups_hold_older_events(self, _isolated_log_path: Path, _tiny_threshold) -> None:
        _write_n(20)
        gz1 = Path(str(_isolated_log_path) + ".1.gz")
        gz2 = Path(str(_isolated_log_path) + ".2.gz")
        assert gz1.exists() and gz2.exists()
        # Higher-numbered slot = older events; lower-numbered = newer.
        older = _read_gz_jsonl(gz2)
        newer = _read_gz_jsonl(gz1)
        assert max(e["i"] for e in older) < min(e["i"] for e in newer), (
            f"slot order violated: gz2={[e['i'] for e in older]} vs gz1={[e['i'] for e in newer]}"
        )

    def test_ring_caps_at_six_and_evicts_oldest(self, _isolated_log_path: Path, _tiny_threshold) -> None:
        # 50 events ≈ 12 rotations → ring saturates and the oldest get evicted.
        _write_n(50)
        for i in range(1, 7):
            assert Path(str(_isolated_log_path) + f".{i}.gz").exists(), f"missing .{i}.gz"
        assert not Path(str(_isolated_log_path) + ".7.gz").exists()
        events = _collect_all_events(_isolated_log_path)
        # Some early events must have been evicted; the survivors are
        # contiguous from the end.
        assert len(events) < 50
        indices = [e["i"] for e in events]
        assert max(indices) == 49
        assert indices == sorted(indices)
        # Earliest surviving index is consistent with a 6-backup ring +
        # current file; the precise number depends on serialized byte
        # count per event, but it must be > 0.
        assert min(indices) > 0


class TestRetentionSweep:
    def test_90_day_sweep_deletes_aged_backups(self, _isolated_log_path: Path, _tiny_threshold) -> None:
        """Pre-create a stale backup in a slot that survives one rotation's
        shift; verify the sweep at the end of _rotate removes it even
        though the ring rule would have kept it."""
        _isolated_log_path.parent.mkdir(parents=True, exist_ok=True)
        stale_src = Path(str(_isolated_log_path) + ".3.gz")
        with gzip.open(stale_src, "wt") as f:
            f.write('{"event": "ancient"}\n')
        ancient = time.time() - (100 * 24 * 60 * 60)
        os.utime(stale_src, (ancient, ancient))
        # Trigger one rotation; .3.gz → .4.gz (mtime preserved by rename).
        # Then sweep removes any .gz older than 90 days.
        _write_n(5)
        assert not Path(str(_isolated_log_path) + ".4.gz").exists(), "90-day sweep should have removed the aged backup"
        # The fresh .1.gz from this rotation must remain.
        new_gz1 = Path(str(_isolated_log_path) + ".1.gz")
        assert new_gz1.exists()
        cutoff = time.time() - audit._RETENTION_SECONDS
        for gz in _isolated_log_path.parent.glob("*.gz"):
            assert gz.stat().st_mtime >= cutoff, f"{gz} survived sweep with stale mtime"


class TestReaderCompat:
    def test_jsonl_tail_yields_only_current_file(self, _isolated_log_path: Path, _tiny_threshold) -> None:
        from findajob.admin.jsonl_tail import tail_events

        _write_n(20)
        assert Path(str(_isolated_log_path) + ".1.gz").exists()
        # tail_events reads the live file only — by design (#355). Rotated
        # events are not surfaced. Verify it still yields a valid set of
        # events without exception.
        events = list(tail_events(_isolated_log_path))
        assert len(events) > 0
        for e in events:
            assert "i" in e
            assert e["i"] >= 0

    def test_staging_green_spans_rotation(self, _isolated_log_path: Path, _tiny_threshold) -> None:
        """staging.green._read_events must include the most recent rotated
        backup so the 26h pipeline_complete predicate survives a rotation
        that lands between green-check runs."""
        from findajob.staging import green

        _write_n(20)
        assert Path(str(_isolated_log_path) + ".1.gz").exists()
        current_only_count = len(_read_jsonl(_isolated_log_path))
        events = green._read_events(_isolated_log_path)
        # Spans .1.gz + current → strictly more events than reading
        # current alone. (Earlier slots .2.gz+ are intentionally excluded
        # — green-check only needs the last triage cycle.)
        assert len(events) > current_only_count
        # Order invariant: .1.gz events appear before current events.
        # Pick the maximum index in each batch and verify the rotated
        # slice's max is lower than the current slice's min.
        rotated_count = len(events) - current_only_count
        rotated_indices = [e["i"] for e in events[:rotated_count]]
        current_indices = [e["i"] for e in events[rotated_count:]]
        assert max(rotated_indices) < min(current_indices)
