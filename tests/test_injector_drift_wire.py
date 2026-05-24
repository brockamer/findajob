"""Verify onboarding injector calls detect_and_record after paste-back."""

from pathlib import Path


def test_injector_calls_detect_and_record():
    src = Path(__file__).resolve().parents[1] / "src" / "findajob" / "onboarding" / "injector.py"
    text = src.read_text()
    assert "detect_and_record" in text, "injector must call detect_and_record"


def test_injector_drift_detection_uses_onboarding_changed_by():
    src = Path(__file__).resolve().parents[1] / "src" / "findajob" / "onboarding" / "injector.py"
    text = src.read_text()
    assert 'changed_by="onboarding"' in text
