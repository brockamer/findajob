"""Tests for findajob.config_loader."""

from __future__ import annotations

import pytest

from findajob import config_loader
from findajob.config_loader import (
    ConfigError,
    is_company_of_interest,
    load_companies_of_interest,
    parse_target_companies_tier1,
)


class TestParseTargetCompaniesTier1:
    """#211 — parser moved from findajob.onboarding.injector.derive_companies_of_interest."""

    def test_strips_bullets_and_commentary(self):
        md = (
            "## Tier 1 — Active Focus\n"
            "- Acme Corp\n"
            "- Example Industries — would take a role there today\n"
            "- Sample Systems (public benefit corp)\n"
        )
        assert parse_target_companies_tier1(md) == ["Acme Corp", "Example Industries", "Sample Systems"]

    def test_ignores_tier2_and_beyond(self):
        md = (
            "## Tier 1 — Active Focus\n- A\n- B\n\n"
            "## Tier 2 — Strong Interest\n- C\n- D\n\n"
            "## Tier 3 — Opportunistic\n- E\n"
        )
        assert parse_target_companies_tier1(md) == ["A", "B"]

    def test_handles_star_bullets_and_numbered(self):
        md = "## Tier 1 — Active Focus\n* Alpha Co\n1. Beta Inc\n- Gamma LLC\n"
        assert parse_target_companies_tier1(md) == ["Alpha Co", "Beta Inc", "Gamma LLC"]

    def test_returns_empty_list_when_no_tier1(self):
        assert parse_target_companies_tier1("## Tier 2\n- Z\n") == []

    def test_case_insensitive_tier1_heading(self):
        # Heading regex tolerates `Tier 1` / `tier 1` / `TIER 1` / spacing variations
        assert parse_target_companies_tier1("## TIER 1\n- X\n") == ["X"]
        assert parse_target_companies_tier1("## tier1\n- Y\n") == ["Y"]

    def test_empty_input(self):
        assert parse_target_companies_tier1("") == []


class TestLoadCompaniesOfInterest:
    def test_loads_from_fixture(self):
        result = load_companies_of_interest()
        assert isinstance(result, frozenset)
        assert "meta" in result
        assert "google" in result
        assert "openai" in result

    def test_lowercases_entries(self):
        result = load_companies_of_interest()
        assert all(c == c.lower() for c in result)

    def test_caches_result(self):
        result1 = load_companies_of_interest()
        result2 = load_companies_of_interest()
        assert result1 is result2  # same object — cache hit


class TestIsCompanyOfInterest:
    @pytest.mark.parametrize(
        "company",
        ["Meta", "meta", "META", "Meta Platforms, Inc.", "Google Cloud", "OpenAI Research"],
    )
    def test_positive_substring_clear(self, company):
        assert is_company_of_interest(company) is True

    @pytest.mark.parametrize(
        "company",
        ["Walmart", "Starbucks", "Acme Corp", "Random Startup LLC"],
    )
    def test_negative(self, company):
        assert is_company_of_interest(company) is False

    def test_empty_string(self):
        assert is_company_of_interest("") is False

    def test_none(self):
        # Typed as str but guard handles falsy
        assert is_company_of_interest(None) is False  # type: ignore[arg-type]


class TestLoadHardRejectRules:
    def test_returns_two_regexes(self):
        reject_re, suppressor_re = config_loader.load_hard_reject_rules()
        assert reject_re.search("Software Engineer") is not None
        assert suppressor_re is not None  # fixture has suppressors

    def test_matches_across_categories(self):
        reject_re, _ = config_loader.load_hard_reject_rules()
        # software category
        assert reject_re.search("Senior Software Engineer") is not None
        assert reject_re.search("SWE II") is not None
        # healthcare category
        assert reject_re.search("Registered Nurse") is not None
        # sales category
        assert reject_re.search("Enterprise Account Executive") is not None

    def test_no_match_for_in_domain_title(self):
        reject_re, _ = config_loader.load_hard_reject_rules()
        assert reject_re.search("Data Center Operations Engineer") is None

    def test_suppressor_compiled(self):
        _, suppressor_re = config_loader.load_hard_reject_rules()
        assert suppressor_re.search("Data Center Security Analyst") is not None
        assert suppressor_re.search("Datacenter NOC") is not None
        assert suppressor_re.search("Security Analyst") is None  # no DC context

    def test_caches_result(self):
        r1 = config_loader.load_hard_reject_rules()
        r2 = config_loader.load_hard_reject_rules()
        assert r1 is r2  # cache hit returns same tuple


