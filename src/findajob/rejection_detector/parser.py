"""Email parsing for the rejection detector.

Extracts the fields the classifier needs from raw RFC 822 bytes:
sender domain, subject, plaintext body, candidate company/role.

Body extraction handles both text/plain and text/html parts. HTML-only
emails (Microsoft Careers, Oracle, Smartrecruiters in the corpus) get
bs4-stripped to plaintext before pattern matching.

Spec: docs/superpowers/specs/2026-05-01-362-rejection-detection-design.md §4.2 parser.py
"""

from __future__ import annotations

import email
import re
from dataclasses import dataclass
from email.message import Message
from email.utils import parseaddr, parsedate_to_datetime

from bs4 import BeautifulSoup


@dataclass(frozen=True)
class ParsedEmail:
    """Raw email data the classifier operates on."""

    message_id: str
    received_at: str
    sender: str
    sender_domain: str
    subject: str
    plaintext_body: str
    subject_lower: str
    plaintext_body_lower: str


def parse(raw: bytes) -> ParsedEmail:
    """Parse raw RFC 822 bytes into the fields the classifier needs.

    Body extraction:
        1. Walk multipart MIME tree; take first text/plain part if present.
        2. If no text/plain part, take first text/html and strip via bs4.
        3. If neither, body is empty string.
    """
    msg = email.message_from_bytes(raw)
    sender_addr = parseaddr(msg.get("From", ""))[1]
    sender_domain = sender_addr.split("@", 1)[-1].lower() if "@" in sender_addr else ""

    subject = msg.get("Subject", "") or ""
    received_at = _format_received_at(msg)
    body = _extract_body(msg)
    message_id = msg.get("Message-ID", "") or ""

    return ParsedEmail(
        message_id=message_id,
        received_at=received_at,
        sender=sender_addr,
        sender_domain=sender_domain,
        subject=subject,
        plaintext_body=body,
        subject_lower=subject.lower(),
        plaintext_body_lower=body.lower(),
    )


def _format_received_at(msg: Message) -> str:
    date_hdr = msg.get("Date", "")
    if not date_hdr:
        return ""
    try:
        dt = parsedate_to_datetime(date_hdr)
        return dt.isoformat() if dt is not None else ""
    except (TypeError, ValueError):
        return ""


def _extract_body(msg: Message) -> str:
    """Return decoded plaintext body. Walks multipart, falls back to bs4 strip."""
    plaintext = ""
    html_payload = ""

    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            if ctype == "text/plain" and not plaintext:
                plaintext = _decode_part(part)
            elif ctype == "text/html" and not html_payload:
                html_payload = _decode_part(part)
    else:
        ctype = msg.get_content_type()
        if ctype == "text/plain":
            plaintext = _decode_part(msg)
        elif ctype == "text/html":
            html_payload = _decode_part(msg)

    if plaintext:
        return _normalize_whitespace(plaintext)
    if html_payload:
        return _strip_html(html_payload)
    return ""


def _decode_part(part: Message) -> str:
    payload = part.get_payload(decode=True)
    if payload is None:
        return ""
    if not isinstance(payload, bytes):
        return ""
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except (LookupError, UnicodeDecodeError):
        return payload.decode("utf-8", errors="replace")


_WHITESPACE_RUN_RE = re.compile(r"\s+")


def _normalize_whitespace(text: str) -> str:
    """Collapse all whitespace runs (incl. line wraps) to single spaces.

    Email body text is line-wrapped at ~76 chars, breaking marker phrases
    across newlines. ``"unfortunately, we are not moving\\nforward"`` would
    otherwise miss its marker in REJECTION_BODY_MARKERS.
    """
    return _WHITESPACE_RUN_RE.sub(" ", text).strip()


def _strip_html(html: str) -> str:
    """bs4-extract text + collapse whitespace runs."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    text = soup.get_text(separator=" ")
    return _normalize_whitespace(text)
