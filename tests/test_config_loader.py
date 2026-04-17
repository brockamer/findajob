"""Tests for findajob.config_loader."""

from __future__ import annotations

import pytest

from findajob import config_loader
from findajob.config_loader import (
    ConfigError,
    is_company_of_interest,
    load_companies_of_interest,
)


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

    def test_missing_companies_file_returns_empty(self, monkeypatch, tmp_path):
        monkeypatch.setattr(config_loader, "_COMPANIES_PATH", tmp_path / "does-not-exist.txt")
        config_loader._reset_cache()
        with pytest.warns(UserWarning, match="companies_of_interest.txt missing"):
            result = config_loader.load_companies_of_interest()
        assert result == frozenset()
        assert config_loader.is_company_of_interest("Meta") is False

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