class TestLoadInDomainRules:
    def test_positive_matches(self):
        in_domain_re, _ = config_loader.load_in_domain_rules()
        assert in_domain_re.search("Data Center Operations Engineer") is not None
        assert in_domain_re.search("NPI Manager") is not None
        assert in_domain_re.search("Operational Readiness Lead") is not None

    def test_positive_misses_out_of_domain(self):
        in_domain_re, _ = config_loader.load_in_domain_rules()
        assert in_domain_re.search("Software Engineer") is None
        assert in_domain_re.search("Account Executive") is None

    def test_poison_compiled(self):
        _, poison_re = config_loader.load_in_domain_rules()
        assert poison_re is not None
        assert poison_re.search("Data Center Workplace Services Manager") is not None
        assert poison_re.search("Custodial Lead") is not None
        assert poison_re.search("Data Center Operations") is None  # no poison term

    def test_caches_result(self):
        r1 = config_loader.load_in_domain_rules()
        r2 = config_loader.load_in_domain_rules()
        assert r1 is r2


class TestMissingFiles:
    def test_missing_rules_file_returns_never_match(self, monkeypatch, tmp_path):
        monkeypatch.setattr(config_loader, "_RULES_PATH", tmp_path / "does-not-exist.yaml")
        config_loader._reset_cache()
        with pytest.warns(UserWarning, match="prefilter_rules.yaml missing"):
            reject_re, suppressor_re = config_loader.load_hard_reject_rules()
        assert reject_re.search("Software Engineer") is None
        assert reject_re is config_loader._NEVER_MATCH
        assert suppressor_re is None

    def test_missing_in_domain_file_returns_never_match(self, monkeypatch, tmp_path):
        monkeypatch.setattr(config_loader, "_IN_DOMAIN_PATH", tmp_path / "does-not-exist.yaml")
        config_loader._reset_cache()
        with pytest.warns(UserWarning, match="in_domain_patterns.yaml missing"):
            in_domain_re, poison_re = config_loader.load_in_domain_rules()
        assert in_domain_re.search("Data Center Operations") is None
        assert poison_re is None

    def test_missing_target_companies_file_returns_empty(self, monkeypatch, tmp_path):
        monkeypatch.setattr(config_loader, "_TARGET_COMPANIES_PATH", tmp_path / "does-not-exist.md")
        config_loader._reset_cache()
        with pytest.warns(UserWarning, match="target_companies.md missing"):
            result = config_loader.load_companies_of_interest()
        assert result == frozenset()
        assert config_loader.is_company_of_interest("Meta") is False

    def test_target_companies_without_tier1_returns_empty(self, monkeypatch, tmp_path):
        bad = tmp_path / "target_companies.md"
        bad.write_text("# Some Header\n\nNo Tier 1 section here.\n")
        monkeypatch.setattr(config_loader, "_TARGET_COMPANIES_PATH", bad)
        config_loader._reset_cache()
        with pytest.warns(UserWarning, match="no '## Tier 1' section"):
            result = config_loader.load_companies_of_interest()
        assert result == frozenset()

    def test_empty_rules_file(self, monkeypatch, tmp_path):
        empty = tmp_path / "prefilter_rules.yaml"
        empty.write_text("")
        monkeypatch.setattr(config_loader, "_RULES_PATH", empty)
        config_loader._reset_cache()
        with pytest.warns(UserWarning, match="prefilter_rules.yaml is empty"):
            reject_re, _ = config_loader.load_hard_reject_rules()
        assert reject_re.search("anything") is None


