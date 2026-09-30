"""Tests for findajob.env_file — atomic, owner-only writes of secret-bearing files."""

from __future__ import annotations

import os
import stat
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest

from findajob import env_file


@pytest.fixture(autouse=True)
def _permissive_umask() -> Iterator[None]:
    """Run every test under the container's umask, so a mode that leaks from it shows up."""
    old = os.umask(0o022)
    try:
        yield
    finally:
        os.umask(old)


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


# --- write_private ---------------------------------------------------------


def test_write_private_creates_owner_only_file(tmp_path: Path) -> None:
    dest = tmp_path / "secret.json"
    env_file.write_private(dest, '{"k": "v"}\n')
    assert dest.read_text() == '{"k": "v"}\n'
    assert _mode(dest) == 0o600


def test_write_private_tightens_existing_world_readable_file(tmp_path: Path) -> None:
    dest = tmp_path / "secret.json"
    dest.write_text("old\n")
    dest.chmod(0o644)
    env_file.write_private(dest, "new\n")
    assert dest.read_text() == "new\n"
    assert _mode(dest) == 0o600


def test_write_private_temp_file_is_owner_only_before_replace(tmp_path: Path) -> None:
    """The staged file must be 0600 while it holds the secret, not only after the rename."""
    dest = tmp_path / "secret.json"
    seen: list[int] = []
    real_replace = os.replace

    def spy(src: str, dst: str) -> None:
        seen.append(stat.S_IMODE(os.stat(src).st_mode))
        real_replace(src, dst)

    with patch("findajob.env_file.os.replace", side_effect=spy):
        env_file.write_private(dest, "secret\n")
    assert seen == [0o600]


def test_write_private_leaves_no_temp_file_on_failure(tmp_path: Path) -> None:
    dest = tmp_path / "secret.json"
    dest.write_text("original\n")
    with (
        patch("findajob.env_file.os.replace", side_effect=OSError("disk full")),
        pytest.raises(OSError, match="disk full"),
    ):
        env_file.write_private(dest, "new\n")
    assert dest.read_text() == "original\n"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["secret.json"]


# --- set_vars: file mode ---------------------------------------------------


def test_set_vars_fresh_directory_creates_0600_file(tmp_path: Path) -> None:
    env_path = tmp_path / "data" / ".env"
    env_file.set_vars(env_path, {"FINDAJOB_AUTH_USER": "admin", "FINDAJOB_AUTH_PASS": "hunter2hunter2"})
    assert _mode(env_path) == 0o600
    assert env_path.read_text() == "FINDAJOB_AUTH_USER=admin\nFINDAJOB_AUTH_PASS=hunter2hunter2\n"


def test_set_vars_tightens_existing_0644_file(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("OTHER=1\n")
    env_path.chmod(0o644)
    env_file.set_vars(env_path, {"GEMINI_API_KEY": "abc"})
    assert _mode(env_path) == 0o600


# --- set_vars: value and key validation -----------------------------------


@pytest.mark.parametrize("bad", ["a\nINJECTED=1", "a\rINJECTED=1", "a b", "a\x85b", "a\x00b"])
def test_set_vars_rejects_line_breaks_and_nul(tmp_path: Path, bad: str) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("KEEP=1\n")
    with pytest.raises(ValueError, match="FINDAJOB_AUTH_PASS"):
        env_file.set_vars(env_path, {"FINDAJOB_AUTH_PASS": bad})
    assert env_path.read_text() == "KEEP=1\n"
    assert sorted(p.name for p in tmp_path.iterdir()) == [".env"]


def test_set_vars_validates_every_value_before_writing(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    with pytest.raises(ValueError):
        env_file.set_vars(env_path, {"GOOD": "ok", "BAD": "x\ny"})
    assert not env_path.exists()


@pytest.mark.parametrize("bad_key", ["", "1ABC", "A B", "A=B", "# A", "A\nB"])
def test_set_vars_rejects_invalid_keys(tmp_path: Path, bad_key: str) -> None:
    with pytest.raises(ValueError):
        env_file.set_vars(tmp_path / ".env", {bad_key: "v"})


def test_check_value_accepts_ordinary_secrets() -> None:
    env_file.check_value("K", "p@ss w0rd=with#symbols'\"")


# --- set_vars: merge semantics --------------------------------------------


def test_set_vars_replaces_existing_key_and_keeps_other_lines(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("# header comment\nOPENROUTER_API_KEY=or\n\nGEMINI_API_KEY=old\nNTFY_TOPIC=t\n")
    env_file.set_vars(env_path, {"GEMINI_API_KEY": "new"})
    assert env_path.read_text() == ("# header comment\nOPENROUTER_API_KEY=or\n\nGEMINI_API_KEY=new\nNTFY_TOPIC=t\n")


def test_set_vars_appends_missing_key(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("A=1")  # no trailing newline
    env_file.set_vars(env_path, {"B": "2"})
    assert env_path.read_text() == "A=1\nB=2\n"


def test_set_vars_matches_keys_exactly_and_ignores_comments(tmp_path: Path) -> None:
    """A comment that mentions the key, or a key that shares its prefix, is left alone."""
    env_path = tmp_path / ".env"
    original = (
        "# FINDAJOB_AUTH_PASS is written by the onboarding auth step\n"
        "# FINDAJOB_AUTH_PASS=\n"
        "FINDAJOB_AUTH_PASS_HINT=keep-me\n"
        "FINDAJOB_AUTH_PASS=old\n"
    )
    env_path.write_text(original)
    env_file.set_vars(env_path, {"FINDAJOB_AUTH_PASS": "new-password"})
    assert env_path.read_text() == (
        "# FINDAJOB_AUTH_PASS is written by the onboarding auth step\n"
        "# FINDAJOB_AUTH_PASS=\n"
        "FINDAJOB_AUTH_PASS_HINT=keep-me\n"
        "FINDAJOB_AUTH_PASS=new-password\n"
    )


def test_set_vars_matches_keys_the_way_the_loader_reads_them(tmp_path: Path) -> None:
    """``KEY = value`` with spaces is the same key to paths.load_env, so it is replaced."""
    env_path = tmp_path / ".env"
    env_path.write_text("  GEMINI_API_KEY = old\n")
    env_file.set_vars(env_path, {"GEMINI_API_KEY": "new"})
    assert env_path.read_text() == "GEMINI_API_KEY=new\n"


def test_set_vars_collapses_duplicate_definitions(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("K=1\nOTHER=x\nK=2\n")
    env_file.set_vars(env_path, {"K": "3"})
    assert env_path.read_text() == "K=3\nOTHER=x\n"


def test_set_vars_none_removes_key(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("GEMINI_API_KEY=old\nOTHER=x\n")
    env_file.set_vars(env_path, {"GEMINI_API_KEY": None})
    assert env_path.read_text() == "OTHER=x\n"


def test_set_vars_none_for_absent_key_is_a_no_op_on_content(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("OTHER=x\n")
    env_file.set_vars(env_path, {"GEMINI_API_KEY": None})
    assert env_path.read_text() == "OTHER=x\n"
