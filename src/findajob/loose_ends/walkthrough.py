"""Playwright walker + YAML loader + hint extractor (#572 Phase 2).

Walks every walkthrough in config/loose_ends_walkthroughs.yaml, executes
each step's primitive via Playwright sync API, and at evaluate_dom steps
hands the redacted DOM + structured hints to rubrics.py for LLM judgment.

The walker is itinerary-driven: it never asks the LLM where to go. The
LLM is only called at evaluate_dom steps to judge what's rendered.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from html.parser import HTMLParser
from pathlib import Path

import yaml


@dataclass(frozen=True)
class Finding:
    """One classified loose-end candidate from a single evaluate_dom step.

    Different shape from phase 1's Finding — Phase 2 carries persona,
    walkthrough provenance, and the (persona, route, rubric) exclusion key.
    """

    persona: str  # "nux_user" | "established_user"
    walkthrough_name: str  # matches config/loose_ends_walkthroughs.yaml
    current_url: str  # path the walker was on when it evaluated
    category: int  # 2 or 3
    is_loose_end: bool  # LLM's judgment (false if excluded)
    confidence: str  # "high" | "medium" | "low" | "review"
    rationale: str
    suggested_surface: str
    excluded: bool  # true if filtered before LLM call
    exclusion_key: str | None  # filled when excluded=True


def write_finding(target: Path, finding: Finding) -> None:
    """Append one JSONL row. Creates the file if needed."""
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as f:
        f.write(json.dumps(asdict(finding)) + "\n")


def read_findings(source: Path) -> list[Finding]:
    """Read a JSONL file into a list of Finding."""
    if not source.exists():
        return []
    out: list[Finding] = []
    for raw in source.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        out.append(Finding(**json.loads(raw)))
    return out


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


Step = GotoStep | PickFirstRowStep | ClickActionStep | AssertPresentStep | EvaluateDomStep


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
        if tag in ("table", "ul", "ol", "div"):
            # Collection container if it has an id or a data-collection attribute.
            collection_id = attr_dict.get("data-collection") or attr_dict.get("id")
            classes = attr_dict.get("class", "").split()
            if collection_id and (
                tag in ("table", "ul", "ol") or "collection" in classes or "data-collection" in attr_dict
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
