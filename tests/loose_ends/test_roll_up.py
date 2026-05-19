"""Tests for src/findajob/loose_ends/roll_up.py."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

from findajob.loose_ends.finding import Finding, write_finding
from findajob.loose_ends.roll_up import (
    aggregate_findings,
    exclusions_fired,
    write_report,
)


def _make_finding(**kw):
    defaults = dict(
        persona="nux_user",
        walkthrough_name="dashboard_first_load",
        current_url="/board/dashboard",
        category=3,
        is_loose_end=True,
        confidence="high",
        rationale="...",
        suggested_surface="",
        excluded=False,
        exclusion_key=None,
    )
    defaults.update(kw)
    return Finding(**defaults)


def test_aggregate_groups_by_confidence_and_persona():
    findings = [
        _make_finding(confidence="high", persona="nux_user"),
        _make_finding(confidence="medium", persona="nux_user"),
        _make_finding(confidence="high", persona="established_user"),
        _make_finding(confidence="low", persona="established_user"),
        _make_finding(confidence="high", persona="nux_user", excluded=True),  # filtered out
    ]
    agg = aggregate_findings(findings)
    assert set(agg.keys()) == {"high", "medium", "low"}
    assert len(agg["high"]) == 2  # excluded filtered out
    assert len(agg["medium"]) == 1
    assert len(agg["low"]) == 1


def test_aggregate_drops_is_loose_end_false():
    findings = [
        _make_finding(confidence="high", is_loose_end=True),
        _make_finding(confidence="high", is_loose_end=False),  # not a loose end at all
    ]
    agg = aggregate_findings(findings)
    assert len(agg["high"]) == 1


def test_exclusions_fired_counts_keys_used():
    findings = [
        _make_finding(excluded=True, exclusion_key="established_user::/admin/stacks/::flow_without_exit"),
        _make_finding(excluded=True, exclusion_key="established_user::/admin/stacks/::flow_without_exit"),
        _make_finding(excluded=True, exclusion_key="nux_user::/board/applied::empty_state_no_guidance"),
        _make_finding(excluded=False),  # not excluded
    ]
    exclusions_yaml = {
        "established_user::/admin/stacks/::flow_without_exit": "Operator-only.",
        "nux_user::/board/applied::empty_state_no_guidance": "Correctly empty for NUX.",
        "established_user::/board/dashboard::empty_state_no_guidance": "Never fires.",
    }
    fired = exclusions_fired(findings=findings, exclusions=exclusions_yaml)
    assert len(fired) == 2  # only the two that matched at least one finding
    keys = {key for key, _ in fired}
    assert "established_user::/board/dashboard::empty_state_no_guidance" not in keys


def test_write_report_renders_both_sections(tmp_path: Path):
    findings = [
        _make_finding(confidence="high", rationale="Empty dashboard, no CTA."),
    ]
    exclusions_yaml = {
        "nux_user::/board/applied::empty_state_no_guidance": "Correctly empty for NUX.",
    }
    findings_jsonl = tmp_path / "findings.jsonl"
    for f in findings:
        write_finding(findings_jsonl, f)
    findings.append(
        _make_finding(
            confidence="low",
            is_loose_end=False,
            excluded=True,
            exclusion_key="nux_user::/board/applied::empty_state_no_guidance",
        )
    )
    for f in findings[1:]:
        write_finding(findings_jsonl, f)

    fake_llm = MagicMock()
    fake_llm.text = "### High\n\n- [nux_user] x — Empty.\n\n### Medium\n\n_None._\n\n### Low\n\n_None._"
    fake_llm.cost_usd = 0.01
    with patch("findajob.loose_ends.roll_up.openrouter.complete", return_value=fake_llm):
        report_path, prose_cost = write_report(
            findings_jsonl=findings_jsonl,
            exclusions=exclusions_yaml,
            output_dir=tmp_path,
            today=date(2026, 5, 19),
        )
    body = report_path.read_text(encoding="utf-8")
    assert "# Dynamic UX walkthrough audit — 2026-05-19" in body
    assert "## Findings" in body
    assert "## Exclusions fired this run" in body
    assert "Correctly empty for NUX" in body
    assert "Empty." in body
    assert prose_cost == 0.01


def test_write_report_renders_no_exclusions_fired_placeholder(tmp_path: Path):
    findings_jsonl = tmp_path / "findings.jsonl"
    write_finding(findings_jsonl, _make_finding(confidence="high"))

    fake_llm = MagicMock()
    fake_llm.text = "### High\n_None._"
    fake_llm.cost_usd = 0.0
    with patch("findajob.loose_ends.roll_up.openrouter.complete", return_value=fake_llm):
        report_path, _ = write_report(
            findings_jsonl=findings_jsonl,
            exclusions={},
            output_dir=tmp_path,
            today=date(2026, 5, 19),
        )
    body = report_path.read_text(encoding="utf-8")
    assert "_No exclusions matched any walkthrough this run._" in body
