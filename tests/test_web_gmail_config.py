"""Tier 4 — route smoke tests for /config/gmail/{,save,test,disconnect}."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from findajob import gmail_imap
from findajob.onboarding import mark_complete
from findajob.web.app import create_app


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setattr(gmail_imap, "GMAIL_CONFIG_PATH", str(tmp_path / "gmail.json"))
    monkeypatch.setattr(gmail_imap, "GMAIL_STATE_PATH", str(tmp_path / "gmail_state.json"))
    db = tmp_path / "pipeline.db"
    db.touch()
    companies = tmp_path / "companies"
    companies.mkdir()
    mark_complete(tmp_path)
    return create_app(companies_root=companies, db_path=db, base_root=tmp_path)


@pytest.fixture
def client(app):
    return TestClient(app)


def _write_config():
    Path(gmail_imap.GMAIL_CONFIG_PATH).write_text(
        json.dumps(
            {
                "_schema": 1,
                "address": "user@gmail.com",
                "app_password": "abcdefghijklmnop",
                "sender_allowlist": ["jobalerts-noreply@linkedin.com"],
                "configured_at": "2026-04-30T00:00:00Z",
            }
        )
    )


def _write_state(**overrides):
    payload = {
        "_schema": 1,
        "last_uid": 100,
        "last_uidvalidity": 67890,
        "auth_failure_streak": 0,
        "last_fetched_at": "2026-04-30T00:00:00Z",
        "last_login_at": "2026-04-30T00:00:00Z",
        "last_error": None,
    }
    payload.update(overrides)
    Path(gmail_imap.GMAIL_STATE_PATH).write_text(json.dumps(payload))


def test_get_config_gmail_renders_off_state(client):
    r = client.get("/config/gmail/")
    assert r.status_code == 200
    assert "Off" in r.text


def test_get_config_gmail_renders_authorized_state(client):
    _write_config()
    _write_state()
    r = client.get("/config/gmail/")
    assert r.status_code == 200
    assert "Authorized" in r.text


def test_post_save_writes_config_file(client):
    r = client.post(
        "/config/gmail/save",
        data={
            "address": "user@gmail.com",
            "app_password": "abcd efgh ijkl mnop",
            "sender_allowlist": "jobalerts-noreply@linkedin.com",
        },
    )
    assert r.status_code == 200
    cfg = gmail_imap.load_config()
    assert cfg is not None
    assert cfg.address == "user@gmail.com"
    assert cfg.app_password == "abcdefghijklmnop"


def test_post_save_rejects_invalid_password_length(client):
    r = client.post(
        "/config/gmail/save",
        data={
            "address": "user@gmail.com",
            "app_password": "short",
            "sender_allowlist": "jobalerts-noreply@linkedin.com",
        },
    )
    assert r.status_code in (200, 400)
    assert "16 characters" in r.text or "App password" in r.text
    assert gmail_imap.load_config() is None


def test_post_test_connection_success_updates_pill(client):
    _write_config()
    with patch(
        "findajob.gmail_imap.test_login",
        return_value=gmail_imap.TestResult.SUCCESS,
    ):
        r = client.post("/config/gmail/test")
    assert r.status_code == 200
    assert "Authorized" in r.text


def test_post_test_connection_auth_failed_updates_pill(client):
    _write_config()
    with patch(
        "findajob.gmail_imap.test_login",
        return_value=gmail_imap.TestResult.AUTH_FAILED,
    ):
        r = client.post("/config/gmail/test")
    assert r.status_code == 200
    assert "Login failed" in r.text


def test_post_disconnect_wipes_both_files(client):
    _write_config()
    _write_state()
    r = client.post("/config/gmail/disconnect")
    assert r.status_code == 200
    assert not Path(gmail_imap.GMAIL_CONFIG_PATH).exists()
    assert not Path(gmail_imap.GMAIL_STATE_PATH).exists()


def test_disclosure_banner_present_on_get(client):
    r = client.get("/config/gmail/")
    assert "What findajob does" in r.text
    assert "<details" in r.text


def test_audit_links_pin_to_build_sha(client, monkeypatch):
    monkeypatch.setenv("FINDAJOB_BUILD_SHA", "abc1234567")
    import importlib

    from findajob.web import constants

    importlib.reload(constants)
    r = client.get("/config/gmail/")
    assert "/blob/abc1234567/" in r.text
    assert "/blob/main/" not in r.text
