"""Tests for the shared RapidAPI key resolver (#414)."""

from __future__ import annotations

import pytest

from findajob.fetchers.adapters._keys import resolve_rapidapi_key


@pytest.fixture(autouse=True)
def _scrub_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("RAPIDAPI_KEY", "JOBS_API14_KEY", "JSEARCH_API_KEY"):
        monkeypatch.delenv(var, raising=False)


def test_returns_empty_when_no_vars_set() -> None:
    assert resolve_rapidapi_key("RAPIDAPI_KEY", "JOBS_API14_KEY") == ""


def test_returns_canonical_when_only_canonical_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RAPIDAPI_KEY", "shared-1234")
    assert resolve_rapidapi_key("RAPIDAPI_KEY", "JOBS_API14_KEY") == "shared-1234"


def test_returns_dedicated_when_only_dedicated_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JOBS_API14_KEY", "legacy-1234")
    assert resolve_rapidapi_key("RAPIDAPI_KEY", "JOBS_API14_KEY") == "legacy-1234"


def test_canonical_wins_over_dedicated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RAPIDAPI_KEY", "shared-1234")
    monkeypatch.setenv("JOBS_API14_KEY", "legacy-1234")
    assert resolve_rapidapi_key("RAPIDAPI_KEY", "JOBS_API14_KEY") == "shared-1234"


def test_treats_empty_string_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RAPIDAPI_KEY", "")
    monkeypatch.setenv("JOBS_API14_KEY", "legacy-1234")
    assert resolve_rapidapi_key("RAPIDAPI_KEY", "JOBS_API14_KEY") == "legacy-1234"


def test_treats_whitespace_only_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RAPIDAPI_KEY", "   ")
    monkeypatch.setenv("JOBS_API14_KEY", "legacy-1234")
    assert resolve_rapidapi_key("RAPIDAPI_KEY", "JOBS_API14_KEY") == "legacy-1234"


def test_argument_order_defines_priority(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RAPIDAPI_KEY", "shared-1234")
    monkeypatch.setenv("JOBS_API14_KEY", "legacy-1234")
    # Reversed order: dedicated first should win
    assert resolve_rapidapi_key("JOBS_API14_KEY", "RAPIDAPI_KEY") == "legacy-1234"
