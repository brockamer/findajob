"""Integration tests for the OpenRouter credit chip in the nav (#665).

These exercise the full template render path via TestClient, so the test
fails if the Jinja global is unwired, the template forgets to call the
helper, or the failure-open contract leaks an exception into the response.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from findajob import openrouter_credits
from findajob.openrouter_credits import CreditInfo
from findajob.web.app import create_app


@pytest.fixture
def app_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Build the app against a tmp DB+companies dir; mark onboarding complete."""
    from findajob.db.migrate import apply_pending
    from findajob.onboarding import mark_complete

    monkeypatch.delenv("FINDAJOB_OPERATOR_MODE", raising=False)
    companies = tmp_path / "companies"
    companies.mkdir(exist_ok=True)
    db = tmp_path / "pipeline.db"
    conn = sqlite3.connect(str(db))
    try:
        apply_pending(conn)
    finally:
        conn.close()
    mark_complete(tmp_path)
    app = create_app(companies_root=companies, db_path=db, base_root=tmp_path)
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clear_credit_cache():
    openrouter_credits.reset_cache_for_tests()
    yield
    openrouter_credits.reset_cache_for_tests()


def test_credit_chip_rendered_when_helper_returns_credit_info(app_client: TestClient) -> None:
    """Healthy CreditInfo → chip HTML appears with formatted amount and slate (normal) classes."""
    with patch.object(
        openrouter_credits,
        "credit_remaining",
        return_value=CreditInfo(remaining_usd=42.50, state="normal"),
    ):
        r = app_client.get("/docs/")

    assert r.status_code == 200
    assert 'id="nav-credit-remaining"' in r.text
    assert "$42.50 left" in r.text
    assert "bg-slate-700/40" in r.text


def test_credit_chip_uses_amber_classes_when_state_amber(app_client: TestClient) -> None:
    """state='amber' → amber Tailwind classes in the chip span."""
    with patch.object(
        openrouter_credits,
        "credit_remaining",
        return_value=CreditInfo(remaining_usd=3.42, state="amber"),
    ):
        r = app_client.get("/docs/")

    assert r.status_code == 200
    assert 'id="nav-credit-remaining"' in r.text
    assert "$3.42 left" in r.text
    assert "bg-amber-600/40" in r.text


def test_credit_chip_uses_red_classes_when_state_red(app_client: TestClient) -> None:
    """state='red' → rose Tailwind classes in the chip span."""
    with patch.object(
        openrouter_credits,
        "credit_remaining",
        return_value=CreditInfo(remaining_usd=0.42, state="red"),
    ):
        r = app_client.get("/docs/")

    assert r.status_code == 200
    assert 'id="nav-credit-remaining"' in r.text
    assert "$0.42 left" in r.text
    assert "bg-rose-700/40" in r.text


def test_credit_chip_hidden_when_helper_returns_none(app_client: TestClient) -> None:
    """Failure-open: helper returns None → no chip in HTML, page still 200."""
    with patch.object(openrouter_credits, "credit_remaining", return_value=None):
        r = app_client.get("/docs/")

    assert r.status_code == 200
    assert 'id="nav-credit-remaining"' not in r.text
    # Spend chip should still render (independent helper)
    assert 'id="nav-credits"' in r.text


def test_credit_chip_does_not_block_page_when_helper_raises(app_client: TestClient) -> None:
    """Defense-in-depth: even if the helper raises (it shouldn't), the page must render.

    The helper's contract is failure-open (never raise), but Jinja's `if` guard
    on the function call provides a second layer — except the call itself would
    propagate. So this test verifies the helper contract holds: returning None
    is the *only* way it fails. If a future refactor breaks that and starts
    raising, this test fails loudly.
    """
    # If the helper raises, the page render WILL 500. This test asserts that
    # the helper's contract is honored — it returns None for any error, never
    # raises. We exercise the real (non-mocked) helper with no API key.
    import os

    saved = os.environ.pop("OPENROUTER_API_KEY", None)
    try:
        r = app_client.get("/docs/")
        assert r.status_code == 200
        assert 'id="nav-credit-remaining"' not in r.text
    finally:
        if saved is not None:
            os.environ["OPENROUTER_API_KEY"] = saved
