"""Gmail IMAP client for findajob.

Read-only, app-password authenticated. The only IMAP verbs called are
LOGIN, LIST, SELECT, SEARCH, FETCH (BODY.PEEK[] — does NOT mark messages
read), and LOGOUT. No STORE, COPY, EXPUNGE, APPEND, MOVE, CREATE, DELETE,
or SUBSCRIBE. See docs/superpowers/specs/2026-04-30-330-design.md §4 for
the full transparency contract.
"""

from __future__ import annotations

import imaplib
import json
import os
import socket
import ssl
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from enum import Enum
from pathlib import Path

from findajob.audit import log_event
from findajob.paths import BASE

GMAIL_CONFIG_PATH = f"{BASE}/config/gmail.json"
GMAIL_STATE_PATH = f"{BASE}/config/gmail_state.json"

_SCHEMA_VERSION = 1

# Default ATS sender allowlist for rejection-detection scanning (#362).
# Spec: docs/superpowers/specs/2026-05-01-362-rejection-detection-design.md §3.1.
# Workday-style talent.{company}.com / {company}@myworkday.com senders are
# matched at the rejection_detector layer via suffix; can't be enumerated here.
DEFAULT_REJECTION_ALLOWLIST: tuple[str, ...] = (
    "no-reply@us.greenhouse-mail.io",
    "no-reply@eu.greenhouse-mail.io",
    "no-reply@ashbyhq.com",
    "no-reply@hire.lever.co",
)


@dataclass(frozen=True)
class GmailConfig:
    address: str
    app_password: str
    sender_allowlist: list[str]
    configured_at: str
    rejection_sender_allowlist: list[str] = field(default_factory=lambda: list(DEFAULT_REJECTION_ALLOWLIST))


def _validate_config_payload(payload: dict) -> bool:
    if payload.get("_schema") != _SCHEMA_VERSION:
        return False
    address = payload.get("address", "")
    if not isinstance(address, str) or "@" not in address or len(address) > 254:
        return False
    pw = payload.get("app_password", "")
    if not isinstance(pw, str):
        return False
    pw_stripped = pw.replace(" ", "")
    if len(pw_stripped) != 16 or not pw_stripped.isalnum():
        return False
    senders = payload.get("sender_allowlist", [])
    if not isinstance(senders, list) or len(senders) > 20:
        return False
    if not all(isinstance(s, str) and "@" in s for s in senders):
        return False
    rej_senders = payload.get("rejection_sender_allowlist")
    if rej_senders is not None:
        if not isinstance(rej_senders, list) or len(rej_senders) > 50:
            return False
        if not all(isinstance(s, str) and "@" in s for s in rej_senders):
            return False
    return True


def load_config() -> GmailConfig | None:
    """Return :class:`GmailConfig` from ``config/gmail.json`` or ``None``.

    Returns ``None`` for: missing file, malformed JSON, schema mismatch,
    or any validation failure. Logs a ``gmail_config_invalid`` event on
    validation failure so the operator can debug from pipeline.jsonl.
    """
    p = Path(GMAIL_CONFIG_PATH)
    if not p.exists():
        return None
    try:
        payload = json.loads(p.read_text())
    except json.JSONDecodeError as e:
        log_event("gmail_config_invalid", reason="json", error=str(e))
        return None
    if not _validate_config_payload(payload):
        log_event("gmail_config_invalid", reason="schema_or_validation")
        return None
    return GmailConfig(
        address=payload["address"],
        app_password=payload["app_password"].replace(" ", ""),
        sender_allowlist=list(payload["sender_allowlist"]),
        configured_at=payload["configured_at"],
        rejection_sender_allowlist=list(payload.get("rejection_sender_allowlist", DEFAULT_REJECTION_ALLOWLIST)),
    )


@dataclass(frozen=True)
class GmailState:
    last_uid: int = 0
    last_uidvalidity: int = 0
    auth_failure_streak: int = 0
    last_fetched_at: str | None = None
    last_login_at: str | None = None
    last_error: str | None = None
    rejection_last_uid: int = 0
    rejection_backlog_scan_complete: bool = False
    rejection_backlog_window_days: int = 0


