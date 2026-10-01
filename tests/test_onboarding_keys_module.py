"""Tests for findajob.onboarding.keys — the one writer and reader of the API keys."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from findajob.onboarding.keys import current_keys, save_keys


@pytest.fixture
def base(tmp_path: Path) -> Path:
    (tmp_path / "data").mkdir()
    return tmp_path


def _env_file(base: Path) -> Path:
    return base / "data" / ".env"


def test_current_keys_empty_when_unset() -> None:
    # Also checks the autouse _isolate_api_key_env fixture: a developer shell
    # that exports these keys must not reach the tests.
    assert current_keys() == ("", "", "")


def test_current_keys_treats_placeholder_openrouter_value_as_unset() -> None:
    # data/.env.example ships OPENROUTER_API_KEY=your_key_here and the Docker
    # install seeds data/.env from it, so the web process can start with it.
    os.environ["OPENROUTER_API_KEY"] = "your_key_here"
    assert current_keys()[0] == ""


def test_current_keys_returns_well_formed_openrouter_value_unchanged() -> None:
    os.environ["OPENROUTER_API_KEY"] = "sk-or-v1-abcdef123456"
    assert current_keys()[0] == "sk-or-v1-abcdef123456"


def test_save_creates_env_file_0600_and_sets_environ(base: Path) -> None:
    assert not _env_file(base).exists()
    save_keys(base, openrouter_api_key=" sk-or-v1-abc ", rapidapi_key="rapid123", gemini_api_key="gem456")
    text = _env_file(base).read_text()
    assert "OPENROUTER_API_KEY=sk-or-v1-abc\n" in text
    assert "RAPIDAPI_KEY=rapid123\n" in text
    assert "GEMINI_API_KEY=gem456\n" in text
    assert stat.S_IMODE(_env_file(base).stat().st_mode) == 0o600
    assert current_keys() == ("sk-or-v1-abc", "rapid123", "gem456")


def test_blank_optional_keeps_saved_value(base: Path) -> None:
    _env_file(base).write_text("GEMINI_API_KEY=old-gem\nOTHER=x\n")
    os.environ["GEMINI_API_KEY"] = "old-gem"
    save_keys(base, openrouter_api_key="sk-or-v1-new", rapidapi_key="", gemini_api_key="   ")
    text = _env_file(base).read_text()
    assert "GEMINI_API_KEY=old-gem\n" in text
    assert "OTHER=x\n" in text
    assert "RAPIDAPI_KEY" not in text
    assert current_keys() == ("sk-or-v1-new", "", "old-gem")


def test_save_replaces_existing_key_in_file_and_environ(base: Path) -> None:
    _env_file(base).write_text("OPENROUTER_API_KEY=sk-or-v1-old\n")
    os.environ["OPENROUTER_API_KEY"] = "sk-or-v1-old"
    save_keys(base, openrouter_api_key="sk-or-v1-new")
    text = _env_file(base).read_text()
    assert text.count("OPENROUTER_API_KEY=") == 1
    assert "OPENROUTER_API_KEY=sk-or-v1-new\n" in text
    assert current_keys()[0] == "sk-or-v1-new"


def test_line_break_in_value_rejected_without_change(base: Path) -> None:
    _env_file(base).write_text("OPENROUTER_API_KEY=sk-or-v1-old\n")
    os.environ["OPENROUTER_API_KEY"] = "sk-or-v1-old"
    with pytest.raises(ValueError):
        save_keys(base, openrouter_api_key="sk-or-v1-new", gemini_api_key="gem\nINJECTED=1")
    assert _env_file(base).read_text() == "OPENROUTER_API_KEY=sk-or-v1-old\n"
    assert current_keys() == ("sk-or-v1-old", "", "")


def test_blank_openrouter_rejected(base: Path) -> None:
    with pytest.raises(ValueError):
        save_keys(base, openrouter_api_key="  ")
    assert not _env_file(base).exists()
