"""Verify triage orchestrator calls detect_and_record before scoring."""

from pathlib import Path


def test_triage_imports_detect_and_record():
    src = Path(__file__).resolve().parents[1] / "src" / "findajob" / "triage" / "orchestrator.py"
    text = src.read_text()
    assert "from findajob.metrics.config_changes import detect_and_record" in text


def test_triage_calls_detect_and_record_before_scoring():
    src = Path(__file__).resolve().parents[1] / "src" / "findajob" / "triage" / "orchestrator.py"
    text = src.read_text()
    call_idx = text.find("detect_and_record(")
    score_idx = text.find("scored_count")
    assert call_idx > 0, "detect_and_record call not found"
    assert call_idx < score_idx, "detect_and_record must be called before scoring loop"
