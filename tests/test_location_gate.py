"""Tests for the deterministic location gate (#1217).

The gate is a recognizer, not a matcher: it rejects a posting only when it
can name a US state, a country or a non-US region that is outside the
candidate's target list. Anything it cannot classify passes to the scorer.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from findajob import location_gate
from findajob.location_gate import load_targets, location_mismatch, parse_targets

# A metro-area persona: one city plus Remote.
METRO = parse_targets(["Denver, CO", "Remote"])
US_WIDE = parse_targets(["United States"])
UK = parse_targets(["London, UK", "Remote"])
CANADA = parse_targets(["Toronto, ON, Canada"])


# ── parse_targets ─────────────────────────────────────────────────────────────


class TestParseTargets:
    def test_metro_list_is_a_gate(self):
        assert METRO is not None

    @pytest.mark.parametrize(
        "lines",
        [
            [],
            ["Remote"],
            ["Denver"],
            ["Remote", "Anywhere"],
        ],
    )
    def test_list_without_a_recognized_place_turns_the_gate_off(self, lines):
        assert parse_targets(lines) is None


# ── location_mismatch: metro-area persona ─────────────────────────────────────


class TestMetroPersona:
    @pytest.mark.parametrize(
        "location",
        [
            "",
            "Remote",
            "United States",
            "US",
            "USA",
            "U.S.",
            "Remote (US)",
            "United States (Remote)",
            "US-Remote",
            "Anywhere in the US",
            "Denver, CO",
            "Boulder, CO 80301",
            "Hybrid (Denver, CO)",
            "Denver, Colorado, United States",
            "Denver CO",
            # Names the gate cannot place: the scorer decides.
            "Dublin",
            "Portland",
            "Cambridge",
            "Greater Boston Area",
            "See posting",
            "2 Locations",
            # Multi-location strings pass if any part fits.
            "NYC, SF, Chicago, Remote",
            "New York, NY or Remote",
            "Austin, TX; Denver, CO",
            "Remote in Austin, TX",
        ],
    )
    def test_passes(self, location):
        assert location_mismatch(location, METRO) is None

    @pytest.mark.parametrize(
        "location",
        [
            "San Francisco, CA",
            "Seoul, South Korea",
            "Singapore",
            "Dublin, Ireland",
            "New York, NY; Washington, DC",
            "London, United Kingdom",
            "Toronto, ON, Canada",
            "Cambridge, MA",
            "Austin, Texas",
            "AUSTIN, TX",
            "Austin TX",
            "Washington, D.C.",
            "Remote - India",
            "Remote - Europe",
            "Remote, EMEA",
            "San Francisco, CA; Seoul, South Korea",
        ],
    )
    def test_rejects(self, location):
        assert location_mismatch(location, METRO) is not None

    @pytest.mark.parametrize(
        ("location", "named"),
        [
            ("San Francisco, CA", "CA"),
            ("Seoul, South Korea", "South Korea"),
            ("Remote - India", "India"),
            ("Austin, Texas", "Texas"),
        ],
    )
    def test_reject_names_the_place_outside_the_targets(self, location, named):
        assert location_mismatch(location, METRO) == named


# ── location_mismatch: other target lists ─────────────────────────────────────


class TestUnitedStatesWide:
    @pytest.mark.parametrize("location", ["San Francisco, CA", "Austin, TX", "Remote - CA", "New York, NY"])
    def test_any_us_place_passes(self, location):
        assert location_mismatch(location, US_WIDE) is None

    @pytest.mark.parametrize("location", ["Seoul, South Korea", "Canada", "Remote - India", "Remote - EMEA"])
    def test_foreign_place_rejects(self, location):
        assert location_mismatch(location, US_WIDE) is not None


class TestNonUsCandidate:
    @pytest.mark.parametrize(
        "location",
        ["Manchester, United Kingdom", "Edinburgh, Scotland", "Remote", "Remote - EMEA", "Remote - Europe"],
    )
    def test_passes(self, location):
        assert location_mismatch(location, UK) is None

    @pytest.mark.parametrize("location", ["Berlin, Germany", "Austin, TX", "Remote (US)"])
    def test_rejects(self, location):
        assert location_mismatch(location, UK) is not None

    def test_ca_as_country_code_still_matches_canada(self):
        assert location_mismatch("Toronto, ON, CA", CANADA) is None

    def test_us_state_rejects_for_canadian_targets(self):
        assert location_mismatch("Seattle, WA", CANADA) is not None


class TestAmbiguousNames:
    def test_washington_alone_matches_dc_targets(self):
        assert location_mismatch("Washington", parse_targets(["Washington, DC"])) is None

    def test_washington_alone_matches_wa_targets(self):
        assert location_mismatch("Washington", parse_targets(["Seattle, WA"])) is None


class TestStateLineMetro:
    """Decision 1 compares US states, so a metro that spans a state line needs one
    target line per state. The onboarding interviewer writes them."""

    def test_other_side_of_the_state_line_rejects_with_one_state(self):
        assert location_mismatch("Overland Park, KS", parse_targets(["Kansas City, MO"])) is not None

    def test_other_side_of_the_state_line_passes_with_both_states(self):
        targets = parse_targets(["Kansas City, MO", "Kansas City, KS"])
        assert location_mismatch("Overland Park, KS", targets) is None


# ── load_targets ──────────────────────────────────────────────────────────────


class TestLoadTargets:
    def test_absent_file_turns_the_gate_off(self, tmp_path: Path):
        assert load_targets(tmp_path / "target_locations.txt") is None

    def test_reads_the_file(self, tmp_path: Path):
        f = tmp_path / "target_locations.txt"
        f.write_text("# where I can work\n\nDenver, CO\nRemote\n")
        targets = load_targets(f)
        assert location_mismatch("Seoul, South Korea", targets) == "South Korea"
        assert location_mismatch("Boulder, CO", targets) is None

    def test_default_path_is_the_module_path(self, tmp_path: Path, monkeypatch):
        f = tmp_path / "target_locations.txt"
        f.write_text("Denver, CO\n")
        monkeypatch.setattr(location_gate, "_TARGET_LOCATIONS_PATH", f)
        assert location_mismatch("Austin, TX", load_targets()) == "TX"

    @pytest.mark.parametrize("value", ["off", "OFF", "0", "false", "no"])
    def test_env_switch_turns_the_gate_off(self, tmp_path: Path, monkeypatch, value):
        f = tmp_path / "target_locations.txt"
        f.write_text("Denver, CO\n")
        monkeypatch.setenv("FINDAJOB_LOCATION_GATE", value)
        assert load_targets(f) is None

    def test_env_switch_on_keeps_the_gate(self, tmp_path: Path, monkeypatch):
        f = tmp_path / "target_locations.txt"
        f.write_text("Denver, CO\n")
        monkeypatch.setenv("FINDAJOB_LOCATION_GATE", "on")
        assert load_targets(f) is not None

    def test_target_lines_reads_the_file_without_the_gate_switch(self, tmp_path: Path, monkeypatch):
        f = tmp_path / "target_locations.txt"
        f.write_text("# comment\nDenver, CO\n\nRemote\n")
        monkeypatch.setenv("FINDAJOB_LOCATION_GATE", "off")
        assert location_gate.target_lines(f) == ["Denver, CO", "Remote"]

    def test_target_lines_absent_file(self, tmp_path: Path):
        assert location_gate.target_lines(tmp_path / "nope.txt") == []


class TestRealFeedStrings:
    """Shapes seen in a live Greenhouse feed (2026-10-13 end-to-end run)."""

    @pytest.mark.parametrize(
        "location",
        [
            # A work-mode word and a country in one segment: the country still counts.
            "US Remote; Canada",
            "US Remote; Chicago, Atlanta, Canada",
            "Remote in the US, Remote in Canada",
            "Remote from the US",
            "US REMOTE",
        ],
    )
    def test_us_remote_inside_one_segment_passes(self, location):
        assert location_mismatch(location, METRO) is None

    def test_remote_in_a_foreign_country_rejects(self):
        assert location_mismatch("Remote in Canada", METRO) == "Canada"

    def test_uppercase_or_separates_alternatives(self):
        assert location_mismatch("London OR Paris OR Germany", METRO) == "Germany"

    def test_oregon_code_after_a_comma_is_still_a_state(self):
        assert location_mismatch("Salem, OR", parse_targets(["Portland, OR"])) is None
        assert location_mismatch("Salem, OR", METRO) == "OR"
