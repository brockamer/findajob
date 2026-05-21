"""Tests for src/findajob/loose_ends/walkthrough.py."""

from __future__ import annotations

import json
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml
from playwright.sync_api import Error as PWError
from playwright.sync_api import TimeoutError as PWTimeout

from findajob.loose_ends.finding import Finding, read_findings, write_finding
from findajob.loose_ends.walkthrough import (
    AssertPresentStep,
    ClickActionStep,
    EvaluateDomStep,
    GotoStep,
    PickFirstRowStep,
    SelectOptionStep,
    Walkthrough,
    dispatch_step,
    extract_hints,
    load_walkthroughs,
    run_walkthrough,
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


def test_extract_hints_recognizes_tbody_id_as_collection():
    # findajob's board templates put the id on <tbody>, not the parent <table>.
    # Without this, the rubric receives an empty collection_container_ids list
    # for every board tab and can't evaluate empty-state loose ends.
    dom = """
    <html><body>
        <table class="min-w-full"><tbody id="rows"></tbody></table>
    </body></html>
    """
    hints = extract_hints(dom=dom, current_url="/board/dashboard")
    assert "rows" in hints["collection_container_ids"]


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


def test_dispatch_goto_calls_page_goto():
    page = MagicMock()
    dispatch_step(
        page=page,
        step=GotoStep(url="/board/applied"),
        base_url="https://example.com",
        persona="established_user",
        walkthrough_name="x",
        exclusions={},
    )
    page.goto.assert_called_once_with("https://example.com/board/applied", wait_until="networkidle")


def test_dispatch_pick_first_row_uses_data_stage_selector():
    page = MagicMock()
    page.locator.return_value.first.get_attribute.return_value = "abc123"
    dispatch_step(
        page=page,
        step=PickFirstRowStep(stage="applied"),
        base_url="https://example.com",
        persona="established_user",
        walkthrough_name="x",
        exclusions={},
    )
    page.locator.assert_called_with('tr[data-stage="applied"]')


def test_dispatch_click_action_clicks_by_label():
    page = MagicMock()
    dispatch_step(
        page=page,
        step=ClickActionStep(action_text="Interviewing"),
        base_url="https://example.com",
        persona="established_user",
        walkthrough_name="x",
        exclusions={},
    )
    page.get_by_role.assert_called_with("button", name="Interviewing")


def test_dispatch_evaluate_dom_calls_rubric_evaluator():
    page = MagicMock()
    page.content.return_value = "<html><body><button>Filter</button></body></html>"
    page.url = "https://example.com/board/dashboard"
    fake_finding = MagicMock()
    fake_finding.is_loose_end = True
    with patch(
        "findajob.loose_ends.walkthrough.evaluate_empty_state_no_guidance",
        return_value=(fake_finding, 0.05),
    ) as mock_eval:
        result, cost = dispatch_step(
            page=page,
            step=EvaluateDomStep(category=3, rubric="empty_state_no_guidance"),
            base_url="https://example.com",
            persona="nux_user",
            walkthrough_name="dashboard_first_load",
            exclusions={},
        )
    assert result is fake_finding
    assert cost == 0.05
    kwargs = mock_eval.call_args.kwargs
    assert kwargs["persona"] == "nux_user"
    assert kwargs["current_url"] == "/board/dashboard"


def test_dispatch_evaluate_dom_routes_to_flow_without_exit_for_cat2():
    page = MagicMock()
    page.content.return_value = "<html></html>"
    page.url = "https://example.com/board/applied"
    with patch(
        "findajob.loose_ends.walkthrough.evaluate_flow_without_exit",
        return_value=(MagicMock(), 0.03),
    ) as mock_eval:
        dispatch_step(
            page=page,
            step=EvaluateDomStep(category=2, rubric="flow_without_exit", context_hint="hint"),
            base_url="https://example.com",
            persona="established_user",
            walkthrough_name="x",
            exclusions={},
        )
    mock_eval.assert_called_once()


def test_dispatch_assert_present_succeeds_when_selector_resolves():
    page = MagicMock()
    page.locator.return_value.count.return_value = 1
    dispatch_step(
        page=page,
        step=AssertPresentStep(selector="[data-fp]"),
        base_url="https://example.com",
        persona="established_user",
        walkthrough_name="x",
        exclusions={},
    )


def test_dispatch_assert_present_raises_when_selector_missing():
    page = MagicMock()
    page.locator.return_value.count.return_value = 0
    with pytest.raises(AssertionError, match="not present"):
        dispatch_step(
            page=page,
            step=AssertPresentStep(selector="[data-fp]"),
            base_url="https://example.com",
            persona="established_user",
            walkthrough_name="x",
            exclusions={},
        )


def test_run_walkthrough_executes_all_steps_and_returns_findings():
    page = MagicMock()
    page.content.return_value = "<html><body></body></html>"
    page.url = "https://example.com/board/applied"
    page.locator.return_value.count.return_value = 1  # assert_present succeeds
    page.locator.return_value.first.get_attribute.return_value = "abc"

    walkthrough = Walkthrough(
        name="x",
        persona="established_user",
        target_category=2,
        steps=(
            GotoStep(url="/board/applied"),
            EvaluateDomStep(category=2, rubric="flow_without_exit"),
        ),
    )
    fake_finding = MagicMock()
    fake_finding.is_loose_end = True
    with patch(
        "findajob.loose_ends.walkthrough.evaluate_flow_without_exit",
        return_value=(fake_finding, 0.04),
    ):
        findings, cost = run_walkthrough(
            page=page,
            walkthrough=walkthrough,
            base_url="https://example.com",
            exclusions={},
        )
    assert len(findings) == 1
    assert findings[0] is fake_finding
    assert cost == 0.04


def test_run_walkthrough_records_timeout_as_review_finding():
    """A Playwright TimeoutError mid-walkthrough must NOT abort the run.

    Walker records a REVIEW finding and returns early. This test exercises
    the real playwright.sync_api.TimeoutError class, not the builtin, so it
    catches the production class hierarchy correctly.
    """
    page = MagicMock()
    page.goto.side_effect = PWTimeout("nav timeout")

    walkthrough = Walkthrough(
        name="x",
        persona="established_user",
        target_category=2,
        steps=(GotoStep(url="/board/applied"),),
    )
    findings, cost = run_walkthrough(
        page=page,
        walkthrough=walkthrough,
        base_url="https://example.com",
        exclusions={},
    )
    assert len(findings) == 1
    assert findings[0].confidence == "review"
    assert "timeout" in findings[0].rationale.lower()
    assert cost == 0.0


def test_run_walkthrough_records_pw_error_as_review_finding():
    """A Playwright Error (e.g. detached frame, strict-mode violation) is also caught.

    PWError is Playwright's base exception class and covers connection-closed,
    detached-frame, and strict-mode violations — all indicate page shape drift
    worth surfacing as a REVIEW finding rather than crashing the audit.
    """
    page = MagicMock()
    page.goto.side_effect = PWError("element detached")

    walkthrough = Walkthrough(
        name="x",
        persona="nux_user",
        target_category=3,
        steps=(GotoStep(url="/board/dashboard"),),
    )
    findings, cost = run_walkthrough(
        page=page,
        walkthrough=walkthrough,
        base_url="https://example.com",
        exclusions={},
    )
    assert len(findings) == 1
    assert findings[0].confidence == "review"
    assert "error" in findings[0].rationale.lower()
    assert cost == 0.0


def test_parse_select_option_step(tmp_path: Path):
    """YAML parsing of select_option step produces a SelectOptionStep."""
    path = tmp_path / "walkthroughs.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "walkthroughs": [
                    {
                        "name": "x",
                        "persona": "established_user",
                        "target_category": 2,
                        "steps": [
                            {
                                "select_option": {
                                    "row_selector": 'tr[data-stage="applied"]',
                                    "option_value": "interview",
                                }
                            }
                        ],
                    }
                ]
            }
        )
    )
    [w] = load_walkthroughs(path)
    assert len(w.steps) == 1
    step = w.steps[0]
    assert isinstance(step, SelectOptionStep)
    assert step.row_selector == 'tr[data-stage="applied"]'
    assert step.option_value == "interview"


