"""Tests for the shared feed-URL probe helper (#984).

The helper probes each ATS board URL in feed_urls.txt against its public
API for liveness, so onboarding (#984), the verify-feed-urls settings
button (#985), and the runtime 404 health-check (#983) can all reuse one
source of truth — no drift.

The probe must NEVER raise into its caller: onboarding completion must
not be blockable by a slow or offline ATS API.
"""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, patch

import requests

from findajob.fetchers.feed_probe import (
    FeedProbeResult,
    is_plausible_company_name,
    probe_feed_line,
    probe_feed_urls,
)


def _make_result(*, status: str, company_name_ok: bool) -> FeedProbeResult:
    return FeedProbeResult(
        line="https://jobs.lever.co/acme  # comment",
        kind="lever",
        slug="acme",
        status=status,  # type: ignore[arg-type]
        http_status=200 if status == "live" else None,
        reason="",
        company="comment",
        company_name_ok=company_name_ok,
    )


def test_is_label_warning_true_only_for_live_feed_with_bad_comment():
    """A live feed (it WILL fetch jobs) whose inline comment is junk is the
    #856 pollution case worth surfacing. A dead/unreachable feed won't fetch,
    so its comment is moot — not a label warning.
    """
    assert _make_result(status="live", company_name_ok=False).is_label_warning is True
    assert _make_result(status="live", company_name_ok=True).is_label_warning is False
    assert _make_result(status="dead", company_name_ok=False).is_label_warning is False
    assert _make_result(status="unreachable", company_name_ok=False).is_label_warning is False
    assert _make_result(status="unsupported", company_name_ok=False).is_label_warning is False


@patch("findajob.fetchers.feed_probe.requests.get")
def test_live_greenhouse_url_returns_live_and_probes_real_api_endpoint(mock_get):
    """A 200 from the Greenhouse boards API marks the slug live, and the
    URL actually probed is the adapter's endpoint template — not the board
    URL from feed_urls.txt. This pins the no-drift contract: the probe
    reuses GreenhouseAdapter._ENDPOINT_TEMPLATE rather than its own copy.
    """
    mock_get.return_value = MagicMock(status_code=200)

    result = probe_feed_line("https://boards.greenhouse.io/acmecorp")

    assert result is not None
    assert result.kind == "greenhouse"
    assert result.slug == "acmecorp"
    assert result.status == "live"
    assert result.http_status == 200

    probed_url = mock_get.call_args[0][0]
    assert probed_url == "https://boards-api.greenhouse.io/v1/boards/acmecorp/jobs?content=true"


@patch("findajob.fetchers.feed_probe.requests.get")
def test_404_marks_slug_dead_with_actionable_reason(mock_get):
    """A 404 is a verdict about the slug itself — it's not a valid board.
    The reason must name the offending slug so the user knows what to fix.
    """
    mock_get.return_value = MagicMock(status_code=404)

    result = probe_feed_line("https://jobs.ashbyhq.com/deadco")

    assert result is not None
    assert result.status == "dead"
    assert result.http_status == 404
    assert "404" in result.reason
    assert "deadco" in result.reason


@patch("findajob.fetchers.feed_probe.requests.get")
def test_server_error_is_unreachable_not_dead(mock_get):
    """A 5xx is transient — about the server right now, not the slug. Must
    NOT condemn the slug as dead, or the UI cries wolf on a healthy feed.
    """
    mock_get.return_value = MagicMock(status_code=503)

    result = probe_feed_line("https://jobs.lever.co/flakyco")

    assert result is not None
    assert result.status == "unreachable"
    assert result.http_status == 503
    assert result.status != "dead"


@patch("findajob.fetchers.feed_probe.requests.get")
def test_rate_limited_is_unreachable_not_dead(mock_get):
    """429 is transient back-pressure, not a dead slug."""
    mock_get.return_value = MagicMock(status_code=429)

    result = probe_feed_line("https://jobs.lever.co/busyco")

    assert result is not None
    assert result.status == "unreachable"
    assert result.status != "dead"


@patch("findajob.fetchers.feed_probe.requests.get")
def test_network_error_is_unreachable_and_never_raises(mock_get):
    """A connection failure must be swallowed into a result, never raised —
    the never-block-onboarding contract. http_status is None (no response).
    """
    mock_get.side_effect = requests.exceptions.ConnectionError("dns fail")

    result = probe_feed_line("https://boards.greenhouse.io/offlineco")

    assert result is not None
    assert result.status == "unreachable"
    assert result.http_status is None


@patch("findajob.fetchers.feed_probe.requests.get")
def test_timeout_is_unreachable_and_never_raises(mock_get):
    """A timeout must be swallowed into a result, never raised."""
    mock_get.side_effect = requests.exceptions.Timeout("slow")

    result = probe_feed_line("https://boards.greenhouse.io/slowco")

    assert result is not None
    assert result.status == "unreachable"
    assert result.http_status is None