class TestMalformedFiles:
    def test_bad_yaml_raises_config_error(self, monkeypatch, tmp_path):
        bad = tmp_path / "prefilter_rules.yaml"
        bad.write_text("hard_rejects: {unclosed")
        monkeypatch.setattr(config_loader, "_RULES_PATH", bad)
        config_loader._reset_cache()
        with pytest.raises(ConfigError, match="YAML parse error"):
            config_loader.load_hard_reject_rules()

    def test_top_level_list_raises(self, monkeypatch, tmp_path):
        bad = tmp_path / "prefilter_rules.yaml"
        bad.write_text("- just\n- a\n- list\n")
        monkeypatch.setattr(config_loader, "_RULES_PATH", bad)
        config_loader._reset_cache()
        with pytest.raises(ConfigError, match="top level must be a mapping"):
            config_loader.load_hard_reject_rules()

    def test_hard_rejects_as_list_raises(self, monkeypatch, tmp_path):
        bad = tmp_path / "prefilter_rules.yaml"
        bad.write_text("hard_rejects:\n  - '\\bfoo\\b'\n")
        monkeypatch.setattr(config_loader, "_RULES_PATH", bad)
        config_loader._reset_cache()
        with pytest.raises(ConfigError, match="'hard_rejects' must be a mapping"):
            config_loader.load_hard_reject_rules()

    def test_bad_regex_raises_with_pattern(self, monkeypatch, tmp_path):
        bad = tmp_path / "prefilter_rules.yaml"
        bad.write_text("hard_rejects:\n  broken:\n    - '(unclosed'\n")
        monkeypatch.setattr(config_loader, "_RULES_PATH", bad)
        config_loader._reset_cache()
        with pytest.raises(ConfigError, match=r"invalid regex.*\(unclosed"):
            config_loader.load_hard_reject_rules()

    def test_non_string_pattern_raises(self, monkeypatch, tmp_path):
        bad = tmp_path / "prefilter_rules.yaml"
        bad.write_text("hard_rejects:\n  bad:\n    - 42\n")
        monkeypatch.setattr(config_loader, "_RULES_PATH", bad)
        config_loader._reset_cache()
        with pytest.raises(ConfigError, match="pattern in 'bad' is not a string"):
            config_loader.load_hard_reject_rules()

    def test_in_domain_positive_as_dict_raises(self, monkeypatch, tmp_path):
        bad = tmp_path / "in_domain_patterns.yaml"
        bad.write_text("positive:\n  nested: value\n")
        monkeypatch.setattr(config_loader, "_IN_DOMAIN_PATH", bad)
        config_loader._reset_cache()
        with pytest.raises(ConfigError, match="'positive' must be a list"):
            config_loader.load_in_domain_rules()


