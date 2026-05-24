"""Tests for company tier resolution."""

from __future__ import annotations

import pytest

from findajob.tiers import load_tier1_companies, resolve_tier


@pytest.fixture(autouse=True)
def _clear_cache():
    load_tier1_companies.cache_clear()
    yield
    load_tier1_companies.cache_clear()


def test_exact_match_returns_tier1(tmp_path, monkeypatch):
    coi = tmp_path / "config" / "companies_of_interest.txt"
    coi.parent.mkdir()
    coi.write_text("Acme Corporation\nGlobex\n")
    monkeypatch.setattr("findajob.tiers._coi_path", lambda: coi)
    assert resolve_tier("Acme Corporation") == "tier1"
    assert resolve_tier("Globex") == "tier1"


def test_case_insensitive(tmp_path, monkeypatch):
    coi = tmp_path / "config" / "companies_of_interest.txt"
    coi.parent.mkdir()
    coi.write_text("Acme Corporation\n")
    monkeypatch.setattr("findajob.tiers._coi_path", lambda: coi)
    assert resolve_tier("ACME CORPORATION") == "tier1"
    assert resolve_tier("acme corporation") == "tier1"


def test_no_match_returns_other(tmp_path, monkeypatch):
    coi = tmp_path / "config" / "companies_of_interest.txt"
    coi.parent.mkdir()
    coi.write_text("Acme Corporation\n")
    monkeypatch.setattr("findajob.tiers._coi_path", lambda: coi)
    assert resolve_tier("Globex") == "other"


def test_missing_file_returns_unknown(tmp_path, monkeypatch):
    missing = tmp_path / "does" / "not" / "exist.txt"
    monkeypatch.setattr("findajob.tiers._coi_path", lambda: missing)
    assert resolve_tier("Acme") == "unknown"


def test_empty_company_returns_unknown():
    assert resolve_tier("") == "unknown"
    assert resolve_tier(None) == "unknown"