@patch("findajob.fetchers.feed_probe.requests.get")
def test_ashby_url_probes_the_ashby_posting_api(mock_get):
    """Ashby kind detection + endpoint reuse (no drift vs AshbyAdapter)."""
    mock_get.return_value = MagicMock(status_code=200)

    result = probe_feed_line("https://jobs.ashbyhq.com/Ramp")

    assert result is not None
    assert result.kind == "ashby"
    assert result.slug == "Ramp"
    assert result.status == "live"
    assert mock_get.call_args[0][0] == "https://api.ashbyhq.com/posting-api/job-board/Ramp"


@patch("findajob.fetchers.feed_probe.requests.get")
def test_lever_url_probes_the_lever_postings_api(mock_get):
    """Lever kind detection + endpoint reuse (no drift vs LeverAdapter)."""
    mock_get.return_value = MagicMock(status_code=200)

    result = probe_feed_line("https://jobs.lever.co/zoox")

    assert result is not None
    assert result.kind == "lever"
    assert result.slug == "zoox"
    assert mock_get.call_args[0][0] == "https://api.lever.co/v0/postings/zoox"


@patch("findajob.fetchers.feed_probe.requests.get")
def test_inline_comment_captured_as_company_and_marked_clean(mock_get):
    """For Ashby/Lever the inline comment IS the company display name. A
    clean single-word company name must round-trip and read as plausible.
    """
    mock_get.return_value = MagicMock(status_code=200)

    result = probe_feed_line("https://jobs.lever.co/zoox  # Zoox")

    assert result is not None
    assert result.slug == "zoox"
    assert result.company == "Zoox"
    assert result.company_name_ok is True


@patch("findajob.fetchers.feed_probe.requests.get")
def test_blank_line_is_nothing_to_probe(mock_get):
    assert probe_feed_line("   ") is None
    mock_get.assert_not_called()


@patch("findajob.fetchers.feed_probe.requests.get")
def test_comment_only_line_is_nothing_to_probe(mock_get):
    assert probe_feed_line("# ===== Greenhouse boards =====") is None
    mock_get.assert_not_called()


@patch("findajob.fetchers.feed_probe.requests.get")
def test_unknown_ats_url_is_flagged_unsupported_not_dropped(mock_get):
    """A non-empty URL that matches no supported ATS must be surfaced as
    'unsupported' (flagged, not silently dropped) and must make no HTTP call.
    """
    result = probe_feed_line("https://acme.wd1.myworkdayjobs.com/careers")

    assert result is not None
    assert result.status == "unsupported"
    assert result.kind is None
    assert result.slug is None
    assert result.http_status is None
    mock_get.assert_not_called()


def test_clean_company_names_are_plausible():
    assert is_plausible_company_name("Zoox") is True
    assert is_plausible_company_name("CoreWeave") is True
    assert is_plausible_company_name("Astera Labs") is True


def test_url_in_company_name_is_implausible():
    assert is_plausible_company_name("https://acme.com") is False
    assert is_plausible_company_name("see www.acme.com/jobs") is False


def test_sentence_like_provenance_company_name_is_implausible():
    assert is_plausible_company_name("Acme Corp added via the discovery run on 2026 May cohort sweep") is False


@patch("findajob.fetchers.feed_probe.requests.get")
def test_junk_inline_comment_flags_company_name_not_ok(mock_get):
    """A junk comment (provenance/URL) is the #856 failure mode — it would
    pollute jobs.company. The probe must flag it even when the URL is live.
    """
    mock_get.return_value = MagicMock(status_code=200)

    result = probe_feed_line("https://jobs.ashbyhq.com/acme  # https://acme.com careers page")

    assert result is not None
    assert result.status == "live"
    assert result.company_name_ok is False


@patch("findajob.fetchers.feed_probe.requests.get")
def test_batch_probes_supported_lines_in_order_and_skips_blanks(mock_get):
    """The batch returns one result per probeable line, in input order.
    Blank and comment-only lines are skipped; unsupported URLs are KEPT
    (flagged), so a junk line can't silently vanish from the report.
    """
    mock_get.return_value = MagicMock(status_code=200)
    lines = [
        "https://boards.greenhouse.io/acme  # Acme",
        "",
        "# ===== heading =====",
        "https://jobs.ashbyhq.com/ramp  # Ramp",
        "https://acme.wd1.myworkdayjobs.com/careers",
    ]

    results = probe_feed_urls(lines)

    assert [r.kind for r in results] == ["greenhouse", "ashby", None]
    assert [r.slug for r in results] == ["acme", "ramp", None]
    assert results[2].status == "unsupported"


@patch("findajob.fetchers.feed_probe.requests.get")
def test_batch_never_raises_on_unexpected_error(mock_get):
    """A non-RequestException in the request layer must NOT escape the batch
    — it is the ultimate fail-safe for the never-block-onboarding contract.
    """
    mock_get.side_effect = ValueError("boom inside the request layer")

    results = probe_feed_urls(["https://boards.greenhouse.io/acme"])

    assert len(results) == 1
    assert results[0].status == "unreachable"


