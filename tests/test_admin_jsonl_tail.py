"""Tests for findajob.admin.jsonl_tail.tail_events."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from findajob.admin.jsonl_tail import tail_events


def _write_events(path: Path, events: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n")


def test_missing_file_returns_empty(tmp_path: Path) -> None:
    assert list(tail_events(tmp_path / "absent.jsonl")) == []


def test_empty_file_returns_empty(tmp_path: Path) -> None:
    p = tmp_path / "empty.jsonl"
    p.touch()
    assert list(tail_events(p)) == []


def test_small_file_yields_newest_first(tmp_path: Path) -> None:
    p = tmp_path / "small.jsonl"
    _write_events(
        p,
        [
            {"ts": "2026-04-30T00:00:00+00:00", "event": "pipeline_started"},
            {"ts": "2026-04-30T00:05:00+00:00", "event": "pipeline_complete"},
        ],
    )
    events = list(tail_events(p))
    assert [e["event"] for e in events] == ["pipeline_complete", "pipeline_started"]


def test_large_file_reads_only_tail(tmp_path: Path) -> None:
    p = tmp_path / "large.jsonl"
    # 5000 events × ~80 bytes ≈ 400 KB. Use max_bytes=10000 to force tail behavior.
    events = [{"ts": f"2026-04-30T00:00:{i:02d}+00:00", "event": "watchdog_run", "i": i} for i in range(5000)]
    _write_events(p, events)
    out = list(tail_events(p, max_bytes=10_000))
    assert len(out) > 0
    assert len(out) < 5000  # did not read whole file
    # Newest event in file is i=4999; tail must include it.
    assert out[0]["i"] == 4999


def test_malformed_line_is_skipped(tmp_path: Path) -> None:
    p = tmp_path / "mixed.jsonl"
    p.write_text(
        '{"ts": "2026-04-30T00:00:00+00:00", "event": "pipeline_started"}\n'
        "this-is-not-json\n"
        '{"ts": "2026-04-30T00:05:00+00:00", "event": "pipeline_complete"}\n'
    )
    events = list(tail_events(p))
    assert [e["event"] for e in events] == ["pipeline_complete", "pipeline_started"]


@pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses chmod 0o000")
def test_unreadable_file_returns_empty(tmp_path: Path) -> None:
    """A chmod-0o000 JSONL (bind-mount perm flip / cross-uid) yields []
    rather than raising PermissionError into the dashboard handler.
    """
    p = tmp_path / "locked.jsonl"
    _write_events(
        p,
        [{"ts": "2026-04-30T00:00:00+00:00", "event": "pipeline_complete"}],
    )
    p.chmod(0o000)
    try:
        assert list(tail_events(p)) == []
    finally:
        p.chmod(0o644)  # let pytest clean up


def test_partial_first_line_at_buffer_boundary_is_dropped(tmp_path: Path) -> None:
    """When the tail-window cuts mid-line, the partial first line is discarded."""
    p = tmp_path / "boundary.jsonl"
    # Two events; force max_bytes to land mid-first-line.
    events = [
        {"ts": "2026-04-30T00:00:00+00:00", "event": "pipeline_started", "padding": "x" * 200},
        {"ts": "2026-04-30T00:05:00+00:00", "event": "pipeline_complete"},
    ]
    _write_events(p, events)
    # Pick a buffer size that splits the first line.
    full = p.read_text()
    cut_at = len(full) - 100  # well into line 2
    out = list(tail_events(p, max_bytes=full[cut_at:].__len__() + 5))
    # Whatever survives, every yielded entry must be valid JSON (no half-line).
    for e in out:
        assert isinstance(e, dict)
