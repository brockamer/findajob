"""Shared fixtures for loose_ends Phase 2 tests."""

from __future__ import annotations

import pytest


@pytest.fixture
def sample_dom_empty_state() -> str:
    """A page with an empty collection container and no CTA — cat 3 positive."""
    return """
    <html><body>
        <h1>Applied Jobs</h1>
        <table id="applied-jobs"><tbody></tbody></table>
    </body></html>
    """


@pytest.fixture
def sample_dom_empty_state_with_cta() -> str:
    """A page with an empty collection container but a CTA — cat 3 negative."""
    return """
    <html><body>
        <h1>Applied Jobs</h1>
        <p>No applications yet. Mark a job applied from the dashboard.</p>
        <table id="applied-jobs"><tbody></tbody></table>
        <a href="/board/dashboard">Go to dashboard</a>
    </body></html>
    """


@pytest.fixture
def sample_dom_flow_without_exit() -> str:
    """A page where the user landed via action and has no exit — cat 2 positive."""
    return """
    <html><body>
        <h1>Status: Interviewing</h1>
        <p>Your interview status has been recorded.</p>
    </body></html>
    """


@pytest.fixture
def sample_dom_flow_with_exit() -> str:
    """A page where the user landed via action and has clear exit — cat 2 negative."""
    return """
    <html><body>
        <h1>Status: Interviewing</h1>
        <p>Your interview status has been recorded.</p>
        <button>Back to Applied</button>
        <button>Undo</button>
    </body></html>
    """


@pytest.fixture
def sample_findings() -> list[dict]:
    """A handful of findings for roll-up tests."""
    return [
        {
            "persona": "nux_user",
            "walkthrough_name": "dashboard_first_load",
            "current_url": "/board/dashboard",
            "category": 3,
            "confidence": "high",
            "rationale": "Empty state with no CTA.",
            "suggested_surface": "Add 'Start onboarding' button to empty dashboard",
            "excluded": False,
        },
        {
            "persona": "established_user",
            "walkthrough_name": "applied_undo_exits",
            "current_url": "/board/applied",
            "category": 2,
            "confidence": "medium",
            "rationale": "No visible undo button.",
            "suggested_surface": "Add Undo to Applied dropdown",
            "excluded": False,
        },
    ]