class TestLoadExcludedEmployers:
    """#84 — deterministic company exclusion via excluded_employers.yaml."""

    def test_returns_configured_values(self):
        # Fixture provides exact + regex entries
        exact_set, regex_re = config_loader.load_excluded_employers()
        assert "excluded corp" in exact_set
        assert regex_re is not None
        assert regex_re.search("State of California") is not None

    def test_returns_empty_when_file_missing(self, monkeypatch, tmp_path):
        monkeypatch.setattr(config_loader, "_EXCLUDED_EMPLOYERS_PATH", tmp_path / "missing.yaml")
        config_loader._reset_cache()
        exact_set, regex_re = config_loader.load_excluded_employers()
        assert exact_set == frozenset()
        assert regex_re is None

    def test_empty_lists_returns_empty(self, monkeypatch, tmp_path):
        f = tmp_path / "ee.yaml"
        f.write_text("exact: []\nregex: []\n")
        monkeypatch.setattr(config_loader, "_EXCLUDED_EMPLOYERS_PATH", f)
        config_loader._reset_cache()
        exact_set, regex_re = config_loader.load_excluded_employers()
        assert exact_set == frozenset()
        assert regex_re is None

    def test_exact_lowercased(self, monkeypatch, tmp_path):
        f = tmp_path / "ee.yaml"
        f.write_text('exact:\n  - "MixedCase Corp"\n')
        monkeypatch.setattr(config_loader, "_EXCLUDED_EMPLOYERS_PATH", f)
        config_loader._reset_cache()
        exact_set, _ = config_loader.load_excluded_employers()
        assert "mixedcase corp" in exact_set
        assert "MixedCase Corp" not in exact_set

    def test_exact_as_dict_raises(self, monkeypatch, tmp_path):
        bad = tmp_path / "ee.yaml"
        bad.write_text("exact:\n  nested: value\n")
        monkeypatch.setattr(config_loader, "_EXCLUDED_EMPLOYERS_PATH", bad)
        config_loader._reset_cache()
        with pytest.raises(ConfigError, match="'exact' must be a list"):
            config_loader.load_excluded_employers()

    def test_regex_as_dict_raises(self, monkeypatch, tmp_path):
        bad = tmp_path / "ee.yaml"
        bad.write_text("regex:\n  nested: value\n")
        monkeypatch.setattr(config_loader, "_EXCLUDED_EMPLOYERS_PATH", bad)
        config_loader._reset_cache()
        with pytest.raises(ConfigError, match="'regex' must be a list"):
            config_loader.load_excluded_employers()

    def test_non_string_exact_raises(self, monkeypatch, tmp_path):
        bad = tmp_path / "ee.yaml"
        bad.write_text("exact:\n  - 42\n")
        monkeypatch.setattr(config_loader, "_EXCLUDED_EMPLOYERS_PATH", bad)
        config_loader._reset_cache()
        with pytest.raises(ConfigError, match="exact entry is not a string"):
            config_loader.load_excluded_employers()

    def test_non_string_regex_raises(self, monkeypatch, tmp_path):
        bad = tmp_path / "ee.yaml"
        bad.write_text("regex:\n  - 42\n")
        monkeypatch.setattr(config_loader, "_EXCLUDED_EMPLOYERS_PATH", bad)
        config_loader._reset_cache()
        with pytest.raises(ConfigError, match="regex pattern is not a string"):
            config_loader.load_excluded_employers()

    def test_invalid_regex_raises(self, monkeypatch, tmp_path):
        bad = tmp_path / "ee.yaml"
        bad.write_text("regex:\n  - '(unclosed'\n")
        monkeypatch.setattr(config_loader, "_EXCLUDED_EMPLOYERS_PATH", bad)
        config_loader._reset_cache()
        with pytest.raises(ConfigError, match=r"invalid regex.*\(unclosed"):
            config_loader.load_excluded_employers()

    def test_reads_per_call_so_settings_saves_take_effect(self, monkeypatch, tmp_path):
        """#729 — loader is read-per-call (not cached) so /settings/excluded-employers/
        saves take effect on the next request without process restart. Mirrors
        load_reject_reasons (#490)."""
        f = tmp_path / "excluded_employers.yaml"
        f.write_text("exact:\n  - 'First'\n")
        monkeypatch.setattr(config_loader, "_EXCLUDED_EMPLOYERS_PATH", f)
        config_loader._reset_cache()

        exact1, _ = config_loader.load_excluded_employers()
        assert exact1 == frozenset({"first"})

        # Simulate /settings/excluded-employers/ save: rewrite the file.
        f.write_text("exact:\n  - 'Second'\n")
        exact2, _ = config_loader.load_excluded_employers()
        assert exact2 == frozenset({"second"})


