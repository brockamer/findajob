"""Tests for findajob.web.auth_env.write_auth_credentials — the first data/.env writer on a fresh install."""

from __future__ import annotations

import os
import stat
from collections.abc import Iterator
from pathlib import Path

import pytest

from findajob.web.auth_env import write_auth_credentials


@pytest.fixture(autouse=True)
def _isolate() -> Iterator[None]:
    """write_auth_credentials sets os.environ directly; clear it on both sides of each test.

    monkeypatch.delenv on an unset variable records nothing to undo, so it would
    leak the values this module's tests set into every later test.
    """
    for key in ("FINDAJOB_AUTH_USER", "FINDAJOB_AUTH_PASS"):
        os.environ.pop(key, None)
    old = os.umask(0o022)  # the container's umask
    try:
        yield
    finally:
        os.umask(old)
        for key in ("FINDAJOB_AUTH_USER", "FINDAJOB_AUTH_PASS"):
            os.environ.pop(key, None)


def test_credentials_written_to_fresh_directory_are_owner_only(tmp_path: Path) -> None:
    (tmp_path / "data").mkdir()
    write_auth_credentials(tmp_path, "admin", "correct-horse-battery")
    env_path = tmp_path / "data" / ".env"
    assert stat.S_IMODE(env_path.stat().st_mode) == 0o600
    assert env_path.read_text() == "FINDAJOB_AUTH_USER=admin\nFINDAJOB_AUTH_PASS=correct-horse-battery\n"


@pytest.mark.parametrize("field", ["username", "password"])
def test_newline_in_credential_raises_and_writes_nothing(tmp_path: Path, field: str) -> None:
    (tmp_path / "data").mkdir()
    creds = {"username": "admin", "password": "correct-horse-battery"}
    creds[field] = "abc\nOPENROUTER_API_KEY=attacker"
    with pytest.raises(ValueError):
        write_auth_credentials(tmp_path, creds["username"], creds["password"])
    assert not (tmp_path / "data" / ".env").exists()
    assert "FINDAJOB_AUTH_USER" not in os.environ
    assert "FINDAJOB_AUTH_PASS" not in os.environ


def test_comment_mentioning_auth_key_is_not_rewritten(tmp_path: Path) -> None:
    env_path = tmp_path / "data" / ".env"
    env_path.parent.mkdir()
    env_path.write_text("# FINDAJOB_AUTH_PASS is set by onboarding\nOPENROUTER_API_KEY=sk-x\n")
    write_auth_credentials(tmp_path, "admin", "correct-horse-battery")
    assert env_path.read_text() == (
        "# FINDAJOB_AUTH_PASS is set by onboarding\n"
        "OPENROUTER_API_KEY=sk-x\n"
        "FINDAJOB_AUTH_USER=admin\n"
        "FINDAJOB_AUTH_PASS=correct-horse-battery\n"
    )
