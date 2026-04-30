"""Unit tests for src/findajob/gmail_imap.py — Tier 2 of the test plan."""

from __future__ import annotations

import json
import os
import stat
from unittest.mock import patch

import pytest

from findajob import gmail_imap


@pytest.fixture
def cfg_path(tmp_path, monkeypatch):
    p = tmp_path / "gmail.json"
    monkeypatch.setattr(gmail_imap, "GMAIL_CONFIG_PATH", str(p))
    return p


def test_load_config_missing_file_returns_none(cfg_path):
    assert not cfg_path.exists()
    assert gmail_imap.load_config() is None


def test_load_config_strips_password_spaces(cfg_path):
    cfg_path.write_text(
        json.dumps(
            {
                "_schema": 1,
                "address": "user@gmail.com",
                "app_password": "abcd efgh ijkl mnop",
                "sender_allowlist": ["jobalerts-noreply@linkedin.com"],
                "configured_at": "2026-04-30T00:00:00Z",
            }
        )
    )
    cfg = gmail_imap.load_config()
    assert cfg is not None
    assert cfg.app_password == "abcdefghijklmnop"
    assert len(cfg.app_password) == 16


def test_load_config_rejects_wrong_password_length(cfg_path):
    cfg_path.write_text(
        json.dumps(
            {
                "_schema": 1,
                "address": "user@gmail.com",
                "app_password": "abcdefghijklmno",  # 15 chars
                "sender_allowlist": ["jobalerts-noreply@linkedin.com"],
                "configured_at": "2026-04-30T00:00:00Z",
            }
        )
    )
    assert gmail_imap.load_config() is None


def test_load_config_rejects_invalid_email(cfg_path):
    cfg_path.write_text(
        json.dumps(
            {
                "_schema": 1,
                "address": "not-an-email",
                "app_password": "abcdefghijklmnop",
                "sender_allowlist": ["jobalerts-noreply@linkedin.com"],
                "configured_at": "2026-04-30T00:00:00Z",
            }
        )
    )
    assert gmail_imap.load_config() is None


def test_load_config_rejects_unknown_schema_version(cfg_path):
    cfg_path.write_text(
        json.dumps(
            {
                "_schema": 99,
                "address": "user@gmail.com",
                "app_password": "abcdefghijklmnop",
                "sender_allowlist": ["jobalerts-noreply@linkedin.com"],
                "configured_at": "2026-04-30T00:00:00Z",
            }
        )
    )
    assert gmail_imap.load_config() is None


def test_save_config_writes_atomically_and_chmod_600(cfg_path):
    cfg = gmail_imap.GmailConfig(
        address="user@gmail.com",
        app_password="abcdefghijklmnop",
        sender_allowlist=["jobalerts-noreply@linkedin.com"],
        configured_at="2026-04-30T00:00:00Z",
    )
    gmail_imap.save_config(cfg)
    assert cfg_path.exists()
    mode = stat.S_IMODE(cfg_path.stat().st_mode)
    assert mode == 0o600
    payload = json.loads(cfg_path.read_text())
    assert payload["_schema"] == 1
    assert payload["address"] == "user@gmail.com"
    assert payload["app_password"] == "abcdefghijklmnop"


def test_save_config_uses_temp_then_rename(cfg_path):
    """Save must go through .tmp + os.replace, never a direct overwrite."""
    cfg_path.write_text("{}")  # pre-existing
    cfg = gmail_imap.GmailConfig(
        address="user@gmail.com",
        app_password="abcdefghijklmnop",
        sender_allowlist=["jobalerts-noreply@linkedin.com"],
        configured_at="2026-04-30T00:00:00Z",
    )
    with patch("findajob.gmail_imap.os.replace", wraps=os.replace) as m:
        gmail_imap.save_config(cfg)
    m.assert_called_once()
    src, dst = m.call_args.args
    assert src.endswith(".tmp")
    assert dst == str(cfg_path)
