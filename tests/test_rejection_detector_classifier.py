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


# ─── #585: classifier extraction-bug regression fixtures ─────────────────────
# Four real rejection-email shapes from operator's May-09 first-run backlog
# that the original `_INTEREST_RE` / `_POSITION_RE` regexes misextracted.
# Each fixture asserts BOTH confidence='high' AND that the extraction
# returned a usable value — guards against silent extraction failure that
# the older confidence-only tests would have missed.


def test_thanks_subject_extracts_company_not_role_token() -> None:
    """Subject 'Thanks for your interest in X, Name' must extract X.

    Regression: the original `_INTEREST_RE` required literal 'thank you' so
    the subject missed entirely. Body extraction then over-captured a role
    token ('the Program Manager') from 'interest in the Program Manager...
    role with COMPANY' — exactly the Zoox-shape failure in operator's queue.
    """
    raw = (FIXTURES / "lever" / "thanks_subject_role_in_body.eml").read_bytes()
    suggestion = classify_email(raw)
    assert suggestion is not None
    assert suggestion.confidence == "high"
    assert suggestion.extracted_company is not None
    assert "NimbusCo" in suggestion.extracted_company
    # The role token 'the Program Manager' MUST NOT leak into the company field.
    assert "Program Manager" not in suggestion.extracted_company


def test_so_much_adverb_interjection_still_extracts() -> None:
    """Body 'Thank you so much for your interest in X' must still extract X.

    Regression: the original regex required `for` to immediately follow
    `you`; 'so much' between them broke the match — Anthropic-shape
    failure where extracted_company stayed None on operator's queue.
    """
    raw = (FIXTURES / "greenhouse" / "so_much_interjection.eml").read_bytes()
    suggestion = classify_email(raw)
    assert suggestion is not None
    assert suggestion.confidence == "high"
    assert suggestion.extracted_company is not None
    assert "AcmeAI" in suggestion.extracted_company
    # The continuation 'AcmeAI and for the time' must NOT leak into the
    # captured company value — the ' and ' alternation in the terminator
    # class is the load-bearing piece.
    assert "and for the time" not in suggestion.extracted_company


def test_as_the_next_step_continuation_terminates_company() -> None:
    """'interest in X as the next step in your career' must extract X, not the tail.

    Regression: lazy `.+?` over-captured up to the period at end of
    sentence because there was no comma/period between the company and the
    role-context continuation — Cobot-shape failure on operator's queue
    that surfaced as `extracted_company='Cobot as the next step in your career'`.
    """
    raw = (FIXTURES / "ashby" / "as_the_next_step.eml").read_bytes()
    suggestion = classify_email(raw)
    assert suggestion is not None
    assert suggestion.confidence == "high"
    assert suggestion.extracted_company is not None
    assert "AcmeRoboCo" in suggestion.extracted_company
    assert "as the next step" not in suggestion.extracted_company


def test_for_our_role_extracts_role() -> None:
    """Body 'for our [ROLE]' must extract the role.

    Regression: the original `_POSITION_RE` only accepted 'for the
    position' / 'application for' shapes — 'for our [ROLE]' produced
    None for `extracted_role`, leaving the matcher with only a company
    to disambiguate by, which collapses to 'ambiguous' when the company
    has multiple active applications. Crusoe-shape failure on operator's
    queue.
    """
    raw = (FIXTURES / "ashby" / "for_our_role.eml").read_bytes()
    suggestion = classify_email(raw)
    assert suggestion is not None
    assert suggestion.confidence == "high"
    assert suggestion.extracted_company is not None
    assert "AcmeCloudCo" in suggestion.extracted_company
    assert suggestion.extracted_role is not None
    # Either 'Infrastructure Engineer' or 'Lab Manager' is acceptable —
    # both are role tokens the matcher can use for disambiguation.
    assert "Infrastructure Engineer" in suggestion.extracted_role or "Lab Manager" in suggestion.extracted_role
