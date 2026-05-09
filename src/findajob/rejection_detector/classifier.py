"""Three-layer rejection classifier.

Layer 1: known ATS sender + rejection body marker + no ack marker → high.
Layer 2: unknown sender + rejection body marker + no ack marker → medium.
Layer 3: LLM tiebreak — deferred until L1+L2 precision data justifies it.

Spec: docs/superpowers/specs/2026-05-01-362-rejection-detection-design.md §4.2 classifier.py + §5
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from findajob.rejection_detector import patterns
from findajob.rejection_detector.parser import ParsedEmail, parse


@dataclass(frozen=True)
class RejectionSuggestion:
    """Output of the classifier — pure data, no DB id yet."""

    gmail_message_id: str
    received_at: str
    sender: str
    subject: str
    body_excerpt: str
    extracted_company: str | None
    extracted_role: str | None
    confidence: str
    suggested_reason: str


def classify_email(raw: bytes) -> RejectionSuggestion | None:
    """Classify a raw email. Returns None for acks, unknowns, and re:/fwd: replies.

    Match-to-job is not done here — that's `matcher.match_job` in matcher.py.
    """
    parsed = parse(raw)
    return _classify_parsed(parsed)


def _classify_parsed(parsed: ParsedEmail) -> RejectionSuggestion | None:
    if _is_hard_skip(parsed):
        return None

    body_lower = parsed.plaintext_body_lower
    sender_platform = patterns.match_sender(parsed.sender_domain)
    has_rej = patterns.has_rejection_marker(body_lower)
    has_ack = patterns.has_ack_marker(body_lower)
    has_pause = patterns.has_soft_pause_marker(body_lower)

    if has_ack and not (has_rej or has_pause):
        return None

    if sender_platform and (has_rej or has_pause) and not has_ack:
        confidence = "high"
    elif (has_rej or has_pause) and not has_ack:
        confidence = "medium"
    else:
        return None

    suggested_reason = _suggest_reason(body_lower)
    extracted_company, extracted_role = _extract_company_and_role(parsed)

    return RejectionSuggestion(
        gmail_message_id=parsed.message_id,
        received_at=parsed.received_at,
        sender=parsed.sender,
        subject=parsed.subject,
        body_excerpt=parsed.plaintext_body[:500],
        extracted_company=extracted_company,
        extracted_role=extracted_role,
        confidence=confidence,
        suggested_reason=suggested_reason,
    )


def _is_hard_skip(parsed: ParsedEmail) -> bool:
    if parsed.sender_domain in patterns.NON_RECRUITING_DOMAINS:
        return True
    if parsed.subject_lower.startswith(("re:", "fwd:")):
        return True
    if not parsed.plaintext_body:
        return True
    return False


def _suggest_reason(body_lower: str) -> str:
    if patterns.has_soft_pause_marker(body_lower):
        return "Position paused"
    if patterns.has_position_filled_marker(body_lower):
        return "Position filled"
    return "Company passed"


_INTEREST_RE = re.compile(
    r"(?:thank you for your (?:interest|application) in|"
    r"update on your application (?:for the position )?(?:at|to))\s+(.+?)(?:[.,!?\n]|$)",
    re.IGNORECASE,
)
_POSITION_RE = re.compile(
    r"(?:for the position(?: of)?|application for(?: the)?(?: position(?: of)?)?)\s+(.+?)(?:\s+at\s+|[.,!?\n]|$)",
    re.IGNORECASE,
)


def _extract_company_and_role(parsed: ParsedEmail) -> tuple[str | None, str | None]:
    """Heuristic extraction from subject + first body lines. Best-effort.

    The matcher tolerates None on either field. These heuristics target the
    subject-line shapes documented in spec §3.1; ambiguous mail returns
    (None, None) so the matcher emits ``status='unmatched'`` rather than
    a wrong-job confirmation.
    """
    subject = parsed.subject.strip()
    body = parsed.plaintext_body

    company: str | None = None
    role: str | None = None

    interest = _INTEREST_RE.search(subject)
    if interest:
        company = interest.group(1).strip().rstrip(".!?,")

    position = _POSITION_RE.search(subject)
    if position:
        role = position.group(1).strip().rstrip(".!?,")

    if company is None and body:
        first_lines = body[:400]
        m = _INTEREST_RE.search(first_lines)
        if m:
            company = m.group(1).strip().rstrip(".!?,")

    if role is None and body:
        first_lines = body[:400]
        m = _POSITION_RE.search(first_lines)
        if m:
            role = m.group(1).strip().rstrip(".!?,")

    return company, role