def test_dispatch_select_option_calls_select_option_on_row_select():
    """Dispatcher uses page.locator(row_selector).first.locator("select").select_option(value)."""
    page = MagicMock()
    dispatch_step(
        page=page,
        step=SelectOptionStep(row_selector='tr[data-stage="applied"]', option_value="interview"),
        base_url="https://example.com",
        persona="established_user",
        walkthrough_name="x",
        exclusions={},
    )
    page.locator.assert_called_with('tr[data-stage="applied"]')
    page.locator.return_value.first.locator.assert_called_with("select")
    page.locator.return_value.first.locator.return_value.select_option.assert_called_with("interview")
    page.wait_for_load_state.assert_called_with("networkidle")


@contextmanager
def _stub_server(port: int = 18572):
    """Run the stub FastAPI app in a daemon thread; yield base URL."""
    pytest.importorskip("uvicorn")
    pytest.importorskip("fastapi")
    pytest.importorskip("playwright")
    import uvicorn

    from tests.loose_ends.stub_app.main import app

    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    # Wait for the server to come up
    for _ in range(40):
        if server.started:
            break
        time.sleep(0.1)
    if not server.started:
        raise RuntimeError("stub server did not start in time")
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=3)


@pytest.mark.integration
def test_end_to_end_walkthrough_against_stub():
    """Full Playwright run against the stub, with the LLM mocked.

    Asserts: hint extractor + Playwright dispatch + rubric routing all
    work together. LLM is mocked to keep the test fast and deterministic.
    """
    pytest.importorskip("playwright")
    from playwright.sync_api import sync_playwright

    from findajob.loose_ends.finding import Finding

    with _stub_server() as base_url:
        # Build a one-walkthrough itinerary that targets the stub's cat-3 page
        walkthrough = Walkthrough(
            name="stub_empty_no_cta",
            persona="nux_user",
            target_category=3,
            steps=(
                GotoStep(url="/empty-no-cta"),
                EvaluateDomStep(category=3, rubric="empty_state_no_guidance"),
            ),
        )

        fake_finding = Finding(
            persona="nux_user",
            walkthrough_name="stub_empty_no_cta",
            current_url="/empty-no-cta",
            category=3,
            is_loose_end=True,
            confidence="high",
            rationale="Empty table, no CTA.",
            suggested_surface="Add CTA",
            excluded=False,
            exclusion_key=None,
        )
        with (
            patch(
                "findajob.loose_ends.walkthrough.evaluate_empty_state_no_guidance",
                return_value=(fake_finding, 0.03),
            ) as mock_eval,
            sync_playwright() as pw,
        ):
            try:
                browser = pw.chromium.launch(headless=True)
            except Exception:
                try:
                    browser = pw.chromium.launch(headless=True, channel="chrome")
                except Exception as exc:
                    pytest.skip(f"Playwright chromium not available: {exc}")
            ctx = browser.new_context()
            page = ctx.new_page()
            findings, cost = run_walkthrough(
                page=page,
                walkthrough=walkthrough,
                base_url=base_url,
                exclusions={},
            )
            browser.close()

    assert mock_eval.called
    assert len(findings) == 1
    assert findings[0].is_loose_end is True
    assert cost == 0.03

    # Verify the hint extractor saw the expected DOM features
    kwargs = mock_eval.call_args.kwargs
    assert "applied-jobs" in kwargs["collection_container_ids"]
    assert kwargs["current_url"] == "/empty-no-cta"
