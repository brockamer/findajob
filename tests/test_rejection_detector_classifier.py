"""Golden-fixture tests for the three-layer classifier cascade."""

from __future__ import annotations

from pathlib import Path

import pytest

from findajob.rejection_detector.classifier import classify_email

FIXTURES = Path(__file__).parent / "fixtures" / "rejection_emails"


@pytest.mark.parametrize(
    "fixture",
    [
        FIXTURES / "greenhouse" / "rejection.eml",
        FIXTURES / "ashby" / "rejection.eml",
        FIXTURES / "ashby" / "will_not_proceed.eml",
        FIXTURES / "lever" / "rejection.eml",
        FIXTURES / "workday" / "rejection.eml",
    ],
)
def test_layer1_high_confidence_rejection(fixture: Path) -> None:
    """Known ATS sender + rejection body marker + no ack marker → confidence='high'."""
    suggestion = classify_email(fixture.read_bytes())
    assert suggestion is not None, f"{fixture.name}: classifier dropped a confirmed rejection"
    assert suggestion.confidence == "high", f"{fixture.name}: expected high, got {suggestion.confidence}"


@pytest.mark.parametrize(
    "fixture",
    [
        FIXTURES / "inhouse" / "decided_not_to_move_forward.eml",
        FIXTURES / "inhouse" / "position_filled.eml",
        FIXTURES / "inhouse" / "soft_pause.eml",
    ],
)
def test_layer2_medium_confidence_rejection(fixture: Path) -> None:
    """In-house sender (unknown to SENDER_FINGERPRINTS) + body marker → confidence='medium'.

    Spec §4.2 Layer 2: body markers are strong enough to surface but
    sender-side gating boosts to high only for the ATS allowlist.
    """
    suggestion = classify_email(fixture.read_bytes())
    assert suggestion is not None, f"{fixture.name}: classifier dropped a confirmed rejection"
    assert suggestion.confidence == "medium", f"{fixture.name}: expected medium, got {suggestion.confidence}"


@pytest.mark.parametrize(
    "fixture",
    [
        FIXTURES / "acks" / "linkedin.eml",
        FIXTURES / "acks" / "microsoft.eml",
        FIXTURES / "acks" / "smartrecruiters.eml",
        FIXTURES / "greenhouse" / "ack.eml",
        FIXTURES / "ashby" / "ack.eml",
        FIXTURES / "workday" / "ack.eml",
    ],
)
def test_acknowledgments_do_not_trigger(fixture: Path) -> None:
    """Application acks must NOT produce a suggestion."""
    suggestion = classify_email(fixture.read_bytes())
    assert suggestion is None, f"{fixture.name}: ack misclassified as rejection"


def test_position_filled_subtype() -> None:
    """spec §3.4: 'position has been filled' → suggested_reason='Position filled'."""
    raw = (FIXTURES / "inhouse" / "position_filled.eml").read_bytes()
    suggestion = classify_email(raw)
    assert suggestion is not None
    assert suggestion.suggested_reason == "Position filled"


def test_soft_pause_subtype() -> None:
    """spec §3.4: 'pausing the recruitment process' → suggested_reason='Position paused'."""
    raw = (FIXTURES / "inhouse" / "soft_pause.eml").read_bytes()
    suggestion = classify_email(raw)
    assert suggestion is not None
    assert suggestion.suggested_reason == "Position paused"


def test_html_only_email_extracted_via_bs4() -> None:
    """spec §4.2.parser: HTML-only emails (no text/plain part) must classify.

    The Oracle fixture is an ack — the test confirms bs4 extraction ran without error
    and the classifier reached its decision (None for an ack body).
    """
    raw = (FIXTURES / "html_only" / "oracle.eml").read_bytes()
    suggestion = classify_email(raw)
    assert suggestion is None


def test_html_only_microsoft_ack_does_not_trigger() -> None:
    raw = (FIXTURES / "html_only" / "microsoft_careers.eml").read_bytes()
    suggestion = classify_email(raw)
    assert suggestion is None


def test_html_only_smartrecruiters_ack_does_not_trigger() -> None:
    raw = (FIXTURES / "html_only" / "smartrecruiters.eml").read_bytes()
    suggestion = classify_email(raw)
    assert suggestion is None


def test_layer1_extracted_company_and_role() -> None:
    """Spot-check the heuristic company/role extraction on greenhouse rejection."""
    raw = (FIXTURES / "greenhouse" / "rejection.eml").read_bytes()
    suggestion = classify_email(raw)
    assert suggestion is not None
    assert suggestion.extracted_company is not None
    assert "ExampleCo" in suggestion.extracted_company