def load_state() -> GmailState:
    """Return :class:`GmailState` or zero-state defaults if missing/unknown."""
    p = Path(GMAIL_STATE_PATH)
    if not p.exists():
        return GmailState()
    try:
        payload = json.loads(p.read_text())
    except json.JSONDecodeError:
        log_event("gmail_state_invalid", reason="json")
        return GmailState()
    if payload.get("_schema") != _SCHEMA_VERSION:
        log_event("gmail_state_invalid", reason="schema")
        return GmailState()
    return GmailState(
        last_uid=int(payload.get("last_uid", 0)),
        last_uidvalidity=int(payload.get("last_uidvalidity", 0)),
        auth_failure_streak=int(payload.get("auth_failure_streak", 0)),
        last_fetched_at=payload.get("last_fetched_at"),
        last_login_at=payload.get("last_login_at"),
        last_error=payload.get("last_error"),
        rejection_last_uid=int(payload.get("rejection_last_uid", 0)),
        rejection_backlog_scan_complete=bool(payload.get("rejection_backlog_scan_complete", False)),
        rejection_backlog_window_days=int(payload.get("rejection_backlog_window_days", 0)),
    )


def save_state(state: GmailState) -> None:
    """Atomically persist :class:`GmailState`."""
    payload = {"_schema": _SCHEMA_VERSION, **asdict(state)}
    p = Path(GMAIL_STATE_PATH)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = f"{GMAIL_STATE_PATH}.tmp"
    with open(tmp_path, "w") as fh:
        json.dump(payload, fh, indent=2)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp_path, GMAIL_STATE_PATH)
    # State is non-secret, but match config posture for consistency.
    os.chmod(GMAIL_STATE_PATH, 0o600)


def save_config(config: GmailConfig) -> None:
    """Atomically persist :class:`GmailConfig` with chmod 600."""
    payload = {
        "_schema": _SCHEMA_VERSION,
        "address": config.address,
        "app_password": config.app_password,
        "sender_allowlist": list(config.sender_allowlist),
        "configured_at": config.configured_at,
        "rejection_sender_allowlist": list(config.rejection_sender_allowlist),
    }
    p = Path(GMAIL_CONFIG_PATH)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = f"{GMAIL_CONFIG_PATH}.tmp"
    with open(tmp_path, "w") as fh:
        json.dump(payload, fh, indent=2)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp_path, GMAIL_CONFIG_PATH)
    os.chmod(GMAIL_CONFIG_PATH, 0o600)


class TestResult(Enum):
    SUCCESS = "success"
    AUTH_FAILED = "auth_failed"
    CONNECTION_ERROR = "connection_error"
    INVALID_CONFIG = "invalid_config"


_AUTH_FAIL_MARKERS = (b"AUTHENTICATIONFAILED", b"Invalid credentials")


def _classify_error(exc: BaseException) -> TestResult:
    """Map an IMAP/network exception to a :class:`TestResult`.

    Authentication failures are bytes-matched against known markers in the
    IMAP error message. Unknown imaplib errors are treated as transient
    (CONNECTION_ERROR) so a single Gmail hiccup never trips the
    auth_failure_streak that drives the user-visible ntfy.
    """
    if isinstance(exc, imaplib.IMAP4.error):
        msg = exc.args[0] if exc.args else b""
        if isinstance(msg, str):
            msg = msg.encode("utf-8", errors="ignore")
        if any(marker in msg for marker in _AUTH_FAIL_MARKERS):
            return TestResult.AUTH_FAILED
        return TestResult.CONNECTION_ERROR
    if isinstance(exc, (socket.timeout, socket.gaierror, ConnectionError, ssl.SSLError, OSError)):
        return TestResult.CONNECTION_ERROR
    return TestResult.CONNECTION_ERROR


def _connect(config: GmailConfig) -> imaplib.IMAP4_SSL:
    """Return an authenticated IMAP4_SSL client.

    Caller MUST invoke ``.logout()`` in a finally block. Uses a 10-second
    socket timeout to bound the Test connection button's worst-case
    response time.

    If ``login()`` raises, the partial socket is cleaned up before re-raising
    so callers never see a half-open client.
    """
    client = imaplib.IMAP4_SSL("imap.gmail.com", 993, timeout=10)
    try:
        client.login(config.address, config.app_password)
    except BaseException:
        try:
            client.logout()
        except Exception:
            pass
        raise
    return client


