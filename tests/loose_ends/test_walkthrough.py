"""Tests for src/findajob/loose_ends/walkthrough.py."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from findajob.loose_ends.walkthrough import (
    AssertPresentStep,
    ClickActionStep,
    EvaluateDomStep,
    Finding,
    GotoStep,
    PickFirstRowStep,
    extract_hints,
    load_walkthroughs,
    read_findings,
    write_finding,
)


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


def test_load_walkthroughs_parses_goto_and_evaluate_dom(tmp_path: Path):
    path = tmp_path / "loose_ends_walkthroughs.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "walkthroughs": [
                    {
                        "name": "dashboard_first_load",
                        "persona": "nux_user",
                        "target_category": 3,
                        "steps": [
                            {"goto": "/board/dashboard"},
                            {"evaluate_dom": {"category": 3, "rubric": "empty_state_no_guidance"}},
                        ],
                    }
                ]
            }
        )
    )
    walkthroughs = load_walkthroughs(path)
    assert len(walkthroughs) == 1
    w = walkthroughs[0]
    assert w.name == "dashboard_first_load"
    assert w.persona == "nux_user"
    assert w.target_category == 3
    assert len(w.steps) == 2
    assert isinstance(w.steps[0], GotoStep)
    assert w.steps[0].url == "/board/dashboard"
    assert isinstance(w.steps[1], EvaluateDomStep)
    assert w.steps[1].category == 3
    assert w.steps[1].rubric == "empty_state_no_guidance"


def test_load_walkthroughs_parses_action_steps(tmp_path: Path):
    path = tmp_path / "loose_ends_walkthroughs.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "walkthroughs": [
                    {
                        "name": "applied_undo_exits",
                        "persona": "established_user",
                        "target_category": 2,
                        "steps": [
                            {"goto": "/board/applied"},
                            {"pick_first_row_with_stage": "applied"},
                            {"click_action": "Interviewing"},
                            {"assert_present": "[data-fp]"},
                            {
                                "evaluate_dom": {
                                    "category": 2,
                                    "rubric": "flow_without_exit",
                                    "context_hint": "Just transitioned applied→interviewing",
                                }
                            },
                        ],
                    }
                ]
            }
        )
    )
    [w] = load_walkthroughs(path)
    assert isinstance(w.steps[1], PickFirstRowStep)
    assert w.steps[1].stage == "applied"
    assert isinstance(w.steps[2], ClickActionStep)
    assert w.steps[2].action_text == "Interviewing"
    assert isinstance(w.steps[3], AssertPresentStep)
    assert w.steps[3].selector == "[data-fp]"
    assert w.steps[4].context_hint == "Just transitioned applied→interviewing"


def test_load_walkthroughs_rejects_unknown_step(tmp_path: Path):
    path = tmp_path / "loose_ends_walkthroughs.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "walkthroughs": [
                    {
                        "name": "x",
                        "persona": "nux_user",
                        "target_category": 3,
                        "steps": [{"unknown_step": "foo"}],
                    }
                ]
            }
        )
    )
    with pytest.raises(ValueError, match="unknown step"):
        load_walkthroughs(path)


def test_load_walkthroughs_rejects_invalid_persona(tmp_path: Path):
    path = tmp_path / "loose_ends_walkthroughs.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "walkthroughs": [
                    {
                        "name": "x",
                        "persona": "month_1_user",
                        "target_category": 3,
                        "steps": [],
                    }
                ]
            }
        )
    )
    with pytest.raises(ValueError, match="persona"):
        load_walkthroughs(path)


def test_extract_hints_finds_buttons_and_links():
    dom = """
    <html><body>
        <button>Filter</button>
        <button type="submit">Apply</button>
        <a href="/board/dashboard">Go to dashboard</a>
    </body></html>
    """
    hints = extract_hints(dom=dom, current_url="/board/applied")
    assert hints["current_url"] == "/board/applied"
    assert set(hints["visible_button_labels"]) == {"Filter", "Apply", "Go to dashboard"}


def test_extract_hints_finds_collection_containers():
    dom = """
    <html><body>
        <table id="applied-jobs"><tbody></tbody></table>
        <ul id="dashboard-list"><li>...</li></ul>
        <div class="collection" data-collection="rejected"></div>
    </body></html>
    """
    hints = extract_hints(dom=dom, current_url="/board")
    assert set(hints["collection_container_ids"]) >= {"applied-jobs", "dashboard-list", "rejected"}


def test_extract_hints_finds_form_targets():
    dom = """
    <html><body>
        <form action="/board/jobs/abc123/apply" method="post"></form>
        <form action="/settings/connections/" method="post"></form>
    </body></html>
    """
    hints = extract_hints(dom=dom, current_url="/board")
    assert "/board/jobs/abc123/apply" in hints["form_action_targets"]
    assert "/settings/connections/" in hints["form_action_targets"]


def test_extract_hints_stable_for_fixed_dom():
    """Same input → same output (no nondeterminism)."""
    dom = "<html><body><button>Save</button></body></html>"
    h1 = extract_hints(dom=dom, current_url="/x")
    h2 = extract_hints(dom=dom, current_url="/x")
    assert h1 == h2
