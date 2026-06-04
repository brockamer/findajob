"""Tests for slug-level ATS feed-URL 404 detection in health_check (#983).

Distinct from the source-level dead-feed check (#637, test_health_check_dead_feeds):
that one fires when an *entire* adapter (all of Greenhouse) stops producing jobs.
This one fires when a *single* feed-URL slug 404s while the rest of the source is
healthy — e.g. greenhouse/openai is dead but Greenhouse overall still returns
thousands of jobs from its other slugs, so the source-level check stays silent.

The direct-fetcher adapters (Greenhouse / Lever / Ashby) emit a
``<ats>_fetch_skip`` event with ``slug`` + ``status`` on any non-200, then
continue. We read those breadcrumbs back out of pipeline.jsonl at health-check
time and surface the persistent 404s.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from findajob.notifications import ntfy
from findajob.notifications.health_check import _dead_slug_warning, _detect_dead_slugs


def _skip(ats: str, slug: str, status: object) -> dict:
    """Build a synthetic ``<ats>_fetch_skip`` event as the adapters emit it."""
    return {"event": f"{ats}_fetch_skip", "slug": slug, "status": status}


class TestDetectDeadSlugs:
    def test_no_skips_returns_empty(self):
        """A clean run with no fetch_skip events → no dead slugs."""
        events = [
            {"event": "jobs_fetched", "adapters": {"greenhouse": 4872}},
            {"event": "pipeline_complete"},
        ]
        assert _detect_dead_slugs(events) == []

    def test_single_404_slug_is_flagged(self):
        """One Greenhouse slug 404'd → reported as ats/slug."""
        events = [_skip("greenhouse", "openai", 404)]
        assert _detect_dead_slugs(events) == ["greenhouse/openai"]

    def test_multiple_ats_slugs_collected_and_sorted(self):
        """Dead slugs across all three ATS sources, returned sorted for stable output."""
        events = [
            _skip("lever", "huggingface", 404),
            _skip("greenhouse", "openai", 404),
            _skip("ashby", "replicate", 404),
        ]
        assert _detect_dead_slugs(events) == [
            "ashby/replicate",
            "greenhouse/openai",
            "lever/huggingface",
        ]

    def test_dedup_same_slug_across_runs_counts_once(self):
        """A slug failing in several runs within the window collapses to one entry."""
        events = [
            _skip("greenhouse", "openai", 404),
            {"event": "jobs_fetched", "adapters": {"greenhouse": 4872}},
            _skip("greenhouse", "openai", 404),
            _skip("greenhouse", "openai", 404),
        ]
        assert _detect_dead_slugs(events) == ["greenhouse/openai"]

    def test_distinct_slugs_same_ats_both_kept(self):
        """Two different slugs under one ATS are distinct dead feeds."""
        events = [
            _skip("greenhouse", "openai", 404),
            _skip("greenhouse", "anthropic", 404),
        ]
        assert _detect_dead_slugs(events) == [
            "greenhouse/anthropic",
            "greenhouse/openai",
        ]

    def test_non_404_status_ignored(self):
        """A 500 / 429 / 403 skip is transient or rate-limit, not a stale slug → ignored."""
        events = [
            _skip("greenhouse", "openai", 500),
            _skip("ashby", "replicate", 429),
            _skip("lever", "stripe", 403),
        ]
        assert _detect_dead_slugs(events) == []

    def test_non_int_status_ignored(self):
        """Lever's ``status='unexpected_format'`` is a parse failure, not a 404 → ignored."""
        events = [_skip("lever", "huggingface", "unexpected_format")]
        assert _detect_dead_slugs(events) == []

    def test_slugless_fetch_skip_ignored(self):
        """API sources (remote_ok/remotive/jobicy) emit *_fetch_skip without a slug.

        These are not ATS feed-URL slugs — they must not appear in the warning,
        and a missing ``slug`` key must not raise.
        """
        events = [
            {"event": "remote_ok_fetch_skip", "status": 404},
            {"event": "himalayas_fetch_skip", "offset": 100, "status": 404},
            {"event": "algora_fetch_skip", "org": "acme", "status": 404},
        ]
        assert _detect_dead_slugs(events) == []

    def test_real_404_kept_alongside_slugless_and_non_404(self):
        """End-to-end mix: only the slug-bearing 404 survives the filters."""
        events = [
            _skip("greenhouse", "openai", 404),  # kept
            _skip("ashby", "replicate", 500),  # non-404, dropped
            {"event": "remote_ok_fetch_skip", "status": 404},  # slugless, dropped
            _skip("lever", "huggingface", "unexpected_format"),  # non-int, dropped
            {"event": "jobs_fetched", "adapters": {"greenhouse": 4872}},  # unrelated
        ]
        assert _detect_dead_slugs(events) == ["greenhouse/openai"]

    def test_blank_slug_ignored(self):
        """Defensive: an empty-string slug is not a real feed and must not be reported."""
        events = [_skip("greenhouse", "", 404)]
        assert _detect_dead_slugs(events) == []

    def test_non_string_event_does_not_crash(self):
        """A corrupt pipeline.jsonl line with a non-string ``event`` must not crash.

        ``cmd_health_check`` runs the whole daily report through this helper with
        no try/except, so a single malformed line raising AttributeError would
        take down every other check (triage-ran, watchdog, queue health) too.
        """
        events = [
            {"event": None, "slug": "x", "status": 404},
            {"event": 123, "slug": "y", "status": 404},
            {"event": ["greenhouse_fetch_skip"], "slug": "z", "status": 404},
        ]
        assert _detect_dead_slugs(events) == []

    def test_missing_event_key_ignored(self):
        """An event dict with no ``event`` key is skipped, not matched."""
        assert _detect_dead_slugs([{"slug": "openai", "status": 404}]) == []

    def test_bare_fetch_skip_suffix_yields_no_empty_ats(self):
        """An event literally named ``_fetch_skip`` must not produce a ``/slug`` entry."""
        events = [{"event": "_fetch_skip", "slug": "openai", "status": 404}]
        assert _detect_dead_slugs(events) == []


