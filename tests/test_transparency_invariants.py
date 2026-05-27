"""Tier 1 — codifies §4 of the #330 design spec as executable assertions.

These tests are the auditable end-of-session check that findajob's
disclosure banner claims hold true. If a test here fails, the disclosure
banner is lying — fix the code, not the test.
"""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import patch

import pytest

REPO = Path(__file__).resolve().parents[1]
GMAIL_IMAP = REPO / "src" / "findajob" / "gmail_imap.py"
SRC_DIR = REPO / "src" / "findajob"

FORBIDDEN_VERBS = ["STORE", "COPY", "EXPUNGE", "APPEND", "MOVE", "CREATE", "DELETE"]


def _strip_comments_and_strings(src: str) -> str:
    """Remove triple-quoted strings, single-line comments, and string literals.

    Crude but sufficient: a forbidden verb appearing only in a docstring
    or comment is fine; the test fires only on real code use.
    """
    src = re.sub(r'""".*?"""', "", src, flags=re.DOTALL)
    src = re.sub(r"'''.*?'''", "", src, flags=re.DOTALL)
    src = re.sub(r"#.*", "", src)
    src = re.sub(r'"[^"]*"', '""', src)
    src = re.sub(r"'[^']*'", "''", src)
    return src


def test_gmail_imap_uses_only_read_verbs():
    src = _strip_comments_and_strings(GMAIL_IMAP.read_text())
    for verb in FORBIDDEN_VERBS:
        assert verb not in src, (
            f"Forbidden IMAP verb {verb!r} found in gmail_imap.py outside "
            f"comments/strings — violates transparency contract §4.1."
        )


def test_gmail_imap_uses_peek_not_body():
    src = GMAIL_IMAP.read_text()
    bodies = re.findall(r"BODY\s*\.\s*PEEK\s*\[|BODY\s*\[", src)
    assert bodies, "No BODY fetch found at all — review gmail_imap.py"
    for hit in bodies:
        assert "PEEK" in hit, "Found BODY[ without PEEK in gmail_imap.py — violates §4.2."


def test_no_smtp_in_codebase():
    """No outbound mail capability anywhere in the package."""
    for py in SRC_DIR.rglob("*.py"):
        text = py.read_text()
        text_no_strings = _strip_comments_and_strings(text)
        assert "import smtplib" not in text_no_strings, (
            f"smtplib import found in {py.relative_to(REPO)} — violates §4.4."
        )
        assert "from smtplib" not in text_no_strings, f"smtplib import found in {py.relative_to(REPO)} — violates §4.4."


def test_app_password_never_logged():
    """Sentinel password must not appear in any log_event call."""
    from findajob import gmail_imap

    sentinel = "ZZZZSENTINELPW01"
    cfg = gmail_imap.GmailConfig(
        address="user@gmail.com",
        app_password=sentinel,
        sender_allowlist=["jobalerts-noreply@linkedin.com"],
        configured_at="2026-04-30T00:00:00Z",
    )

    captured = []

    def fake_log_event(event, **kwargs):
        captured.append((event, kwargs))

    with patch("findajob.gmail_imap.log_event", side_effect=fake_log_event):
        with patch(
            "findajob.gmail_imap.imaplib.IMAP4_SSL",
            side_effect=Exception("network err"),
        ):
            gmail_imap.test_login(cfg)
            gmail_imap.fetch_new_messages(cfg, gmail_imap.GmailState())

    for event, kwargs in captured:
        for v in kwargs.values():
            assert sentinel not in str(v), f"App password leaked into log event {event!r} — violates §4.5."


