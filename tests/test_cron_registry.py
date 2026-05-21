"""Unit tests for the cron tile registry + concurrency helpers (#650)."""

from __future__ import annotations

import dataclasses

import pytest

from findajob.web.cron_registry import (
    CRON_TILES,
    CRON_TILES_BY_SLUG,
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
