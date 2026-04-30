"""Verify BUILD_SHA constants resolve from env vars correctly."""

import importlib

from findajob.web import constants


def test_build_sha_resolves_from_env(monkeypatch):
    monkeypatch.setenv("FINDAJOB_BUILD_SHA", "abc123def4567890")
    importlib.reload(constants)
    assert constants.BUILD_SHA == "abc123def4567890"
    assert constants.BUILD_SHA_SHORT == "abc123d"


def test_build_sha_defaults_to_main(monkeypatch):
    monkeypatch.delenv("FINDAJOB_BUILD_SHA", raising=False)
    importlib.reload(constants)
    assert constants.BUILD_SHA == "main"
    assert constants.BUILD_SHA_SHORT == "main"


def test_github_blob_url_pins_to_sha(monkeypatch):
    monkeypatch.setenv("FINDAJOB_BUILD_SHA", "abc123def4567890")
    importlib.reload(constants)
    url = constants.github_blob_url("src/findajob/gmail_imap.py")
    assert "/blob/abc123def4567890/" in url
    assert url.endswith("src/findajob/gmail_imap.py")