def test_login(config: GmailConfig) -> TestResult:
    """One-shot LOGIN/LOGOUT against Gmail. Returns the classified result.

    Used by the /config/gmail/test endpoint to surface auth status to the
    user without performing a fetch.
    """
    client = None
    try:
        client = _connect(config)
        return TestResult.SUCCESS
    except BaseException as exc:  # noqa: BLE001 — narrow inside _classify_error
        return _classify_error(exc)
    finally:
        if client is not None:
            try:
                client.logout()
            except Exception:
                pass


@dataclass(frozen=True)
class FetchOutcome:
    result: TestResult
    messages: list[tuple[str, bytes]] = field(default_factory=list)
    new_uid: int | None = None
    new_uidvalidity: int | None = None


def _parse_search_uids(response: list) -> list[int]:
    """imaplib SEARCH returns a list of bytes; parse to a list of ints."""
    if not response:
        return []
    blob = response[0]
    if not blob:
        return []
    if isinstance(blob, bytes):
        return [int(x) for x in blob.split() if x]
    return []


_COLDSTART_WINDOW_DAYS = 30


def fetch_new_messages(config: GmailConfig, state: GmailState, since_days: int | None = None) -> FetchOutcome:
    """Fetch unread-by-us messages from Gmail via incremental UID tracking.

    Behavior:
      - SELECTs INBOX read-only.
      - On UIDVALIDITY mismatch (cold-start: first authorize, or server-side
        mailbox reset): ``SEARCH SINCE <_COLDSTART_WINDOW_DAYS days ago>`` per
        sender, log ``gmail_uidvalidity_reset``. Bounds the initial sync so a
        long-lived inbox with years of LinkedIn / Indeed / ZipRecruiter alerts
        doesn't ingest thousands of stale jobs on first connect.
      - Otherwise: ``SEARCH (UID <last_uid+1>:* FROM "<sender>")`` per sender.
      - ``since_days`` overrides both paths with ``SEARCH SINCE <N days ago>``
        per sender (diagnostic/backfill use). Logs ``gmail_since_override``.
        State is still advanced to the highest UID found so the next
        incremental run picks up only new messages.
      - Fetches via ``BODY.PEEK[]`` so the \\Seen flag is never set.
      - Logs out in finally.
    """
    client: imaplib.IMAP4_SSL | None = None
    try:
        client = _connect(config)
        client.select("INBOX", readonly=True)

        uidvalidity_raw = client.untagged_responses.get("UIDVALIDITY", [b"0"])[0]
        # imaplib types UIDVALIDITY values as bytes | tuple[bytes, bytes], but the
        # IMAP RFC 3501 spec defines UIDVALIDITY as a single number, so the tuple
        # form does not occur in practice. Narrow defensively.
        if isinstance(uidvalidity_raw, tuple):
            uidvalidity_raw = uidvalidity_raw[0]
        current_uidvalidity = int(uidvalidity_raw) if uidvalidity_raw else 0

        override_since_date: str | None = None
        if since_days is not None:
            override_since_date = (datetime.now(UTC) - timedelta(days=since_days)).strftime("%d-%b-%Y")
            log_event("gmail_since_override", days=since_days, since_date=override_since_date)

        cold_start = override_since_date is None and current_uidvalidity != state.last_uidvalidity
        if cold_start:
            log_event(
                "gmail_uidvalidity_reset",
                old=state.last_uidvalidity,
                new=current_uidvalidity,
            )
            since_date = (datetime.now(UTC) - timedelta(days=_COLDSTART_WINDOW_DAYS)).strftime("%d-%b-%Y")

        all_messages: list[tuple[str, bytes]] = []
        seen_uids: set[int] = set()
        max_uid = state.last_uid

        for sender in config.sender_allowlist:
            if override_since_date:
                criteria = f'(SINCE "{override_since_date}" FROM "{sender}")'
            elif cold_start:
                criteria = f'(SINCE "{since_date}" FROM "{sender}")'
            else:
                criteria = f'(UID {state.last_uid + 1}:* FROM "{sender}")'
            typ, search_resp = client.uid("SEARCH", criteria)
            if typ != "OK":
                continue
            uids = _parse_search_uids(search_resp)
            for uid in uids:
                if uid in seen_uids:
                    continue
                seen_uids.add(uid)
                fetch_typ, fetch_resp = client.uid("FETCH", str(uid), "(BODY.PEEK[])")
                if fetch_typ != "OK":
                    continue
                # imaplib FETCH returns [(metadata, raw_bytes), b')'] — first tuple
                for entry in fetch_resp:
                    if isinstance(entry, tuple) and len(entry) >= 2:
                        all_messages.append((sender, entry[1]))
                        if uid > max_uid:
                            max_uid = uid
                        break

        return FetchOutcome(
            result=TestResult.SUCCESS,
            messages=all_messages,
            new_uid=max_uid,
            new_uidvalidity=current_uidvalidity,
        )
    except BaseException as exc:  # noqa: BLE001
        return FetchOutcome(result=_classify_error(exc))
    finally:
        if client is not None:
            try:
                client.logout()
            except Exception:
                pass


