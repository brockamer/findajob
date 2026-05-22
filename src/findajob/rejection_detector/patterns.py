"""Pattern data for the rejection-detection classifier.

Single source of truth — never inline these strings in detector logic.
Adding a new ATS sender or marker phrase: edit this file, add a fixture
under tests/fixtures/rejection_emails/, ship one PR.

Spec: docs/superpowers/specs/2026-05-01-362-rejection-detection-design.md §3
"""

from __future__ import annotations

SENDER_FINGERPRINTS: dict[str, str] = {
    "us.greenhouse-mail.io": "greenhouse",
    "eu.greenhouse-mail.io": "greenhouse",
    "ashbyhq.com": "ashby",
    "hire.lever.co": "lever",
    "myworkday.com": "workday",
    "smartrecruiters.com": "smartrecruiters",
    "email.careers.microsoft.com": "microsoft",
    "oracle.com": "oracle",
    "tesla.com": "tesla",
}


REJECTION_BODY_MARKERS: tuple[str, ...] = (
    "decided to move forward with other candidates",
    "decided to pursue other candidates",
    "unfortunately, we are not moving forward",
    "will not be moving forward",
    "yours was not selected",
    "your application was not selected",
    "regret to inform",
    "the position has been filled",
    "no longer accepting applications",
    "we have decided not to proceed",
    "we have decided not to move forward with your application",
    "we will not be proceeding with your candidacy",
    "after careful review of your application",
)


ACK_BODY_MARKERS: tuple[str, ...] = (
    "thank you for applying to",
    "your application has been received",
    "we will review your application shortly",
    "application received",
)


SOFT_PAUSE_MARKERS: tuple[str, ...] = (
    "pausing the recruitment process",
    "we are pausing",
    "paused the search",
)


POSITION_FILLED_MARKERS: tuple[str, ...] = (
    "position has been filled",
    "no longer accepting applications",
)


NON_RECRUITING_DOMAINS: frozenset[str] = frozenset(
    {
        "linkedin.com",
        "jobs-noreply@linkedin.com",
        "indeed.com",
    }
)


SENIORITY_TOKENS: frozenset[str] = frozenset(
    {
        "junior",
        "jr",
        "associate",
        "senior",
        "sr",
        "staff",
        "principal",
        "lead",
        "director",
        "vp",
        "head",
        "manager",
        "mgr",
    }
)


def match_sender(sender_domain: str) -> str | None:
    """Return ATS platform name if sender_domain matches a fingerprint, else None.

    Suffix match handles Workday-style ``talent.{company}.com`` —
    if any fingerprint key is a suffix of the input domain, it's a match.
    """
    sender_lower = sender_domain.lower()
    for fp, platform in SENDER_FINGERPRINTS.items():
        if sender_lower.endswith(fp):
            return platform
    if "talent." in sender_lower or "myworkday.com" in sender_lower:
        return "workday"
    return None


def has_rejection_marker(body_lower: str) -> bool:
    return any(marker in body_lower for marker in REJECTION_BODY_MARKERS)


def has_ack_marker(body_lower: str) -> bool:
    return any(marker in body_lower for marker in ACK_BODY_MARKERS)


def has_soft_pause_marker(body_lower: str) -> bool:
    return any(marker in body_lower for marker in SOFT_PAUSE_MARKERS)


def has_position_filled_marker(body_lower: str) -> bool:
    return any(marker in body_lower for marker in POSITION_FILLED_MARKERS)
