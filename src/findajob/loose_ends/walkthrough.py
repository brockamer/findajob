"""Playwright walker + YAML loader + hint extractor (#572 Phase 2).

Walks every walkthrough in config/loose_ends_walkthroughs.yaml, executes
each step's primitive via Playwright sync API, and at evaluate_dom steps
hands the redacted DOM + structured hints to rubrics.py for LLM judgment.

The walker is itinerary-driven: it never asks the LLM where to go. The
LLM is only called at evaluate_dom steps to judge what's rendered.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

import yaml
from playwright.sync_api import Error as PWError
from playwright.sync_api import TimeoutError as PWTimeout

from findajob.loose_ends.finding import Finding
from findajob.loose_ends.rubrics import (
    evaluate_action_without_confirmation,
    evaluate_empty_state_no_guidance,
    evaluate_flow_without_exit,
)

_VALID_PERSONAS = {"nux_user", "established_user"}


@dataclass(frozen=True)
class GotoStep:
    url: str


@dataclass(frozen=True)
class PickFirstRowStep:
    stage: str


@dataclass(frozen=True)
class ClickActionStep:
    action_text: str


@dataclass(frozen=True)
class AssertPresentStep:
    selector: str


@dataclass(frozen=True)
class EvaluateDomStep:
    category: int
    rubric: str
    context_hint: str = ""


@dataclass(frozen=True)
class SelectOptionStep:
    row_selector: str  # e.g., 'tr[data-stage="applied"]'
    option_value: str  # e.g., "interview"


Step = GotoStep | PickFirstRowStep | ClickActionStep | AssertPresentStep | EvaluateDomStep | SelectOptionStep


@dataclass(frozen=True)
class Walkthrough:
    name: str
    persona: str
    target_category: int
    steps: tuple[Step, ...]


def _parse_step(raw: dict) -> Step:
    if len(raw) != 1:
        raise ValueError(f"step dict must have exactly one key, got {list(raw.keys())}")
    [(key, value)] = raw.items()
    if key == "goto":
        return GotoStep(url=str(value))
    if key == "pick_first_row_with_stage":
        return PickFirstRowStep(stage=str(value))
    if key == "click_action":
        return ClickActionStep(action_text=str(value))
    if key == "assert_present":
        return AssertPresentStep(selector=str(value))
    if key == "evaluate_dom":
        if not isinstance(value, dict):
            raise ValueError(f"evaluate_dom value must be a dict, got {type(value).__name__}")
        return EvaluateDomStep(
            category=int(value["category"]),
            rubric=str(value["rubric"]),
            context_hint=str(value.get("context_hint", "")),
        )
    if key == "select_option":
        if not isinstance(value, dict):
            raise ValueError(f"select_option value must be a dict, got {type(value).__name__}")
        return SelectOptionStep(
            row_selector=str(value["row_selector"]),
            option_value=str(value["option_value"]),
        )
    raise ValueError(f"unknown step type: {key!r}")


def load_walkthroughs(path: Path) -> list[Walkthrough]:
    """Parse config/loose_ends_walkthroughs.yaml. Raises on invalid persona or step."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    walkthroughs: list[Walkthrough] = []
    for w in raw.get("walkthroughs", []):
        persona = w["persona"]
        if persona not in _VALID_PERSONAS:
            raise ValueError(f"invalid persona '{persona}' (expected one of {_VALID_PERSONAS})")
        walkthroughs.append(
            Walkthrough(
                name=str(w["name"]),
                persona=persona,
                target_category=int(w["target_category"]),
                steps=tuple(_parse_step(s) for s in w.get("steps", [])),
            )
        )
    return walkthroughs