class TestLoadRejectReasons:
    """#429 — single source of truth for the reject-reason taxonomy."""

    def test_returns_defaults_when_file_missing(self, monkeypatch, tmp_path):
        monkeypatch.setattr(config_loader, "_REJECT_REASONS_PATH", tmp_path / "missing.yaml")
        config_loader._reset_cache()
        reasons, title_signal = config_loader.load_reject_reasons()
        assert reasons == config_loader._DEFAULT_REJECT_REASONS
        assert title_signal == config_loader._DEFAULT_TITLE_SIGNAL_REASONS
        # Defaults must be field-agnostic: no operator-domain tokens.
        assert "Too TPM-Heavy" not in reasons
        assert "Too Senior" not in reasons

    def test_reads_configured_values(self, monkeypatch, tmp_path):
        f = tmp_path / "reject_reasons.yaml"
        f.write_text(
            'reasons:\n  - "Skills Gap"\n  - "Wrong Shift"\n  - "Other"\ntitle_signal_reasons:\n  - "Skills Gap"\n'
        )
        monkeypatch.setattr(config_loader, "_REJECT_REASONS_PATH", f)
        config_loader._reset_cache()
        reasons, title_signal = config_loader.load_reject_reasons()
        assert reasons == ("Skills Gap", "Wrong Shift", "Other")
        assert title_signal == frozenset({"Skills Gap"})

    def test_empty_reasons_returns_defaults(self, monkeypatch, tmp_path):
        f = tmp_path / "reject_reasons.yaml"
        f.write_text("reasons: []\n")
        monkeypatch.setattr(config_loader, "_REJECT_REASONS_PATH", f)
        config_loader._reset_cache()
        reasons, title_signal = config_loader.load_reject_reasons()
        assert reasons == config_loader._DEFAULT_REJECT_REASONS
        assert title_signal == config_loader._DEFAULT_TITLE_SIGNAL_REASONS

    def test_missing_title_signal_returns_empty_frozenset(self, monkeypatch, tmp_path):
        f = tmp_path / "reject_reasons.yaml"
        f.write_text('reasons:\n  - "Skills Mismatch"\n')
        monkeypatch.setattr(config_loader, "_REJECT_REASONS_PATH", f)
        config_loader._reset_cache()
        reasons, title_signal = config_loader.load_reject_reasons()
        assert reasons == ("Skills Mismatch",)
        assert title_signal == frozenset()

    def test_reasons_as_dict_raises(self, monkeypatch, tmp_path):
        bad = tmp_path / "reject_reasons.yaml"
        bad.write_text("reasons:\n  nested: value\n")
        monkeypatch.setattr(config_loader, "_REJECT_REASONS_PATH", bad)
        config_loader._reset_cache()
        with pytest.raises(ConfigError, match="'reasons' must be a list"):
            config_loader.load_reject_reasons()

    def test_title_signal_as_dict_raises(self, monkeypatch, tmp_path):
        bad = tmp_path / "reject_reasons.yaml"
        bad.write_text('reasons:\n  - "x"\ntitle_signal_reasons:\n  nested: value\n')
        monkeypatch.setattr(config_loader, "_REJECT_REASONS_PATH", bad)
        config_loader._reset_cache()
        with pytest.raises(ConfigError, match="'title_signal_reasons' must be a list"):
            config_loader.load_reject_reasons()

    def test_non_string_reason_raises(self, monkeypatch, tmp_path):
        bad = tmp_path / "reject_reasons.yaml"
        bad.write_text("reasons:\n  - 42\n")
        monkeypatch.setattr(config_loader, "_REJECT_REASONS_PATH", bad)
        config_loader._reset_cache()
        with pytest.raises(ConfigError, match="reasons entry is not a string"):
            config_loader.load_reject_reasons()

    def test_no_cache_picks_up_file_changes(self, monkeypatch, tmp_path):
        """#490: cache removed so /settings/reject-reasons/ saves take
        effect on the next request without a process restart."""
        f = tmp_path / "reject_reasons.yaml"
        f.write_text("reasons:\n  - One\n  - Two\n")
        monkeypatch.setattr(config_loader, "_REJECT_REASONS_PATH", f)

        reasons1, _ = config_loader.load_reject_reasons()
        assert reasons1 == ("One", "Two")

        f.write_text("reasons:\n  - Three\n  - Four\n")
        reasons2, _ = config_loader.load_reject_reasons()
        assert reasons2 == ("Three", "Four")  # No _reset_cache() call needed


