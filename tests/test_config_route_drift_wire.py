"""Verify /config/ POST triggers drift detection."""

from pathlib import Path


def test_config_save_calls_detect_and_record():
    src = Path(__file__).resolve().parents[1] / "src" / "findajob" / "web" / "routes" / "config.py"
    text = src.read_text()
    assert "detect_and_record" in text, "/config/ POST must call detect_and_record"
