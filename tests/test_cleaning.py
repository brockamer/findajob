"""Regression tests for src/findajob/cleaning.py — #182 dedup cluster.

Covers three bug modes surfaced during #61 PR-B smoke test:
- A — leading whitespace in title produces different fingerprints
- B — same URL re-ingested produces different fingerprints (location volatility)
- C — LinkedIn ↔ Greenhouse syndication ingested as two rows (coarse location)

Plus broader clean_title / clean_company / fingerprint coverage.
"""

from __future__ import annotations

from findajob.cleaning import (
    clean_company,
    clean_title,
    fingerprint,
    is_coarse_location,
    loose_fingerprint,
    normalize,
    normalize_location,
)

# ── Bug A — title whitespace ────────────────────────────────────────────────


class TestCleanTitleWhitespace:
    def test_leading_space_stripped(self):
        assert clean_title(" Director of Lab Services") == "Director of Lab Services"

    def test_trailing_space_stripped(self):
        assert clean_title("Director of Lab Services ") == "Director of Lab Services"

    def test_leading_nbsp_stripped(self):
        # Non-breaking space (U+00A0) — str.strip() with no args does NOT strip this.
        assert clean_title(" Director of Lab Services") == "Director of Lab Services"

    def test_leading_tab_stripped(self):
        assert clean_title("\tDirector of Lab Services") == "Director of Lab Services"

    def test_internal_whitespace_runs_collapsed(self):
        assert clean_title("Director  of   Lab    Services") == "Director of Lab Services"

    def test_internal_nbsp_collapsed(self):
        assert clean_title("Director of Lab Services") == "Director of Lab Services"

    def test_whitespace_variants_produce_same_fingerprint(self):
        """Bug A: two titles differing only in leading whitespace must dedupe."""
        fp1 = fingerprint(clean_title("Director of Lab Services"), "Nscale", "Barstow, TX")
        fp2 = fingerprint(clean_title(" Director of Lab Services"), "Nscale", "Barstow, TX")
        fp3 = fingerprint(clean_title(" Director of Lab Services"), "Nscale", "Barstow, TX")
        assert fp1 == fp2 == fp3


# ── Bug B — location volatility ─────────────────────────────────────────────


class TestLocationNormalization:
    def test_parenthetical_onsite_stripped(self):
        assert normalize_location("Barstow, TX (On-site)") == normalize_location("Barstow, TX")

    def test_parenthetical_remote_stripped(self):
        assert normalize_location("Austin, TX (Remote)") == normalize_location("Austin, TX")

    def test_parenthetical_hybrid_stripped(self):
        assert normalize_location("Menlo Park, CA (Hybrid)") == normalize_location("Menlo Park, CA")

    def test_trailing_country_us_stripped(self):
        # "Sunnyvale, CA, United States" should match "Sunnyvale, CA"
        assert normalize_location("Sunnyvale, CA, United States") == normalize_location("Sunnyvale, CA")

    def test_trailing_country_us_abbrev_stripped(self):
        assert normalize_location("Sunnyvale, CA, US") == normalize_location("Sunnyvale, CA")

    def test_distinct_cities_remain_distinct(self):
        """Preserve genuine per-location reqs (Data Center Site Manager case)."""
        assert normalize_location("Austin, TX") != normalize_location("Menlo Park, CA")

    def test_empty_location_stable(self):
        assert normalize_location("") == ""
        assert normalize_location("   ") == ""


class TestBugBSameUrlStableFingerprint:
    def test_location_suffix_variance_produces_same_fingerprint(self):
        """Bug B: re-ingest of same URL shouldn't produce new fingerprint when
        only the LinkedIn-appended on-site/remote tag varies."""
        fp_run1 = fingerprint("Director of Lab Services", "Nscale", "Barstow, TX")
        fp_run2 = fingerprint("Director of Lab Services", "Nscale", "Barstow, TX (On-site)")
        assert fp_run1 == fp_run2


# ── Bug C — cross-source syndication with coarse location ──────────────────


class TestCoarseLocationDetector:
    def test_empty_is_coarse(self):
        assert is_coarse_location("")

    def test_country_code_is_coarse(self):
        assert is_coarse_location("US")
        assert is_coarse_location("United States")
        assert is_coarse_location("UK")
        assert is_coarse_location("Canada")

    def test_specific_city_is_not_coarse(self):
        assert not is_coarse_location("Barstow, TX")
        assert not is_coarse_location("Menlo Park, CA")
        assert not is_coarse_location("Sunnyvale, CA, United States")

    def test_country_with_noise_is_still_coarse(self):
        # "US (Remote)" after stripping parenthetical is just "US" — coarse.
        assert is_coarse_location("US (Remote)")


class TestLooseFingerprint:
    def test_same_company_title_produces_same_loose_fp(self):
        fp1 = loose_fingerprint("Data Center Operations Program Manager", "Nscale")
        fp2 = loose_fingerprint("Data Center Operations Program Manager", "Nscale")
        assert fp1 == fp2

    def test_different_titles_produce_different_loose_fps(self):
        fp1 = loose_fingerprint("Data Center Operations Program Manager", "Nscale")
        fp2 = loose_fingerprint("Director of Lab Services", "Nscale")
        assert fp1 != fp2


class TestBugCCrossSourceDedup:
    """LinkedIn syndication of a Greenhouse posting — one side has coarse location."""

    def test_linkedin_specific_vs_greenhouse_coarse_loose_matches(self):
        """LinkedIn says 'Barstow, TX', Greenhouse says 'US' — (company, title) must match."""
        linkedin_loose = loose_fingerprint("Data Center Operations Program Manager", "Nscale")
        greenhouse_loose = loose_fingerprint("Data Center Operations Program Manager", "Nscale")
        assert linkedin_loose == greenhouse_loose

    def test_distinct_cities_do_not_get_loose_matched(self):
        """Same (company, title) but both sides have specific cities — not a candidate
        for Tier 2 loose match. Triage should gate the Tier 2 lookup on coarseness."""
        austin = is_coarse_location("Austin, TX")
        menlo = is_coarse_location("Menlo Park, CA")
        # Both specific → Tier 2 should not be used.
        assert not austin
        assert not menlo


# ── Broader coverage for cleaning primitives ───────────────────────────────


class TestCleanTitleExisting:
    def test_strips_via_dice(self):
        assert clean_title("Director of Lab Services · Jobs via Dice · Austin, TX") == "Director of Lab Services"

    def test_strips_salary_suffix(self):
        assert clean_title("Senior SRE · $180K - $220K · Remote") == "Senior SRE"

    def test_strips_days_ago(self):
        assert clean_title("Staff Engineer · 2 days ago") == "Staff Engineer"


class TestCleanCompany:
    def test_strips_location_suffix(self):
        assert clean_company("Google · Sunnyvale, CA, US") == "Google"

    def test_strips_connections_count(self):
        assert clean_company("Meta · 12 connections") == "Meta"

    def test_empty_returns_empty(self):
        assert clean_company("") == ""


class TestNormalize:
    def test_lowercases(self):
        assert normalize("DIRECTOR OF LAB") == "director of lab"

    def test_collapses_whitespace(self):
        assert normalize("director   of    lab") == "director of lab"

    def test_expands_abbreviations(self):
        assert "senior" in normalize("Sr. Engineer")
        assert "director" in normalize("Dir. of Ops")