class TestSaveRejectReasons:
    """#490: writer for `config/reject_reasons.yaml`."""

    def test_atomic_roundtrip(self, monkeypatch, tmp_path):
        f = tmp_path / "reject_reasons.yaml"
        monkeypatch.setattr(config_loader, "_REJECT_REASONS_PATH", f)

        config_loader.save_reject_reasons(("Alpha", "Beta", "Gamma"), frozenset({"Alpha"}))
        reasons, title_signal = config_loader.load_reject_reasons()
        assert reasons == ("Alpha", "Beta", "Gamma")
        assert title_signal == frozenset({"Alpha"})

    def test_rejects_empty_reasons(self, monkeypatch, tmp_path):
        f = tmp_path / "reject_reasons.yaml"
        monkeypatch.setattr(config_loader, "_REJECT_REASONS_PATH", f)
        with pytest.raises(ConfigError, match="non-empty"):
            config_loader.save_reject_reasons((), frozenset())

    def test_rejects_empty_after_strip(self, monkeypatch, tmp_path):
        f = tmp_path / "reject_reasons.yaml"
        monkeypatch.setattr(config_loader, "_REJECT_REASONS_PATH", f)
        with pytest.raises(ConfigError, match="non-empty|empty"):
            config_loader.save_reject_reasons(("",), frozenset())

    def test_rejects_comma_in_reason(self, monkeypatch, tmp_path):
        f = tmp_path / "reject_reasons.yaml"
        monkeypatch.setattr(config_loader, "_REJECT_REASONS_PATH", f)
        with pytest.raises(ConfigError, match="comma"):
            config_loader.save_reject_reasons(("Skills, mismatch",), frozenset())

    def test_rejects_title_signal_not_in_reasons(self, monkeypatch, tmp_path):
        f = tmp_path / "reject_reasons.yaml"
        monkeypatch.setattr(config_loader, "_REJECT_REASONS_PATH", f)
        with pytest.raises(ConfigError, match="title_signal"):
            config_loader.save_reject_reasons(("Alpha",), frozenset({"NotInReasons"}))

    def test_rejects_duplicate_reasons(self, monkeypatch, tmp_path):
        f = tmp_path / "reject_reasons.yaml"
        monkeypatch.setattr(config_loader, "_REJECT_REASONS_PATH", f)
        with pytest.raises(ConfigError, match="duplicate"):
            config_loader.save_reject_reasons(("Alpha", "Alpha"), frozenset())

    def test_atomic_no_partial_write_on_failure(self, monkeypatch, tmp_path):
        """If os.replace fails mid-write, the original file is left intact."""
        f = tmp_path / "reject_reasons.yaml"
        f.write_text("reasons:\n  - Original\n")
        monkeypatch.setattr(config_loader, "_REJECT_REASONS_PATH", f)

        def boom(*a, **kw):
            raise OSError("simulated failure")

        monkeypatch.setattr("os.replace", boom)

        with pytest.raises(OSError):
            config_loader.save_reject_reasons(("New",), frozenset())

        assert "Original" in f.read_text()

    def test_strips_whitespace(self, monkeypatch, tmp_path):
        f = tmp_path / "reject_reasons.yaml"
        monkeypatch.setattr(config_loader, "_REJECT_REASONS_PATH", f)
        config_loader.save_reject_reasons(("  Padded  ",), frozenset({"  Padded  "}))
        reasons, title_signal = config_loader.load_reject_reasons()
        assert reasons == ("Padded",)
        assert title_signal == frozenset({"Padded"})
