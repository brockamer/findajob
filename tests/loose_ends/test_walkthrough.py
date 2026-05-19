"""Tests for src/findajob/loose_ends/walkthrough.py."""

from __future__ import annotations

import json
from pathlib import Path

from findajob.loose_ends.walkthrough import Finding, read_findings, write_finding


def test_finding_is_frozen_dataclass():
    """Finding instances are immutable (matches phase 1's idiom)."""
    f = Finding(
        persona="nux_user",
        walkthrough_name="dashboard_first_load",
        current_url="/board/dashboard",
        category=3,
        is_loose_end=True,
        confidence="high",
        rationale="Empty state with no CTA.",
        suggested_surface="Add a CTA",
        excluded=False,
        exclusion_key=None,
    )
    try:
        f.confidence = "low"  # type: ignore[misc]
    except Exception as e:
        # FrozenInstanceError message varies by Python version; check class name or message
        assert "frozen" in type(e).__name__.lower() or "frozen" in str(e).lower()
    else:
        raise AssertionError("Finding should be frozen")


def test_findings_roundtrip_jsonl(tmp_path: Path):
    """write_finding + read_findings roundtrip preserves all fields."""
    target = tmp_path / "findings.jsonl"
    f1 = Finding(
        persona="nux_user",
        walkthrough_name="dashboard_first_load",
        current_url="/board/dashboard",
        category=3,
        is_loose_end=True,
        confidence="high",
        rationale="Empty.",
        suggested_surface="CTA",
        excluded=False,
        exclusion_key=None,
    )
    f2 = Finding(
        persona="established_user",
        walkthrough_name="applied_undo",
        current_url="/board/applied",
        category=2,
        is_loose_end=False,
        confidence="low",
        rationale="Has exit.",
        suggested_surface="",
        excluded=True,
        exclusion_key="established_user::/board/applied::flow_without_exit",
    )
    write_finding(target, f1)
    write_finding(target, f2)
    out = read_findings(target)
    assert out == [f1, f2]


def test_write_finding_appends_one_jsonl_line_per_call(tmp_path: Path):
    target = tmp_path / "findings.jsonl"
    f = Finding(
        persona="nux_user",
        walkthrough_name="dashboard_first_load",
        current_url="/board/dashboard",
        category=3,
        is_loose_end=True,
        confidence="high",
        rationale="Empty.",
        suggested_surface="",
        excluded=False,
        exclusion_key=None,
    )
    write_finding(target, f)
    write_finding(target, f)
    lines = target.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    for line in lines:
        json.loads(line)  # each line is valid JSON
