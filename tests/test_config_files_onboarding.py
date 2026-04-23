"""Verify #148 adds target_companies.md + business_sector reference to the /config/ editor.

companies_of_interest.txt is derived, not user-edited — it must NOT be editable.
"""

from __future__ import annotations

from findajob.web.config_files import EDITABLE_CATEGORIES, is_editable


def test_target_companies_is_editable() -> None:
    assert is_editable("config/target_companies.md") is True


def test_business_sector_reference_is_editable() -> None:
    assert is_editable("config/business_sector_employers_reference.md") is True


def test_companies_of_interest_is_not_editable() -> None:
    # Derived at injection time — editing it directly would drift.
    assert is_editable("config/companies_of_interest.txt") is False


def test_both_new_files_listed_under_search_config() -> None:
    search = EDITABLE_CATEGORIES["Search config"]
    assert isinstance(search, list)
    assert "config/target_companies.md" in search
    assert "config/business_sector_employers_reference.md" in search