def test_rejection_scan_app_password_never_logged():
    """The rejection-scan path must also keep the app password out of log events (#362)."""
    from findajob import gmail_imap

    sentinel = "ZZZZSENTINELPW02"
    cfg = gmail_imap.GmailConfig(
        address="user@gmail.com",
        app_password=sentinel,
        sender_allowlist=["jobalerts-noreply@linkedin.com"],
        configured_at="2026-04-30T00:00:00Z",
    )

    captured = []

    def fake_log_event(event, **kwargs):
        captured.append((event, kwargs))

    with patch("findajob.gmail_imap.log_event", side_effect=fake_log_event):
        with patch(
            "findajob.gmail_imap.imaplib.IMAP4_SSL",
            side_effect=Exception("network err"),
        ):
            gmail_imap.fetch_new_messages_for_rejection_scan(cfg, gmail_imap.GmailState())

    for event, kwargs in captured:
        for v in kwargs.values():
            assert sentinel not in str(v), f"App password leaked into log event {event!r} — violates §4.5."


def test_rejection_scan_uses_only_readonly_verbs():
    """Spec §4.4: the rejection-scan path uses the same read-only verb set as
    fetch_new_messages — SELECT (readonly=True), UID SEARCH, UID FETCH(BODY.PEEK[]).
    No STORE / COPY / EXPUNGE / APPEND / MOVE / CREATE / DELETE / SUBSCRIBE.

    This is a runtime complement to ``test_gmail_imap_uses_only_read_verbs``
    (which file-greps for FORBIDDEN_VERBS in source) — it observes the
    actual IMAP client-method invocations made during a scan.
    """
    from unittest.mock import MagicMock

    from findajob import gmail_imap

    cfg = gmail_imap.GmailConfig(
        address="user@gmail.com",
        app_password="abcdefghijklmnop",
        sender_allowlist=[],
        configured_at="2026-04-30T00:00:00Z",
    )

    fake_client = MagicMock()
    fake_client.login.return_value = ("OK", [])
    fake_client.logout.return_value = ("OK", [])

    def select_side_effect(mailbox, **kwargs):
        fake_client.untagged_responses = {"UIDVALIDITY": [b"42"]}
        return ("OK", [b"1234"])

    fake_client.select = MagicMock(side_effect=select_side_effect)
    fake_client.uid = MagicMock(return_value=("OK", [b""]))

    with patch("findajob.gmail_imap.imaplib.IMAP4_SSL", return_value=fake_client):
        gmail_imap.fetch_new_messages_for_rejection_scan(cfg, gmail_imap.GmailState())

    forbidden = {"STORE", "COPY", "EXPUNGE", "APPEND", "MOVE", "CREATE", "DELETE", "SUBSCRIBE"}
    for call in fake_client.uid.call_args_list:
        verb = call.args[0] if call.args else ""
        assert verb not in forbidden, f"Forbidden IMAP verb {verb!r} called from rejection-scan path."

    forbidden_methods = {"store", "copy", "expunge", "append", "move", "create", "delete", "subscribe"}
    for method_name in forbidden_methods:
        method = getattr(fake_client, method_name, None)
        if method is not None and isinstance(method, MagicMock):
            assert not method.called, f"Forbidden IMAP method {method_name!r} called from rejection-scan path."


def test_app_password_never_in_audit_log():
    """gmail_imap must not import write_audit at all."""
    src = GMAIL_IMAP.read_text()
    assert "write_audit" not in src, (
        "gmail_imap.py references write_audit — violates §4.5; credentials must never flow through audit_log."
    )


def test_gmail_creds_in_gitignore():
    gi = (REPO / ".gitignore").read_text()
    assert "config/gmail.json" in gi, "config/gmail.json missing from .gitignore — violates §4.7."
    assert "config/gmail_state.json" in gi, "config/gmail_state.json missing from .gitignore — violates §4.7."


def test_pre_commit_hook_blocks_gmail_creds():
    """If a pre-commit hook is installed, it must reject staged Gmail creds."""
    hook = REPO / ".git" / "hooks" / "pre-commit"
    if not hook.exists():
        pytest.skip("No pre-commit hook installed in this clone")
    text = hook.read_text()
    assert "gmail.json" in text or "gmail_token" in text, (
        "Pre-commit hook does not mention gmail credentials — extend its PATTERNS "
        "array per docs/operations/config-reference.md."
    )
