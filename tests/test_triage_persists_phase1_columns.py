"""Verify triage UPDATE includes scored_by + company_tier columns."""

from pathlib import Path


def _scoring_update_block() -> str:
    """Return the scoring UPDATE block from the orchestrator source."""
    src = Path(__file__).resolve().parents[1] / "src" / "findajob" / "triage" / "orchestrator.py"
    text = src.read_text()
    # The scoring UPDATE is the one that sets relevance_score — unique to this block.
    idx = text.find("relevance_score=?")
    assert idx > 0, "could not find scoring UPDATE block (relevance_score=? not found)"
    # Walk back to the UPDATE keyword
    start = text.rfind("UPDATE jobs SET", 0, idx)
    assert start > 0, "could not find UPDATE jobs SET before relevance_score=?"
    return text[start : start + 600]


def test_triage_update_includes_scored_by():
    block = _scoring_update_block()
    assert "scored_by=?" in block, "scored_by not in triage scoring UPDATE"


def test_triage_update_includes_company_tier():
    block = _scoring_update_block()
    assert "company_tier=?" in block, "company_tier not in triage scoring UPDATE"
