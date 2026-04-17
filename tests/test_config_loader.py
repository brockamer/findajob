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