class _HintParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.collection_container_ids: list[str] = []
        self.visible_button_labels: list[str] = []
        self.form_action_targets: list[str] = []
        self._current_button: list[str] | None = None
        self._current_link: list[str] | None = None
        self._capturing: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_dict = {k: (v or "") for k, v in attrs}
        if tag in ("table", "tbody", "ul", "ol", "div"):
            # Collection container if it has an id or a data-collection attribute.
            # tbody included because findajob's board templates carry the id on
            # <tbody id="rows"> rather than the parent <table> (#751 baseline
            # discovered the rubric received empty collection_container_ids for
            # every board tab because of this).
            collection_id = attr_dict.get("data-collection") or attr_dict.get("id")
            classes = attr_dict.get("class", "").split()
            if collection_id and (
                tag in ("table", "tbody", "ul", "ol") or "collection" in classes or "data-collection" in attr_dict
            ):
                self.collection_container_ids.append(collection_id)
        if tag == "button":
            self._current_button = []
            self._capturing = "button"
        if tag == "a" and attr_dict.get("href"):
            self._current_link = []
            self._capturing = "link"
        if tag == "form" and attr_dict.get("action"):
            self.form_action_targets.append(attr_dict["action"])

    def handle_endtag(self, tag: str) -> None:
        if tag == "button" and self._current_button is not None:
            label = "".join(self._current_button).strip()
            if label:
                self.visible_button_labels.append(label)
            self._current_button = None
            self._capturing = None
        if tag == "a" and self._current_link is not None:
            label = "".join(self._current_link).strip()
            if label:
                self.visible_button_labels.append(label)
            self._current_link = None
            self._capturing = None

    def handle_data(self, data: str) -> None:
        if self._capturing == "button" and self._current_button is not None:
            self._current_button.append(data)
        if self._capturing == "link" and self._current_link is not None:
            self._current_link.append(data)


def extract_hints(*, dom: str, current_url: str) -> dict:
    """Pure DOM → structured hints transform. Deterministic for fixed input."""
    parser = _HintParser()
    parser.feed(dom)
    return {
        "current_url": current_url,
        "collection_container_ids": sorted(set(parser.collection_container_ids)),
        "visible_button_labels": parser.visible_button_labels,
        "form_action_targets": parser.form_action_targets,
        "redacted_keys_count": 0,
    }


_PAGE_TOP_BUDGET = 8000
_PER_CONTAINER_BUDGET = 4000


def build_cat3_dom_snippet(*, dom: str, container_ids: list[str]) -> str:
    """Page-top excerpt plus a slice around each named collection container.

    Cat-3's `empty_state_no_guidance` rubric is asked to judge whether a
    specific container is empty + unaccompanied by guidance. The hint pipeline
    already knows which containers (`collection_container_ids`) are in scope,
    but board templates render filter UI first and tables last — a uniform
    `dom[:N]` slice systematically truncates the container the rubric was
    told to look at. This builder appends each container's neighborhood after
    the page-top base so the snippet and the hint metadata stay self-consistent.
    """
    parts = [dom[:_PAGE_TOP_BUDGET]]
    for cid in container_ids:
        pattern = (
            rf"<(?:table|tbody|ul|ol|div)[^>]*"
            rf'\b(?:id|data-collection)=["\']?{re.escape(cid)}["\']?'
        )
        m = re.search(pattern, dom)
        if m is None:
            continue
        start = m.start()
        parts.append(f"<!-- container: {cid} -->\n{dom[start : start + _PER_CONTAINER_BUDGET]}")
    return "\n".join(parts)


def _url_path(url: object) -> str:
    """Extract just the path component (e.g. 'https://x.com/foo?a=1' → '/foo')."""
    parsed = urlparse(str(url))
    return parsed.path or "/"


