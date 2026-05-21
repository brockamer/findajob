"""Unit tests for the cron tile registry + concurrency helpers (#650)."""

from __future__ import annotations

import dataclasses
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from findajob.web.cron_registry import (
    CRON_TILES,
    CRON_TILES_BY_SLUG,
    is_currently_running,
    last_run_at,
)


def test_registry_has_seven_entries() -> None:
    """Spec §2.3 — registry is exactly the 7 curated entries."""
    assert len(CRON_TILES) == 7
    slugs = {t.slug for t in CRON_TILES}
    assert slugs == {
        "triage",
        "detect-rejections",
        "discover",
        "notify-health",
        "notify-stats",
        "watchdog",
        "notify-scoreboard",
    }


def test_notify_scoreboard_is_disabled() -> None:
    """Spec §2.3 — notify-scoreboard mirrors `enabled: false` in yaml."""
    assert CRON_TILES_BY_SLUG["notify-scoreboard"].enabled is False


def test_spend_gated_tiles_are_triage_and_discover_only() -> None:
    """Spec §2.3 — only the two LLM-spending crons run through check_launch_gate."""
    gated = {t.slug for t in CRON_TILES if t.gated_by_spend_ceiling}
    assert gated == {"triage", "discover"}


def test_confirm_required_tiles_match_spend_gated() -> None:
    """v1: confirm-required iff cost-bearing — keeps the UX coupling explicit."""
    confirm = {t.slug for t in CRON_TILES if t.confirm_required}
    assert confirm == {"triage", "discover"}


def test_max_runtime_minutes_match_spec_table() -> None:
    """Spec §2.3 table — mirrors script-side timeouts."""
    runtimes = {t.slug: t.max_runtime_minutes for t in CRON_TILES}
    assert runtimes == {
        "triage": 120,
        "detect-rejections": 10,
        "discover": 10,
        "notify-health": 2,
        "notify-stats": 2,
        "watchdog": 15,
        "notify-scoreboard": 2,
    }


def test_crontile_is_frozen() -> None:
    """Frozen dataclass — mutation forbidden."""
    tile = CRON_TILES[0]
    with pytest.raises(dataclasses.FrozenInstanceError):
        tile.slug = "mutated"  # type: ignore[misc]


def test_notify_tiles_use_args_field_not_script_path_concatenation() -> None:
    """The subcommand belongs in `args`, not concatenated into `script_path`,
    so the dispatcher doesn't have to .split() an ad-hoc combined string.
    """
    assert CRON_TILES_BY_SLUG["notify-health"].script_path == "scripts/notify.py"
    assert CRON_TILES_BY_SLUG["notify-health"].args == ("health-check",)
    assert CRON_TILES_BY_SLUG["notify-stats"].script_path == "scripts/notify.py"
    assert CRON_TILES_BY_SLUG["notify-stats"].args == ("daily-stats",)
    assert CRON_TILES_BY_SLUG["notify-scoreboard"].script_path == "scripts/notify.py"
    assert CRON_TILES_BY_SLUG["notify-scoreboard"].args == ("scoreboard",)


def test_non_notify_tiles_have_empty_args() -> None:
    for slug in ("triage", "detect-rejections", "discover", "watchdog"):
        assert CRON_TILES_BY_SLUG[slug].args == ()


def test_cron_tiles_by_slug_mirrors_list_no_duplicates() -> None:
    """No duplicate slugs in CRON_TILES; the by-slug dict is 1:1."""
    assert len(CRON_TILES_BY_SLUG) == len(CRON_TILES)


def _write_jsonl(base_root: Path, events: list[dict]) -> None:
    log_dir = base_root / "logs"
    log_dir.mkdir(exist_ok=True)
    with (log_dir / "pipeline.jsonl").open("w") as f:
        for ev in events:
            f.write(json.dumps(ev) + "\n")


def _ts(minutes_ago: int) -> str:
    """Mirror what `findajob.audit.log_event` writes — `datetime.now(UTC).isoformat()`."""
    return (datetime.now(UTC) - timedelta(minutes=minutes_ago)).isoformat()


def test_is_currently_running_false_when_log_missing(tmp_path: Path) -> None:
    assert is_currently_running("triage", tmp_path) is False


def test_is_currently_running_false_when_no_start_event(tmp_path: Path) -> None:
    _write_jsonl(tmp_path, [{"ts": _ts(5), "event": "pipeline_complete"}])
    assert is_currently_running("triage", tmp_path) is False


def test_is_currently_running_true_for_unmatched_recent_start(tmp_path: Path) -> None:
    _write_jsonl(tmp_path, [{"ts": _ts(5), "event": "cron_started", "cron": "triage"}])
    assert is_currently_running("triage", tmp_path) is True


def test_is_currently_running_false_after_matching_finished(tmp_path: Path) -> None:
    _write_jsonl(
        tmp_path,
        [
            {"ts": _ts(10), "event": "cron_started", "cron": "triage"},
            {"ts": _ts(5), "event": "cron_finished", "cron": "triage", "status": "succeeded"},
        ],
    )
    assert is_currently_running("triage", tmp_path) is False


def test_is_currently_running_false_for_leaked_start_past_max_runtime(tmp_path: Path) -> None:
    """triage max_runtime_minutes=120 — a cron_started 200 minutes ago is leaked."""
    _write_jsonl(tmp_path, [{"ts": _ts(200), "event": "cron_started", "cron": "triage"}])
    assert is_currently_running("triage", tmp_path) is False


def test_is_currently_running_isolates_by_slug(tmp_path: Path) -> None:
    """A running 'triage' doesn't make 'discover' look running."""
    _write_jsonl(tmp_path, [{"ts": _ts(5), "event": "cron_started", "cron": "triage"}])
    assert is_currently_running("triage", tmp_path) is True
    assert is_currently_running("discover", tmp_path) is False


def test_last_run_at_returns_most_recent_finished(tmp_path: Path) -> None:
    older_ts = _ts(30)
    newer_ts = _ts(10)
    _write_jsonl(
        tmp_path,
        [
            {"ts": older_ts, "event": "cron_finished", "cron": "triage", "status": "succeeded"},
            {"ts": newer_ts, "event": "cron_finished", "cron": "triage", "status": "succeeded"},
        ],
    )
    assert last_run_at("triage", tmp_path) == newer_ts


def test_last_run_at_none_when_never_run(tmp_path: Path) -> None:
    assert last_run_at("triage", tmp_path) is None
