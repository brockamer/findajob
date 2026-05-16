"""Tests for findajob.config_loader.load_spend_ceiling (#671)."""

from __future__ import annotations

import warnings
from pathlib import Path

import pytest

from findajob import config_loader
from findajob.config_loader import load_spend_ceiling


@pytest.fixture(autouse=True)
def _point_at_tmp(tmp_path, monkeypatch):
    """Override _SPEND_CEILING_PATH so each test controls its own file."""
    path = tmp_path / "spend_ceiling.txt"
    monkeypatch.setattr(config_loader, "_SPEND_CEILING_PATH", path)
    # Also clear _warned so warning-dedup doesn't mask test assertions
    config_loader._warned.clear()
    yield
    config_loader._warned.clear()


def _write(tmp_path: Path, content: str) -> None:
    (tmp_path / "spend_ceiling.txt").write_text(content, encoding="utf-8")


class TestLoadSpendCeilingDisabled:
    def test_missing_file_returns_none(self, tmp_path):
        # File doesn't exist; _point_at_tmp sets the path but doesn't create the file
        result = load_spend_ceiling()
        assert result is None

    def test_empty_file_returns_none(self, tmp_path):
        _write(tmp_path, "")
        assert load_spend_ceiling() is None

    def test_whitespace_only_returns_none(self, tmp_path):
        _write(tmp_path, "   \n")
        assert load_spend_ceiling() is None

    @pytest.mark.parametrize("sentinel", ["disabled", "DISABLED", "Disabled"])
    def test_disabled_sentinel(self, tmp_path, sentinel):
        _write(tmp_path, sentinel)
        assert load_spend_ceiling() is None

    @pytest.mark.parametrize("sentinel", ["none", "NONE", "None"])
    def test_none_sentinel(self, tmp_path, sentinel):
        _write(tmp_path, sentinel)
        assert load_spend_ceiling() is None

    @pytest.mark.parametrize("sentinel", ["off", "OFF", "Off"])
    def test_off_sentinel(self, tmp_path, sentinel):
        _write(tmp_path, sentinel)
        assert load_spend_ceiling() is None

    def test_zero_string_returns_none(self, tmp_path):
        _write(tmp_path, "0")
        assert load_spend_ceiling() is None

    def test_zero_float_returns_none(self, tmp_path):
        _write(tmp_path, "0.0")
        assert load_spend_ceiling() is None

    def test_negative_returns_none(self, tmp_path):
        _write(tmp_path, "-10.00")
        assert load_spend_ceiling() is None

    def test_malformed_returns_none_and_warns(self, tmp_path):
        _write(tmp_path, "abc")
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = load_spend_ceiling()
        assert result is None
        assert any("unrecognised value" in str(warning.message) for warning in w)


class TestLoadSpendCeilingEnabled:
    def test_float_string_returns_float(self, tmp_path):
        _write(tmp_path, "50.00")
        result = load_spend_ceiling()
        assert result == 50.0
        assert isinstance(result, float)

    def test_integer_shaped_string_returns_float(self, tmp_path):
        _write(tmp_path, "50")
        result = load_spend_ceiling()
        assert result == 50.0
        assert isinstance(result, float)

    def test_small_ceiling(self, tmp_path):
        _write(tmp_path, "0.01")
        assert load_spend_ceiling() == 0.01

    def test_large_ceiling(self, tmp_path):
        _write(tmp_path, "1000.00")
        assert load_spend_ceiling() == 1000.0

    def test_trailing_newline_stripped(self, tmp_path):
        _write(tmp_path, "25.00\n")
        assert load_spend_ceiling() == 25.0
