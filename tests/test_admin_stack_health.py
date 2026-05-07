"""Tests for findajob.admin.stack_health.gather."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from findajob.admin.stack_discovery import StackPath
from findajob.admin.stack_health import StackHealth, gather
from tests.conftest_admin import build_pipeline_db, build_pipeline_jsonl, write_corrupt_db

# Fixed reference time for deterministic tests.
NOW = datetime(2026, 4, 30, 12, 0, 0, tzinfo=UTC)


def _stackpath(root: Path, handle: str = "alice") -> StackPath:
    state = root / f"findajob-{handle}" / "state"
    return StackPath(
        handle=handle,
        root=root / f"findajob-{handle}",
        db_path=state / "data" / "pipeline.db",
        jsonl_path=state / "logs" / "pipeline.jsonl",
    )


def test_missing_db_and_missing_jsonl(tmp_path: Path) -> None:
    sp = _stackpath(tmp_path)
    h = gather(sp, now=NOW)
    assert h.handle == "alice"
    assert h.db_missing is True
    assert h.jsonl_missing is True
    assert h.error is None
    assert h.last_triage_complete is None
    assert h.stage_counts == {}
    assert h.stuck_prep_count == 0
    assert h.freshness == "unknown"


def test_corrupt_db_sets_error_field(tmp_path: Path) -> None:
    sp = _stackpath(tmp_path)
    write_corrupt_db(sp.db_path)
    build_pipeline_jsonl(sp.jsonl_path, [])
    h = gather(sp, now=NOW)
    assert h.error is not None
    assert "database" in h.error.lower() or "sqlite" in h.error.lower() or "file is not" in h.error.lower()


def test_stage_counts_aggregate(tmp_path: Path) -> None:
    sp = _stackpath(tmp_path)
    build_pipeline_db(
        sp.db_path,
        rows=[
            {"id": "a", "stage": "scored"},
            {"id": "b", "stage": "scored"},
            {"id": "c", "stage": "manual_review"},
            {"id": "d", "stage": "applied"},
        ],
    )
    build_pipeline_jsonl(sp.jsonl_path, [])
    h = gather(sp, now=NOW)
    assert h.stage_counts == {"scored": 2, "manual_review": 1, "applied": 1}


def test_stuck_prep_counts_only_over_60min(tmp_path: Path) -> None:
    sp = _stackpath(tmp_path)
    fresh = (NOW - timedelta(minutes=30)).isoformat()
    stuck1 = (NOW - timedelta(minutes=61)).isoformat()
    stuck2 = (NOW - timedelta(hours=3)).isoformat()
    build_pipeline_db(
        sp.db_path,
        rows=[
            {"id": "a", "stage": "prep_in_progress", "prep_started_at": fresh},
            {"id": "b", "stage": "prep_in_progress", "prep_started_at": stuck1},
            {"id": "c", "stage": "prep_in_progress", "prep_started_at": stuck2},
            {"id": "d", "stage": "scored"},  # not prep_in_progress, ignored
        ],
    )
    build_pipeline_jsonl(sp.jsonl_path, [])
    h = gather(sp, now=NOW)
    assert h.stuck_prep_count == 2


def test_pipeline_complete_event_drives_freshness(tmp_path: Path) -> None:
    sp = _stackpath(tmp_path)
    build_pipeline_db(sp.db_path)
    fresh = (NOW - timedelta(hours=3)).isoformat()
    build_pipeline_jsonl(
        sp.jsonl_path,
        [
            {"ts": fresh, "event": "pipeline_started"},
            {"ts": fresh, "event": "pipeline_complete"},
        ],
    )
    h = gather(sp, now=NOW)
    assert h.last_triage_complete is not None
    assert h.freshness == "fresh"


def test_freshness_buckets(tmp_path: Path) -> None:
    cases = [
        (timedelta(hours=10), "fresh"),
        (timedelta(hours=23, minutes=59), "fresh"),
        (timedelta(hours=24), "late"),
        (timedelta(hours=30), "late"),
        (timedelta(hours=36), "stale"),
        (timedelta(days=3), "stale"),
    ]
    for delta, expected in cases:
        sp_root = tmp_path / f"case-{int(delta.total_seconds())}"
        sp_root.mkdir()
        sp = _stackpath(sp_root, handle="t")
        build_pipeline_db(sp.db_path)
        ts = (NOW - delta).isoformat()
        build_pipeline_jsonl(
            sp.jsonl_path,
            [{"ts": ts, "event": "pipeline_complete"}],
        )
        h = gather(sp, now=NOW)
        assert h.freshness == expected, f"{delta} → expected {expected}, got {h.freshness}"


def test_24h_event_counts(tmp_path: Path) -> None:
    sp = _stackpath(tmp_path)
    build_pipeline_db(sp.db_path)
    in_window = (NOW - timedelta(hours=10)).isoformat()
    in_window_2 = (NOW - timedelta(hours=20)).isoformat()
    out_of_window = (NOW - timedelta(hours=30)).isoformat()
    build_pipeline_jsonl(
        sp.jsonl_path,
        [
            {"ts": in_window, "event": "pipeline_complete"},
            {"ts": in_window_2, "event": "pipeline_complete"},
            {"ts": out_of_window, "event": "pipeline_complete"},
            {"ts": in_window, "event": "pipeline_terminated"},
        ],
    )
    h = gather(sp, now=NOW)
    assert h.triage_success_24h == 2
    assert h.triage_failure_24h == 1


def test_last_failure_timestamps(tmp_path: Path) -> None:
    sp = _stackpath(tmp_path)
    build_pipeline_db(sp.db_path)
    build_pipeline_jsonl(
        sp.jsonl_path,
        [
            {"ts": "2026-04-30T01:00:00+00:00", "event": "discovery_failed"},
            {"ts": "2026-04-29T22:00:00+00:00", "event": "prep_failed_reset"},
        ],
    )
    h = gather(sp, now=NOW)
    assert h.last_discovery_failed == datetime(2026, 4, 30, 1, 0, tzinfo=UTC)
    assert h.last_prep_failed == datetime(2026, 4, 29, 22, 0, tzinfo=UTC)


def test_returns_stackhealth_dataclass(tmp_path: Path) -> None:
    sp = _stackpath(tmp_path)
    h = gather(sp, now=NOW)
    assert isinstance(h, StackHealth)


def test_naive_timestamp_is_coerced_to_utc(tmp_path: Path) -> None:
    """A naïve ISO timestamp (older log file or hand-edited entry) must
    not crash the dashboard with a TypeError on comparison against the
    tz-aware cutoff. Coerced to UTC and counted normally.
    """
    sp = _stackpath(tmp_path)
    build_pipeline_db(sp.db_path)
    naive = (NOW.replace(tzinfo=None) - timedelta(hours=10)).isoformat()
    build_pipeline_jsonl(
        sp.jsonl_path,
        [{"ts": naive, "event": "pipeline_complete"}],
    )
    h = gather(sp, now=NOW)
    assert h.last_triage_complete is not None
    assert h.triage_success_24h == 1
    assert h.freshness == "fresh"


def test_connect_uri_includes_immutable_flag(tmp_path: Path) -> None:
    """gather() MUST connect with `immutable=1` in the URI so cross-uid
    bind-mount reads (operator container reading a tester DB owned by a
    different host uid) don't fail on the WAL/shm sidecars whose perms
    `mode=ro` alone doesn't relax.

    Asserted via mock because tmp DBs are owned by the test process,
    so a regression that drops `immutable=1` wouldn't surface in the
    other tests in this file.
    """
    sp = _stackpath(tmp_path)
    build_pipeline_db(sp.db_path)
    build_pipeline_jsonl(sp.jsonl_path, [])

    with patch("findajob.admin.stack_health.sqlite3.connect", wraps=__import__("sqlite3").connect) as mock_connect:
        gather(sp, now=NOW)

    assert mock_connect.called, "gather() did not call sqlite3.connect"
    uri = mock_connect.call_args.args[0]
    assert "immutable=1" in uri, f"sqlite3.connect URI lost immutable=1 flag: {uri!r}"
    assert "mode=ro" in uri, f"sqlite3.connect URI lost mode=ro flag: {uri!r}"
    assert mock_connect.call_args.kwargs.get("uri") is True


def test_24h_window_excludes_exact_24h_boundary(tmp_path: Path) -> None:
    """24h-window count uses strict `>`, matching `_freshness`'s strict
    `<` (where exactly 24h ago is "late", not "fresh"). An event at
    exactly the cutoff is out of the window.
    """
    sp = _stackpath(tmp_path)
    build_pipeline_db(sp.db_path)
    boundary = (NOW - timedelta(hours=24)).isoformat()
    just_inside = (NOW - timedelta(hours=23, minutes=59)).isoformat()
    build_pipeline_jsonl(
        sp.jsonl_path,
        [
            {"ts": boundary, "event": "pipeline_complete"},
            {"ts": just_inside, "event": "pipeline_complete"},
        ],
    )
    h = gather(sp, now=NOW)
    assert h.triage_success_24h == 1


def test_only_pipeline_terminated_leaves_freshness_unknown(tmp_path: Path) -> None:
    """A stack that has started runs but never completed one (cron firing
    but every triage crashing) must surface freshness="unknown" — not
    "fresh" off the start event, not "stale" off some default. The
    completion timestamp drives freshness; failures alone do not.
    """
    sp = _stackpath(tmp_path)
    build_pipeline_db(sp.db_path)
    recent = (NOW - timedelta(hours=2)).isoformat()
    older = (NOW - timedelta(hours=14)).isoformat()
    build_pipeline_jsonl(
        sp.jsonl_path,
        [
            {"ts": older, "event": "pipeline_started"},
            {"ts": older, "event": "pipeline_terminated"},
            {"ts": recent, "event": "pipeline_started"},
            {"ts": recent, "event": "pipeline_terminated"},
        ],
    )
    h = gather(sp, now=NOW)
    assert h.last_triage_complete is None
    assert h.last_triage_failed is not None
    assert h.freshness == "unknown"
    assert h.triage_success_24h == 0
    assert h.triage_failure_24h == 2
