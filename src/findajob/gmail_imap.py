"""Gmail IMAP client for findajob.

Read-only, app-password authenticated. The only IMAP verbs called are
LOGIN, LIST, SELECT, SEARCH, FETCH (BODY.PEEK[] — does NOT mark messages
read), and LOGOUT. No STORE, COPY, EXPUNGE, APPEND, MOVE, CREATE, DELETE,
or SUBSCRIBE. See docs/superpowers/specs/2026-04-30-330-design.md §4 for
the full transparency contract.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

from findajob.paths import BASE
from findajob.utils import log_event

GMAIL_CONFIG_PATH = f"{BASE}/config/gmail.json"
GMAIL_STATE_PATH = f"{BASE}/config/gmail_state.json"

_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class GmailConfig:
    address: str
    app_password: str
    sender_allowlist: list[str]
    configured_at: str


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
    )


@dataclass(frozen=True)
class GmailState:
    last_uid: int = 0
    last_uidvalidity: int = 0
    auth_failure_streak: int = 0
    last_fetched_at: str | None = None
    last_login_at: str | None = None
    last_error: str | None = None


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