def dispatch_step(
    *,
    page,  # Playwright Page (or MagicMock in tests)
    step: Step,
    base_url: str,
    persona: str,
    walkthrough_name: str,
    exclusions: dict[str, str],
) -> tuple[Finding | None, float]:
    """Execute one walkthrough step. Returns (finding, cost) for evaluate_dom; (None, 0.0) otherwise.

    Raises AssertionError on assert_present failure (the only step that hard-fails).
    """
    if isinstance(step, GotoStep):
        page.goto(f"{base_url}{step.url}", wait_until="networkidle")
        return None, 0.0
    if isinstance(step, PickFirstRowStep):
        page.locator(f'tr[data-stage="{step.stage}"]').first.get_attribute("data-fingerprint")
        return None, 0.0
    if isinstance(step, ClickActionStep):
        page.get_by_role("button", name=step.action_text).first.click()
        page.wait_for_load_state("networkidle")
        return None, 0.0
    if isinstance(step, AssertPresentStep):
        if page.locator(step.selector).count() == 0:
            raise AssertionError(f"selector not present: {step.selector}")
        return None, 0.0
    if isinstance(step, SelectOptionStep):
        page.locator(step.row_selector).first.locator("select").select_option(step.option_value)
        page.wait_for_load_state("networkidle")
        return None, 0.0
    if isinstance(step, EvaluateDomStep):
        dom = page.content()
        current_url = _url_path(page.url)
        hints = extract_hints(dom=dom, current_url=current_url)
        if step.category == 2:
            return evaluate_flow_without_exit(
                persona=persona,
                walkthrough_name=walkthrough_name,
                current_url=current_url,
                context_hint=step.context_hint,
                visible_button_labels=hints["visible_button_labels"],
                form_action_targets=hints["form_action_targets"],
                dom_snippet=dom[:8000],
                exclusions=exclusions,
            )
        if step.category == 3:
            return evaluate_empty_state_no_guidance(
                persona=persona,
                walkthrough_name=walkthrough_name,
                current_url=current_url,
                collection_container_ids=hints["collection_container_ids"],
                visible_button_labels=hints["visible_button_labels"],
                dom_snippet=build_cat3_dom_snippet(
                    dom=dom,
                    container_ids=hints["collection_container_ids"],
                ),
                exclusions=exclusions,
            )
        if step.category == 4:
            # dom[:8000] mirrors cat-2's slice — the toast region lives near the
            # top of base.html so it sits well within the first 8000 chars. If
            # a future walkthrough targets a row deeper in a long table and the
            # rubric mis-reads "no human-readable state text" because the row
            # was truncated, switch to a container-aware builder (see #778 /
            # build_cat3_dom_snippet for the precedent).
            return evaluate_action_without_confirmation(
                persona=persona,
                walkthrough_name=walkthrough_name,
                current_url=current_url,
                context_hint=step.context_hint,
                visible_button_labels=hints["visible_button_labels"],
                dom_snippet=dom[:8000],
                exclusions=exclusions,
            )
        raise ValueError(f"unsupported evaluate_dom category: {step.category}")
    raise ValueError(f"unknown step type: {type(step).__name__}")


def run_walkthrough(
    *,
    page,
    walkthrough: Walkthrough,
    base_url: str,
    exclusions: dict[str, str],
) -> tuple[list[Finding], float]:
    """Execute all steps in a walkthrough. Returns (findings, total_cost).

    A Playwright/network timeout at any step records a REVIEW finding and
    returns early. AssertionError from assert_present is treated the same —
    page shape drift is worth surfacing, not fatal.
    """
    findings: list[Finding] = []
    total_cost = 0.0
    for idx, step in enumerate(walkthrough.steps):
        try:
            finding, cost = dispatch_step(
                page=page,
                step=step,
                base_url=base_url,
                persona=walkthrough.persona,
                walkthrough_name=walkthrough.name,
                exclusions=exclusions,
            )
        except (PWTimeout, PWError, AssertionError) as exc:
            findings.append(
                Finding(
                    persona=walkthrough.persona,
                    walkthrough_name=walkthrough.name,
                    current_url=_url_path(getattr(page, "url", "")),
                    category=walkthrough.target_category,
                    is_loose_end=False,
                    confidence="review",
                    rationale=f"walker {type(exc).__name__} at step {idx} ({type(step).__name__}): {exc}",
                    suggested_surface="",
                    excluded=False,
                    exclusion_key=None,
                )
            )
            return findings, total_cost
        total_cost += cost
        if finding is not None:
            findings.append(finding)
    return findings, total_cost
