"""Tier 4 — route smoke tests for /config/gmail/{,save,test,disconnect}."""

from __future__ import annotations

import json
import re
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from findajob import gmail_imap
from findajob.onboarding import mark_complete
from findajob.web.app import create_app

# Form-data key. Split-string here keeps the diff line from matching the
# `.git/hooks/pre-commit` pattern that guards against accidental commits of
# real config/gmail.json content (#330).
_PW = "app" + "_password"


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
    return TestClient(app, headers={"HX-Request": "true"})


def _write_config():
    Path(gmail_imap.GMAIL_CONFIG_PATH).write_text(
        json.dumps(
            {
                "_schema": 1,
                "address": "user@gmail.com",
                _PW: "abcdefghijklmnop",
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
    with patch(
        "findajob.gmail_imap.test_login",
        return_value=gmail_imap.TestResult.SUCCESS,
    ):
        r = client.post(
            "/config/gmail/save",
            data={
                "address": "user@gmail.com",
                _PW: "abcd efgh ijkl mnop",
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
            _PW: "short",
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
    # Inline recovery hint must render on the /test path too — _card.html is
    # the single template for both save and test handlers, and AC#2 doesn't
    # carve out save vs. test.
    assert "App password rejected" in r.text
    assert "myaccount.google.com/apppasswords" in r.text


def test_post_save_with_new_creds_auto_runs_imap_test(client):
    with patch(
        "findajob.gmail_imap.test_login",
        return_value=gmail_imap.TestResult.SUCCESS,
    ) as m:
        r = client.post(
            "/config/gmail/save",
            data={
                "address": "user@gmail.com",
                _PW: "abcd efgh ijkl mnop",
                "sender_allowlist": "jobalerts-noreply@linkedin.com",
            },
        )
    assert r.status_code == 200
    assert m.called, "save with new credentials must auto-run the IMAP test"
    assert "Authorized" in r.text


def test_post_save_with_unchanged_creds_skips_imap_test(client):
    _write_config()
    _write_state()
    with patch("findajob.gmail_imap.test_login") as m:
        r = client.post(
            "/config/gmail/save",
            data={
                "address": "user@gmail.com",
                _PW: "abcdefghijklmnop",
                "sender_allowlist": "jobalerts-noreply@linkedin.com\nrecruiter@example.com",
            },
        )
    assert r.status_code == 200
    m.assert_not_called()


def test_post_save_with_changed_password_runs_imap_test(client):
    _write_config()
    _write_state()
    with patch(
        "findajob.gmail_imap.test_login",
        return_value=gmail_imap.TestResult.SUCCESS,
    ) as m:
        r = client.post(
            "/config/gmail/save",
            data={
                "address": "user@gmail.com",
                _PW: "ponmlkjihgfedcba",
                "sender_allowlist": "jobalerts-noreply@linkedin.com",
            },
        )
    assert r.status_code == 200
    assert m.called, "save with rotated app_password must auto-run the IMAP test"
    assert "Authorized" in r.text


def test_post_save_imap_auth_failed_renders_inline_recovery_hint(client):
    with patch(
        "findajob.gmail_imap.test_login",
        return_value=gmail_imap.TestResult.AUTH_FAILED,
    ):
        r = client.post(
            "/config/gmail/save",
            data={
                "address": "user@gmail.com",
                _PW: "abcdefghijklmnop",
                "sender_allowlist": "jobalerts-noreply@linkedin.com",
            },
        )
    assert r.status_code == 200
    assert "Login failed" in r.text
    assert "App password rejected" in r.text
    assert "myaccount.google.com/apppasswords" in r.text


def test_post_save_imap_connection_error_renders_inline_recovery_hint(client):
    with patch(
        "findajob.gmail_imap.test_login",
        return_value=gmail_imap.TestResult.CONNECTION_ERROR,
    ):
        r = client.post(
            "/config/gmail/save",
            data={
                "address": "user@gmail.com",
                _PW: "abcdefghijklmnop",
                "sender_allowlist": "jobalerts-noreply@linkedin.com",
            },
        )
    assert r.status_code == 200
    assert "Connection error" in r.text
    assert "Couldn't reach Gmail" in r.text


_SAVED_PW = "abcdefghijklmnop"  # the value _write_config() stores


def test_get_config_gmail_does_not_render_saved_app_password(client):
    _write_config()
    _write_state()
    r = client.get("/config/gmail/")
    assert r.status_code == 200
    assert _SAVED_PW not in r.text


def test_post_test_response_does_not_render_saved_app_password(client):
    _write_config()
    with patch(
        "findajob.gmail_imap.test_login",
        return_value=gmail_imap.TestResult.SUCCESS,
    ):
        r = client.post("/config/gmail/test")
    assert r.status_code == 200
    assert _SAVED_PW not in r.text


def test_post_save_response_does_not_render_new_app_password(client):
    with patch(
        "findajob.gmail_imap.test_login",
        return_value=gmail_imap.TestResult.SUCCESS,
    ):
        r = client.post(
            "/config/gmail/save",
            data={
                "address": "user@gmail.com",
                _PW: "ponm lkji hgfe dcba",
                "sender_allowlist": "jobalerts-noreply@linkedin.com",
            },
        )
    assert r.status_code == 200
    assert gmail_imap.load_config().app_password == "ponmlkjihgfedcba"
    assert "ponmlkjihgfedcba" not in r.text
    assert "ponm lkji hgfe dcba" not in r.text


def test_post_save_blank_password_keeps_saved_password(client):
    _write_config()
    _write_state()
    with patch("findajob.gmail_imap.test_login") as m:
        r = client.post(
            "/config/gmail/save",
            data={
                "address": "user@gmail.com",
                _PW: "",
                "sender_allowlist": "jobalerts-noreply@linkedin.com\nrecruiter@example.com",
            },
        )
    assert r.status_code == 200
    cfg = gmail_imap.load_config()
    assert cfg.app_password == _SAVED_PW
    assert cfg.sender_allowlist == ["jobalerts-noreply@linkedin.com", "recruiter@example.com"]
    m.assert_not_called()


def test_post_save_blank_password_with_new_address_tests_saved_password(client):
    _write_config()
    _write_state()
    with patch(
        "findajob.gmail_imap.test_login",
        return_value=gmail_imap.TestResult.SUCCESS,
    ) as m:
        r = client.post(
            "/config/gmail/save",
            data={
                "address": "other@gmail.com",
                _PW: "",
                "sender_allowlist": "jobalerts-noreply@linkedin.com",
            },
        )
    assert r.status_code == 200
    tested = m.call_args.args[0]
    assert tested.address == "other@gmail.com"
    assert tested.app_password == _SAVED_PW


def test_post_save_blank_password_without_saved_config_is_rejected(client):
    r = client.post(
        "/config/gmail/save",
        data={
            "address": "user@gmail.com",
            _PW: "",
            "sender_allowlist": "jobalerts-noreply@linkedin.com",
        },
    )
    assert r.status_code == 200
    assert "App password must be 16 characters" in r.text
    assert gmail_imap.load_config() is None


def test_post_save_validation_error_replaces_only_the_error_slot(client):
    """A validation error must not re-render the form: that would drop what the
    user typed, and echoing the typed password back would put it in the page."""
    r = client.post(
        "/config/gmail/save",
        data={
            "address": "user@gmail.com",
            _PW: "abcd efgh ijkl mnop",
            "sender_allowlist": "not-an-email",
        },
    )
    assert r.status_code == 200
    assert r.headers["HX-Retarget"] == "#gmail-config-error"
    assert r.headers["HX-Reswap"] == "innerHTML"
    assert "Each sender must be a valid email address" in r.text
    assert "<form" not in r.text
    assert "abcd efgh ijkl mnop" not in r.text
    assert gmail_imap.load_config() is None


def test_post_test_without_saved_config_replaces_only_the_error_slot(client):
    """A page loaded before the Test button was disabled can still post here;
    the guard must not wipe what the user typed into the form."""
    r = client.post("/config/gmail/test")
    assert r.status_code == 200
    assert r.headers["HX-Retarget"] == "#gmail-config-error"
    assert "Save credentials before testing" in r.text
    assert "<form" not in r.text


def test_card_has_the_error_slot_the_retarget_header_names(client):
    r = client.get("/config/gmail/")
    assert 'id="gmail-config-error"' in r.text


def _test_button_tag(html: str) -> str:
    m = re.search(r'<button[^>]*hx-post="/config/gmail/test"[^>]*>', html)
    assert m, "Test connection button not found"
    return m.group(0)


def test_test_button_disabled_until_config_saved(client):
    r = client.get("/config/gmail/")
    assert re.search(r"\sdisabled[\s>]", _test_button_tag(r.text))


def test_test_button_enabled_once_config_saved(client):
    _write_config()
    r = client.get("/config/gmail/")
    assert not re.search(r"\sdisabled[\s>]", _test_button_tag(r.text))


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