class TestDeadSlugsFromPipelineJsonl:
    """AC1/AC4: read a synthetic pipeline.jsonl back through recent_log_events.

    Exercises the real file-read + 25h cutoff path the production check depends
    on, not just the pure detector — a stale (>25h) 404 must be excluded.
    """

    def test_detects_in_window_404s_and_excludes_stale(self, tmp_path, monkeypatch):
        now = datetime.now(UTC)
        recent = (now - timedelta(hours=2)).isoformat()
        stale = (now - timedelta(hours=30)).isoformat()  # outside the 25h window

        lines = [
            {"ts": recent, "event": "greenhouse_fetch_skip", "slug": "openai", "status": 404},
            {"ts": recent, "event": "ashby_fetch_skip", "slug": "replicate", "status": 500},  # non-404
            {"ts": recent, "event": "remote_ok_fetch_skip", "status": 404},  # slugless
            {"ts": recent, "event": "jobs_fetched", "adapters": {"greenhouse": 4872}},
            {"ts": stale, "event": "lever_fetch_skip", "slug": "huggingface", "status": 404},  # too old
        ]
        log_path = tmp_path / "pipeline.jsonl"
        log_path.write_text("\n".join(json.dumps(line) for line in lines) + "\n")
        monkeypatch.setattr(ntfy, "LOG_PATH", str(log_path))

        events = ntfy.recent_log_events(hours=25)
        assert _detect_dead_slugs(events) == ["greenhouse/openai"]

    def test_missing_log_file_is_clean(self, tmp_path, monkeypatch):
        """No pipeline.jsonl yet (fresh install) → no events, no dead slugs, no raise."""
        monkeypatch.setattr(ntfy, "LOG_PATH", str(tmp_path / "does_not_exist.jsonl"))
        assert _detect_dead_slugs(ntfy.recent_log_events(hours=25)) == []


class TestDeadSlugWarning:
    """The operator-facing WARN line — capped to the first ~5 like the other checks."""

    def test_empty_returns_none(self):
        """No dead slugs → no WARN line appended."""
        assert _dead_slug_warning([]) is None

    def test_single_slug_line(self):
        assert _dead_slug_warning(["greenhouse/openai"]) == "WARN: 1 feed URL(s) 404'd last triage: greenhouse/openai"

    def test_lists_all_when_five_or_fewer(self):
        slugs = ["ashby/replicate", "greenhouse/openai", "lever/huggingface"]
        assert _dead_slug_warning(slugs) == (
            "WARN: 3 feed URL(s) 404'd last triage: ashby/replicate, greenhouse/openai, lever/huggingface"
        )

    def test_caps_list_at_five_but_count_is_total(self):
        """>5 dead slugs: count reflects the true total, only first 5 are named."""
        slugs = [f"greenhouse/co{i}" for i in range(7)]
        msg = _dead_slug_warning(slugs)
        assert msg is not None
        assert msg.startswith("WARN: 7 feed URL(s) 404'd last triage: ")
        assert "greenhouse/co0" in msg
        assert "greenhouse/co4" in msg
        assert "greenhouse/co5" not in msg
        assert "greenhouse/co6" not in msg
        listed = msg.split("last triage: ", 1)[1]
        assert len(listed.split(", ")) == 5