@patch("findajob.fetchers.feed_probe.requests.get")
def test_batch_offline_marks_all_unreachable(mock_get):
    """Fully offline: every line resolves to unreachable, no exception."""
    mock_get.side_effect = requests.exceptions.ConnectionError("offline")

    results = probe_feed_urls(
        [
            "https://boards.greenhouse.io/a",
            "https://jobs.ashbyhq.com/b",
            "https://jobs.lever.co/c",
        ]
    )

    assert len(results) == 3
    assert all(r.status == "unreachable" for r in results)


def test_batch_empty_input_returns_empty_list():
    assert probe_feed_urls([]) == []


@patch("findajob.fetchers.feed_probe.requests.get")
def test_batch_caps_concurrency_at_max_workers(mock_get):
    """Concurrency is bounded: with max_workers=2, no more than 2 probes are
    ever in flight at once — so a long feed list can't fan out unboundedly.
    """
    in_flight = 0
    peak = 0
    lock = threading.Lock()

    def slow(*args, **kwargs):
        nonlocal in_flight, peak
        with lock:
            in_flight += 1
            peak = max(peak, in_flight)
        time.sleep(0.05)
        with lock:
            in_flight -= 1
        return MagicMock(status_code=200)

    mock_get.side_effect = slow
    lines = [f"https://jobs.lever.co/co{i}" for i in range(6)]

    probe_feed_urls(lines, max_workers=2)

    assert peak <= 2  # the cap held
    assert peak > 1  # ...and parallelism actually happened (not serialized)


@patch("findajob.fetchers.feed_probe.requests.get")
def test_probe_sends_the_same_user_agent_as_the_adapters(mock_get):
    """No-drift extends to headers: every adapter sends a findajob User-Agent.
    If the probe sent the bare python-requests UA, an ATS that filters it would
    block the probe (-> false 'unreachable') while the real fetch succeeds.
    """
    mock_get.return_value = MagicMock(status_code=200)

    probe_feed_line("https://boards.greenhouse.io/acme")

    sent_headers = mock_get.call_args.kwargs.get("headers", {})
    assert sent_headers.get("User-Agent") == "findajob-pipeline/1.0 (personal job search tool)"


@patch("findajob.fetchers.feed_probe.requests.get")
def test_custom_timeout_is_passed_through_to_requests(mock_get):
    mock_get.return_value = MagicMock(status_code=200)

    probe_feed_line("https://boards.greenhouse.io/acme", timeout=2.5)

    assert mock_get.call_args.kwargs["timeout"] == 2.5


@patch("findajob.fetchers.feed_probe.requests.get")
def test_greenhouse_eu_and_jobboards_variants_probe_the_unified_api(mock_get):
    """EU and job-boards Greenhouse URLs are valid board URLs. There is no
    separate EU API host (boards-api.eu.greenhouse.io does not resolve), so all
    variants correctly probe the unified boards-api.greenhouse.io — same host
    the adapter uses. This pins that an EU board is NOT falsely marked dead.
    """
    mock_get.return_value = MagicMock(status_code=200)
    for url, slug in [
        ("https://boards.eu.greenhouse.io/eucorp", "eucorp"),
        ("https://job-boards.greenhouse.io/jbcorp", "jbcorp"),
    ]:
        mock_get.reset_mock()
        mock_get.return_value = MagicMock(status_code=200)

        result = probe_feed_line(url)

        assert result is not None
        assert result.kind == "greenhouse"
        assert result.slug == slug
        assert result.status == "live"
        assert mock_get.call_args[0][0] == f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"


@patch("findajob.fetchers.feed_probe.requests.get")
def test_slug_with_dots_and_dashes_extracted_intact(mock_get):
    """Slugs legitimately contain dots/dashes (regex char class [A-Za-z0-9_.-]);
    they must survive into the probed URL unmangled.
    """
    mock_get.return_value = MagicMock(status_code=200)

    result = probe_feed_line("https://jobs.lever.co/my-company.io")

    assert result is not None
    assert result.slug == "my-company.io"
    assert mock_get.call_args[0][0] == "https://api.lever.co/v0/postings/my-company.io"


def test_company_name_length_boundary():
    assert is_plausible_company_name("A" * 49) is True
    assert is_plausible_company_name("A" * 50) is True
    assert is_plausible_company_name("A" * 51) is False


def test_company_name_word_count_boundary():
    assert is_plausible_company_name("one two three four five") is True  # 5 words
    assert is_plausible_company_name("one two three four five six") is True  # 6 words
    assert is_plausible_company_name("one two three four five six seven") is False  # 7 words


def test_batch_absorbs_real_invalid_timeout_without_raising():
    """The onboarding-safety guarantee, exercised against the REAL codepath:
    a genuinely invalid timeout makes urllib3 raise ValueError before any
    network call. probe_feed_urls must absorb it (degrade to unreachable),
    never raise — onboarding completion can't be blocked. No mock here on
    purpose: this is the actual ValueError path, not a simulated one.
    """
    results = probe_feed_urls(["https://boards.greenhouse.io/acme"], timeout=-1)

    assert len(results) == 1
    assert results[0].status == "unreachable"