def fetch_new_messages_for_rejection_scan(
    config: GmailConfig,
    state: GmailState,
    since_days: int | None = None,
) -> FetchOutcome:
    """Fetch new messages from ``rejection_sender_allowlist`` for classification.

    Read-only IMAP semantics matching :func:`fetch_new_messages` (BODY.PEEK,
    no STORE/COPY/EXPUNGE/APPEND/MOVE/CREATE/DELETE). Maintains a separate
    UID checkpoint at ``state.rejection_last_uid`` so the rejection scan
    cycle is independent of the job-fetch cycle.

    Does NOT do cold-start widening — first-run backlog scan is the
    caller's responsibility (``scripts/detect_rejections.py`` in M-stage 4).
    Pass ``since_days`` for the one-shot first-run backlog scan; logs
    ``rejection_scan_since_override``.

    Spec: docs/superpowers/specs/2026-05-01-362-rejection-detection-design.md §4.4
    """
    client: imaplib.IMAP4_SSL | None = None
    try:
        client = _connect(config)
        client.select("INBOX", readonly=True)

        uidvalidity_raw = client.untagged_responses.get("UIDVALIDITY", [b"0"])[0]
        if isinstance(uidvalidity_raw, tuple):
            uidvalidity_raw = uidvalidity_raw[0]
        current_uidvalidity = int(uidvalidity_raw) if uidvalidity_raw else 0

        override_since_date: str | None = None
        if since_days is not None:
            override_since_date = (datetime.now(UTC) - timedelta(days=since_days)).strftime("%d-%b-%Y")
            log_event("rejection_scan_since_override", days=since_days, since_date=override_since_date)

        all_messages: list[tuple[str, bytes]] = []
        seen_uids: set[int] = set()
        max_uid = state.rejection_last_uid

        for sender in config.rejection_sender_allowlist:
            if override_since_date:
                criteria = f'(SINCE "{override_since_date}" FROM "{sender}")'
            else:
                criteria = f'(UID {state.rejection_last_uid + 1}:* FROM "{sender}")'
            typ, search_resp = client.uid("SEARCH", criteria)
            if typ != "OK":
                continue
            uids = _parse_search_uids(search_resp)
            for uid in uids:
                if uid in seen_uids:
                    continue
                seen_uids.add(uid)
                fetch_typ, fetch_resp = client.uid("FETCH", str(uid), "(BODY.PEEK[])")
                if fetch_typ != "OK":
                    continue
                for entry in fetch_resp:
                    if isinstance(entry, tuple) and len(entry) >= 2:
                        all_messages.append((sender, entry[1]))
                        if uid > max_uid:
                            max_uid = uid
                        log_event("rejection_email_scanned", uid=uid, sender=sender)
                        break

        log_event("rejection_scan_completed", count=len(all_messages), max_uid=max_uid)
        return FetchOutcome(
            result=TestResult.SUCCESS,
            messages=all_messages,
            new_uid=max_uid,
            new_uidvalidity=current_uidvalidity,
        )
    except BaseException as exc:  # noqa: BLE001
        return FetchOutcome(result=_classify_error(exc))
    finally:
        if client is not None:
            try:
                client.logout()
            except Exception:
                pass
