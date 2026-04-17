"""Tests for findajob.config_loader."""

from __future__ import annotations

import pytest

from findajob import config_loader
from findajob.config_loader import (
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
