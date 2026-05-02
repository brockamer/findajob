#!/usr/bin/env python3
"""Autonomous Playwright walkthrough harness for findajob onboarding.

Drives a headless browser through the full onboarding interview against a
live findajob instance (typically findajob-test), replaying user answers
from a prior transcript. Checks #401 PR B acceptance criteria and emits a
machine-readable findings report.

Usage:
  uv run python scripts/walkthrough_harness.py \\
    --base-url https://findajob-test.example.com/ \\
    --output-dir tmp/onboarding-walkthrough-YYYY-MM-DD/ \\
    --secrets-file ~/.secrets

Replay corpus: defaults to ``tests/fixtures/walkthrough/corpus_transcript.md``
(an in-repo fictional persona). Override with ``--replay-from <path>`` if
re-baselining or experimenting with a captured transcript. Real operator
walkthroughs MUST NOT be checked into the repo as the corpus — they
contain PII; sanitize and re-baseline the in-repo corpus instead. See the
re-baseline workflow notes at the top of the corpus file.

Secrets file format (one per line, optionally quoted, # = comment):
  FINDAJOB_TEST_USER=myuser
  FINDAJOB_TEST_PASS=mypass

The file can equivalently use shell-sourceable ``export KEY=value`` lines so
it can double as a script you ``source`` in your shell.
  FINDAJOB_TEST_OR_KEY=sk-or-v1-...
  FINDAJOB_TEST_RAPIDAPI_KEY=abc...
  FINDAJOB_TEST_GOOGLE_KEY=AIza...   # optional

Exit codes:
  0 — all acceptance criteria PASS
  1 — at least one criterion FAILed
  2 — at least one criterion is REVIEW (no FAILs)

SECRET HYGIENE: keys are never passed on the command line, never logged,
and are redacted from DOM snapshots before being written to disk.
"""

from __future__ import annotations

import argparse
import json
import re
import shlex
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Allow importing from the scripts/ directory for the corpus parser module.
sys.path.insert(0, str(Path(__file__).parent))
from walkthrough_replay_corpus import _PHASE_ANCHORS, ReplayCorpus, load_corpus

# Ordered list of phase-end anchor names. Index in this list IS the phase
# number minus one (i.e. PHASE_ANCHOR_ORDER[0] == "phase_1_end" marks the
# end of phase 1). Drives the harness's "current phase" state machine and
# corpus-side phase-range computation.
PHASE_ANCHOR_ORDER = ["phase_1_end", "phase_2_end", "phase_3_end", "phase_4_end", "phase_5_end"]

# ---------------------------------------------------------------------------
# Secret loading
# ---------------------------------------------------------------------------

_REQUIRED_SECRET_VARS = [
    "FINDAJOB_TEST_OR_KEY",
    "FINDAJOB_TEST_RAPIDAPI_KEY",
]
# USER/PASS only matter when the target stack sits behind HTTP Basic Auth
# (tester stacks like alice/papa/dave/judy/tango). The operator's findajob-test
# instance is open from the WireGuard mesh / public domain without auth, so
# leaving them unset just means Playwright skips the httpCredentials context.
_OPTIONAL_SECRET_VARS = [
    "FINDAJOB_TEST_USER",
    "FINDAJOB_TEST_PASS",
    "FINDAJOB_TEST_GOOGLE_KEY",
]

# Input field names on the Step 1 form that carry API key values.
_KEY_INPUT_NAMES = {"openrouter_api_key", "rapidapi_key", "google_api_key"}


def load_secrets(path: Path) -> dict[str, str]:
    """Parse a KEY=value secrets file. Never shells out — parses manually.

    Accepts both bare ``KEY=value`` and shell-sourceable ``export KEY=value``
    so the file can double as a shell-source script.
    """
    if not path.exists():
        raise FileNotFoundError(f"Secrets file not found: {path}")

    secrets: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :]
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        # Strip surrounding quotes (single or double)
        value = value.strip().strip('"').strip("'")
        secrets[key] = value

    missing = [v for v in _REQUIRED_SECRET_VARS if v not in secrets or not secrets[v]]
    if missing:
        raise ValueError(
            "Missing required secrets. Add to your secrets file:\n" + "\n".join(f"  {v}=..." for v in missing)
        )

    return secrets


# ---------------------------------------------------------------------------
# DOM snapshot redaction
# ---------------------------------------------------------------------------

# Matches <input ... name="<key_input>" ... value="..."> in any attribute order.
# Replaces only the value="..." portion with ***REDACTED***.
_REDACT_VALUE_RE = re.compile(
    r'(<input\b[^>]*\bname="(?:' + "|".join(re.escape(n) for n in _KEY_INPUT_NAMES) + r')"[^>]*\bvalue=")([^"]*)',
    re.IGNORECASE,
)
_REDACT_VALUE_RE2 = re.compile(
    r'(<input\b[^>]*\bvalue=")([^"]*)("[^>]*\bname="(?:' + "|".join(re.escape(n) for n in _KEY_INPUT_NAMES) + r')")',
    re.IGNORECASE,
)


def redact_dom_snapshot(html: str) -> str:
    """Replace API key values in DOM snapshots before writing to disk."""
    html = _REDACT_VALUE_RE.sub(r"\1***REDACTED***", html)
    html = _REDACT_VALUE_RE2.sub(r"\1***REDACTED***\3", html)
    return html


# ---------------------------------------------------------------------------
# Answer matching
# ---------------------------------------------------------------------------

# Words that indicate a yes/no readiness question from the assistant.
_AFFIRMATIVE_TRIGGERS = [
    "ready?",
    "shall we",
    "want to continue",
    "next?",
    "continue?",
    "let's move on",
    "good to go",
    "shall i",
    "sound good?",
    "look good?",
    "looks good?",
    "ready to",
    "next step",
]

# Minimum keyword overlap fraction to consider a corpus question a match.
_KEYWORD_MATCH_THRESHOLD = 0.4


def _is_affirmative_question(assistant_text: str) -> bool:
    lower = assistant_text.lower()
    return any(trigger in lower for trigger in _AFFIRMATIVE_TRIGGERS)


def _keyword_overlap(a: str, b: str) -> float:
    """Fraction of a's significant words that appear in b."""
    stopwords = {"a", "an", "the", "and", "or", "is", "it", "in", "of", "to", "do", "you", "your", "i", "me", "my"}
    a_words = {w for w in re.findall(r"\b[a-z]{3,}\b", a.lower()) if w not in stopwords}
    b_lower = b.lower()
    if not a_words:
        return 0.0
    matches = sum(1 for w in a_words if w in b_lower)
    return matches / len(a_words)


def compute_phase_ranges(corpus: ReplayCorpus) -> list[tuple[int, int]]:
    """Convert ``corpus.phase_anchors`` into ordered 0-based half-open
    ranges over ``corpus.user_messages``, one per phase.

    The result has ``len(PHASE_ANCHOR_ORDER) + 1`` entries — one slot per
    phase 1..N plus a tail slot. Each fired anchor at 1-based turn ``T``
    marks ``T - 1`` (0-based) as the FIRST turn of the new phase, because
    anchors are detected on the assistant turn whose user response is in
    the new phase. The "tail" slot lands at the index immediately after
    the last fired anchor — i.e. it represents the phase the corpus
    ended in. Slots for unfired anchors at the end are empty
    (``start == end``).
    """
    ranges: list[tuple[int, int]] = [(0, 0)] * (len(PHASE_ANCHOR_ORDER) + 1)
    prev_start = 0
    last_phase_idx = 0
    for i, name in enumerate(PHASE_ANCHOR_ORDER):
        t = corpus.phase_anchors.get(name, 0)
        if t <= 0:
            continue
        next_start = t - 1
        ranges[i] = (prev_start, next_start)
        prev_start = next_start
        last_phase_idx = i + 1
    ranges[last_phase_idx] = (prev_start, corpus.turn_count)
    return ranges


def advance_phase_idx(assistant_text: str, current_phase_idx: int) -> int:
    """Detect phase advance from the assistant's text. Monotonically
    non-decreasing — once we've advanced past phase N we can't go back.
    A single assistant turn that mentions multiple anchors (e.g.
    "we just finished phase 2; moving to phase 3") advances by all of
    them in one call.
    """
    lower = assistant_text.lower()
    new_idx = current_phase_idx
    for i in range(current_phase_idx, len(PHASE_ANCHOR_ORDER)):
        anchor_name = PHASE_ANCHOR_ORDER[i]
        for phrase in _PHASE_ANCHORS[anchor_name]:
            if phrase in lower:
                new_idx = i + 1
                break
    return new_idx


def pick_answer(
    turn_idx: int,
    assistant_text: str,
    corpus: ReplayCorpus,
    intent_map: dict[str, str],
    *,
    current_phase_idx: int | None = None,
    phase_relative_turn: int | None = None,
    phase_ranges: list[tuple[int, int]] | None = None,
) -> tuple[str, str]:
    """Select the best replay answer for the current assistant question.

    Two modes:

    - **Legacy** (``current_phase_idx is None``): preserved for unit tests
      and any caller that doesn't track phase. Positional match is by
      absolute ``turn_idx``; keyword overlap considers all corpus turns.

    - **Phase-scoped** (``current_phase_idx`` provided, plus ``phase_ranges``
      and ``phase_relative_turn``): positional match is by the
      within-phase index; keyword overlap is restricted to corpus turns
      belonging to the same phase. Eliminates cross-phase contamination
      when the prompt's question shape has shifted between corpus capture
      and the current run (#405 Issue 2).

    Returns ``(answer_text, match_reason)`` — match_reason is one of
    ``positional``, ``positional_within_phase``, ``keyword(...)``,
    ``keyword_within_phase(...)``, ``intent(...)``, ``affirmative``,
    ``review``, ``review_no_phase_match``.
    """
    if _is_affirmative_question(assistant_text):
        return ("yes", "affirmative")

    if current_phase_idx is not None and phase_ranges is not None:
        return _pick_answer_phase_scoped(
            assistant_text=assistant_text,
            corpus=corpus,
            intent_map=intent_map,
            current_phase_idx=current_phase_idx,
            phase_relative_turn=phase_relative_turn or 0,
            phase_ranges=phase_ranges,
        )

    # Legacy path: positional by absolute turn_idx, keyword over all corpus.
    if 0 <= turn_idx < len(corpus.user_messages):
        prior = corpus.user_messages[turn_idx]
        if prior.strip():
            return (prior, "positional")

    best_overlap = 0.0
    best_answer = ""
    for prior_asst_idx, prior_asst in enumerate(corpus.assistant_messages):
        if not prior_asst.strip():
            continue
        overlap = _keyword_overlap(assistant_text, prior_asst)
        if overlap > best_overlap:
            best_overlap = overlap
            if prior_asst_idx < len(corpus.user_messages):
                best_answer = corpus.user_messages[prior_asst_idx]

    if best_overlap >= _KEYWORD_MATCH_THRESHOLD and best_answer.strip():
        return (best_answer, f"keyword(overlap={best_overlap:.2f})")

    for intent_key, letter_choices in intent_map.items():
        if intent_key.lower() in assistant_text.lower():
            return (letter_choices, f"intent({intent_key})")

    return ("Skip — using prior context", "review")


def _pick_answer_phase_scoped(
    *,
    assistant_text: str,
    corpus: ReplayCorpus,
    intent_map: dict[str, str],
    current_phase_idx: int,
    phase_relative_turn: int,
    phase_ranges: list[tuple[int, int]],
) -> tuple[str, str]:
    candidate_indices: list[int] = []
    if 0 <= current_phase_idx < len(phase_ranges):
        start, end = phase_ranges[current_phase_idx]
        if start < end:
            candidate_indices = list(range(start, end))

    if candidate_indices:
        # Rule 1: positional within phase
        if 0 <= phase_relative_turn < len(candidate_indices):
            corpus_idx = candidate_indices[phase_relative_turn]
            prior = corpus.user_messages[corpus_idx]
            if prior.strip():
                return (prior, "positional_within_phase")

        # Rule 2: keyword overlap restricted to phase
        best_overlap = 0.0
        best_answer = ""
        for corpus_idx in candidate_indices:
            prior_asst = corpus.assistant_messages[corpus_idx]
            if not prior_asst.strip():
                continue
            overlap = _keyword_overlap(assistant_text, prior_asst)
            if overlap > best_overlap:
                best_overlap = overlap
                best_answer = corpus.user_messages[corpus_idx]

        if best_overlap >= _KEYWORD_MATCH_THRESHOLD and best_answer.strip():
            return (best_answer, f"keyword_within_phase(overlap={best_overlap:.2f})")

    # Rule 3: intent map — phase-independent (a categorical-question detector
    # that classifies by question shape, not by corpus similarity). Runs
    # whether or not the corpus has any turns for the current phase.
    for intent_key, letter_choices in intent_map.items():
        if intent_key.lower() in assistant_text.lower():
            return (letter_choices, f"intent({intent_key})")

    if not candidate_indices:
        return ("Skip — using prior context", "review_no_phase_match")
    return ("Skip — using prior context", "review")


# ---------------------------------------------------------------------------
# Acceptance criteria tracking
# ---------------------------------------------------------------------------

CRITERIA = [
    "no_resume_banner_on_fresh_stack",
    "start_button_loading_state",
    "markdown_rendered_in_assistant_bubbles",
    "no_raw_file_block_in_dom",
    "captured_file_badge_present",
    "nav_cost_badge_increments",
    "auto_scroll_approaches_scroll_height",
    "finalize_block_populated_without_reload",
    "final_cost_within_ceiling",
]

Verdict = str  # "PASS" | "FAIL" | "REVIEW"


@dataclass
class FindingsRow:
    criterion: str
    turn: int | str
    verdict: Verdict
    evidence: str  # snapshot path or description


@dataclass
class FindingsReport:
    rows: list[FindingsRow] = field(default_factory=list)

    def add(self, criterion: str, turn: int | str, verdict: Verdict, evidence: str) -> None:
        self.rows.append(FindingsRow(criterion, turn, verdict, evidence))

    def exit_code(self) -> int:
        verdicts = {r.verdict for r in self.rows}
        if "FAIL" in verdicts:
            return 1
        if "REVIEW" in verdicts:
            return 2
        return 0

    def to_markdown(self) -> str:
        lines = [
            "# Walkthrough Findings",
            "",
            f"Generated: {datetime.now(UTC).isoformat()}",
            "",
            "| Criterion | Turn | Verdict | Evidence |",
            "|---|---|---|---|",
        ]
        for row in self.rows:
            lines.append(f"| {row.criterion} | {row.turn} | {row.verdict} | {row.evidence} |")
        return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Cost log
# ---------------------------------------------------------------------------


@dataclass
class CostLog:
    entries: list[float] = field(default_factory=list)

    def record(self, cumulative_usd: float) -> None:
        self.entries.append(cumulative_usd)

    def total(self) -> float:
        return self.entries[-1] if self.entries else 0.0

    def to_summary(self) -> dict[str, Any]:
        if not self.entries:
            return {"total_usd": 0.0, "turns": 0, "cost_per_turn_p50": 0.0, "cost_per_turn_p95": 0.0}

        deltas = [self.entries[0]] + [self.entries[i] - self.entries[i - 1] for i in range(1, len(self.entries))]
        sorted_d = sorted(deltas)
        n = len(sorted_d)
        p50 = sorted_d[n // 2]
        p95 = sorted_d[min(int(n * 0.95), n - 1)]

        return {
            "total_usd": round(self.total(), 4),
            "turns": len(self.entries),
            "cost_per_turn_p50": round(p50, 4),
            "cost_per_turn_p95": round(p95, 4),
        }


# ---------------------------------------------------------------------------
# Main harness
# ---------------------------------------------------------------------------


def _load_intent_map(base_dir: Path) -> dict[str, str]:
    """Load replay_intent.yaml if present, otherwise return empty dict."""
    yaml_path = base_dir / "replay_intent.yaml"
    if not yaml_path.exists():
        return {}
    try:
        import yaml  # pyyaml is already a project dep

        data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return {str(k): str(v) for k, v in data.items()}
    except Exception:
        pass
    return {}


def run_walkthrough(
    base_url: str,
    corpus: ReplayCorpus,
    output_dir: Path,
    secrets: dict[str, str],
    max_turns: int,
    cost_ceiling_usd: float,
    browser_channel: str | None = None,
) -> FindingsReport:
    """Drive the full onboarding walkthrough via Playwright sync API."""

    # Import Playwright here so the module is importable (and unit-testable)
    # without playwright installed.
    try:
        from playwright.sync_api import TimeoutError as PWTimeout
        from playwright.sync_api import sync_playwright
    except ImportError:
        print(
            "ERROR: playwright not installed. Run: uv run playwright install chromium",
            file=sys.stderr,
        )
        sys.exit(1)

    output_dir.mkdir(parents=True, exist_ok=True)
    snapshots_dir = output_dir / "dom-snapshots"
    snapshots_dir.mkdir(exist_ok=True)

    intent_map = _load_intent_map(Path(__file__).parent)
    findings = FindingsReport()
    cost_log = CostLog()
    console_messages: list[dict[str, Any]] = []
    transcript_turns: list[dict[str, str]] = []

    user = secrets.get("FINDAJOB_TEST_USER", "")
    password = secrets.get("FINDAJOB_TEST_PASS", "")
    or_key = secrets["FINDAJOB_TEST_OR_KEY"]
    rapidapi_key = secrets["FINDAJOB_TEST_RAPIDAPI_KEY"]
    google_key = secrets.get("FINDAJOB_TEST_GOOGLE_KEY", "")

    # Normalize base URL
    base_url = base_url.rstrip("/")

    with sync_playwright() as pw:
        # On platforms where Playwright doesn't ship a prebuilt chromium
        # (e.g. Ubuntu 26.04 dev VM), pass --browser-channel chrome to use
        # the system-installed Chrome binary instead.
        launch_kwargs: dict[str, Any] = {"headless": True}
        if browser_channel:
            launch_kwargs["channel"] = browser_channel
        browser = pw.chromium.launch(**launch_kwargs)
        context_kwargs: dict[str, Any] = {}
        if user and password:
            context_kwargs["http_credentials"] = {"username": user, "password": password}
        ctx = browser.new_context(**context_kwargs)
        page = ctx.new_page()

        # Capture console messages throughout the session
        page.on(
            "console",
            lambda msg: console_messages.append(
                {
                    "turn": "N/A",
                    "level": msg.type,
                    "text": msg.text,
                }
            ),
        )

        def snapshot(label: str) -> Path:
            html = page.content()
            html = redact_dom_snapshot(html)
            snap_path = snapshots_dir / f"{label}.html"
            snap_path.write_text(html, encoding="utf-8")
            return snap_path

        def read_cost() -> float:
            # The data attribute lives on the inner <span>, not on the
            # #progress-row <div> wrapper. The wrong selector returned None
            # → 0.0 every time; #405 Issue 1.
            try:
                val = page.get_attribute(
                    "#progress-row span[data-cumulative-cost-usd]",
                    "data-cumulative-cost-usd",
                )
                return float(val) if val else 0.0
            except Exception:
                return 0.0

        # ── Step 0: Navigate to /onboarding/ ─────────────────────────────────
        print(f"[harness] Navigating to {base_url}/onboarding/")
        page.goto(f"{base_url}/onboarding/", wait_until="networkidle")
        snap0 = snapshot("turn-00-onboarding-index")

        # Criterion: no resume banner on fresh stack
        dom = page.content()
        has_resume_banner = "resume" in dom.lower() and ("in-progress" in dom.lower() or "resume your" in dom.lower())
        findings.add(
            "no_resume_banner_on_fresh_stack",
            "0",
            "FAIL" if has_resume_banner else "PASS",
            str(snap0),
        )

        # ── Step 1: Fill API keys ─────────────────────────────────────────────
        print("[harness] Filling Step 1 API keys...")
        page.fill('input[name="openrouter_api_key"]', or_key)
        page.fill('input[name="rapidapi_key"]', rapidapi_key)
        if google_key:
            try:
                page.fill('input[name="google_api_key"]', google_key)
            except Exception:
                pass  # Field may not exist
        # Scope the submit click to the keys form — the page also has the
        # disabled Step 2 form whose generic `button[type="submit"]` selector
        # would otherwise match first and hang waiting for it to enable.
        page.click('form[action="/onboarding/keys"] button[type="submit"]')
        page.wait_for_load_state("networkidle")
        snapshot("turn-00-keys-saved")

        # ── Step 2: Start Interview ───────────────────────────────────────────
        print("[harness] Clicking Start Interview...")

        # Criterion: Start button loading state — read the form's x-data attr
        # via page.locator (re-evaluated lazily) rather than holding a stale
        # ElementHandle that Alpine/HTMX may detach from the DOM.
        start_form_loc = page.locator('form[action*="/onboarding/interview/start"]')
        x_data = start_form_loc.get_attribute("x-data") or ""
        has_alpine_loading = "starting" in x_data
        findings.add(
            "start_button_loading_state",
            "0",
            "PASS" if has_alpine_loading else "REVIEW",
            str(snapshot("turn-00-before-start")),
        )

        # Use page.click(selector) — auto-waits for visible+enabled and
        # re-evaluates the selector each retry, surviving DOM re-attachment.
        page.click('form[action*="/onboarding/interview/start"] button[type="submit"]')

        # Wait for redirect to /onboarding/interview/{sid}
        try:
            page.wait_for_url(re.compile(r"/onboarding/interview/[^/]+$"), timeout=30_000)
        except PWTimeout:
            page.wait_for_load_state("networkidle")
        snapshot("turn-01-interview-start")

        # ── Interview loop ────────────────────────────────────────────────────
        turn_idx = 0
        prev_cost = 0.0
        nav_cost_incremented = False
        markdown_checked = False
        file_badge_checked = False
        auto_scroll_checked = False

        # Phase scoping state — restricts corpus matching to within-phase
        # candidates so prompt revisions that reorder/add questions don't
        # cause cross-phase contamination (#405 Issue 2).
        current_phase_idx = 0
        phase_relative_turn = 0
        phase_ranges = compute_phase_ranges(corpus)
        print(f"[harness] Corpus phase ranges (0-based, half-open): {phase_ranges}")

        def count_assistant_bubbles() -> int:
            return len(page.query_selector_all("[data-role='assistant']"))

        prev_bubble_count = count_assistant_bubbles()

        for turn_num in range(1, max_turns + 1):
            # Read the latest assistant message
            bubbles = page.query_selector_all("[data-role='assistant']")
            if not bubbles:
                print(f"[harness] Turn {turn_num}: no assistant bubbles found, waiting...")
                try:
                    page.wait_for_selector("[data-role='assistant']", timeout=15_000)
                    bubbles = page.query_selector_all("[data-role='assistant']")
                except PWTimeout:
                    findings.add(
                        "markdown_rendered_in_assistant_bubbles", turn_num, "FAIL", "No assistant bubble appeared"
                    )
                    break

            latest_bubble = bubbles[-1]
            assistant_text = latest_bubble.inner_text() or ""

            # Criterion: markdown rendered (no raw ** or ### in first assistant bubble)
            if not markdown_checked and turn_num == 1:
                raw_md_visible = "**" in assistant_text or "###" in assistant_text
                snap_path = snapshot(f"turn-{turn_num:02d}-after-kickoff")
                findings.add(
                    "markdown_rendered_in_assistant_bubbles",
                    turn_num,
                    "FAIL" if raw_md_visible else "PASS",
                    str(snap_path),
                )
                markdown_checked = True

            # Criterion: FILE block badge (check once after a FILE-emitting turn)
            if not file_badge_checked:
                dom = page.content()
                has_raw_file = "<<<FILE:" in dom
                has_badge = "captured-file" in dom
                if has_raw_file or has_badge:
                    snap_path = snapshot(f"turn-{turn_num:02d}-file-emission")
                    findings.add(
                        "no_raw_file_block_in_dom",
                        turn_num,
                        "FAIL" if has_raw_file else "PASS",
                        str(snap_path),
                    )
                    findings.add(
                        "captured_file_badge_present",
                        turn_num,
                        "PASS" if has_badge else "FAIL",
                        str(snap_path),
                    )
                    file_badge_checked = True

            # Criterion: nav cost badge increments
            current_cost = read_cost()
            if not nav_cost_incremented and current_cost > prev_cost:
                findings.add(
                    "nav_cost_badge_increments",
                    turn_num,
                    "PASS",
                    f"cost {prev_cost:.4f} → {current_cost:.4f}",
                )
                nav_cost_incremented = True

            cost_log.record(current_cost)

            # Criterion: auto-scroll (check scroll position vs scrollHeight)
            if not auto_scroll_checked and turn_num >= 2:
                try:
                    scroll_ratio = page.evaluate("""() => {
                        const el = document.getElementById('messages');
                        if (!el) return 0;
                        const gap = el.scrollHeight - el.scrollTop - el.clientHeight;
                        return gap;
                    }""")
                    # Within 50px of bottom is "approaches scrollHeight"
                    snap_path = snapshot(f"turn-{turn_num:02d}-scroll-check")
                    findings.add(
                        "auto_scroll_approaches_scroll_height",
                        turn_num,
                        "PASS" if scroll_ratio <= 50 else "REVIEW",
                        f"gap_from_bottom={scroll_ratio}px, snapshot={snap_path}",
                    )
                    auto_scroll_checked = True
                except Exception:
                    pass

            # Criterion: finalize block populated without reload
            finalize_populated = page.query_selector("#finalize-block form") is not None
            if finalize_populated:
                snap_path = snapshot(f"turn-{turn_num:02d}-finalize-populated")
                findings.add(
                    "finalize_block_populated_without_reload",
                    turn_num,
                    "PASS",
                    str(snap_path),
                )
                print(f"[harness] Turn {turn_num}: finalize block populated — interview complete.")
                # Capture transcript turn
                transcript_turns.append({"role": "assistant", "text": assistant_text})
                break

            # Cost ceiling check
            if current_cost > cost_ceiling_usd:
                findings.add(
                    "final_cost_within_ceiling",
                    turn_num,
                    "REVIEW",
                    f"cost {current_cost:.4f} exceeds ceiling {cost_ceiling_usd:.2f} at turn {turn_num}",
                )
                print(f"[harness] Cost ceiling ${cost_ceiling_usd:.2f} exceeded at turn {turn_num}. Stopping.")
                break

            # Detect phase advance from this assistant turn before picking
            # an answer. If we just crossed a phase boundary, reset the
            # within-phase positional counter so the next user message is
            # the *first* answer in the new phase.
            new_phase_idx = advance_phase_idx(assistant_text, current_phase_idx)
            if new_phase_idx != current_phase_idx:
                print(f"[harness] Phase advance: {current_phase_idx} → {new_phase_idx} (triggered at turn {turn_num})")
                current_phase_idx = new_phase_idx
                phase_relative_turn = 0

            # Pick answer from corpus, restricted to within-phase candidates.
            answer, reason = pick_answer(
                turn_idx,
                assistant_text,
                corpus,
                intent_map,
                current_phase_idx=current_phase_idx,
                phase_relative_turn=phase_relative_turn,
                phase_ranges=phase_ranges,
            )
            phase_relative_turn += 1

            if reason == "review":
                findings.add(
                    f"answer_match_turn_{turn_num}",
                    turn_num,
                    "REVIEW",
                    f"New question not in corpus at turn_idx={turn_idx}: {assistant_text[:80]!r}",
                )

            print(f"[harness] Turn {turn_num} ({reason}): {shlex.quote(answer[:60])}")
            transcript_turns.append({"role": "assistant", "text": assistant_text})
            transcript_turns.append({"role": "user", "text": answer})

            # Type and send the answer
            try:
                page.wait_for_selector("textarea", timeout=10_000)
                page.fill("textarea", answer)
                prev_bubble_count = count_assistant_bubbles()
                # The hx-post attribute is on the <form>, not the button.
                page.click('form[hx-post*="/turn"] button[type="submit"]')
                # Wait for HTMX to append a new assistant bubble
                page.wait_for_function(
                    f"document.querySelectorAll('[data-role=\\'assistant\\']').length > {prev_bubble_count}",
                    timeout=60_000,
                )
            except PWTimeout:
                print(f"[harness] Turn {turn_num}: timeout waiting for assistant response.")
                break

            prev_cost = current_cost
            turn_idx += 1

            # Capture DOM snapshot every turn
            snapshot(f"turn-{turn_num:02d}-post-turn")

        else:
            # Exhausted max_turns without finalize
            findings.add(
                "finalize_block_populated_without_reload",
                max_turns,
                "FAIL",
                f"Finalize block not populated after {max_turns} turns.",
            )

        # If finalize block is populated, click Finalize
        if page.query_selector("#finalize-block form") is not None:
            print("[harness] Clicking Finalize...")
            try:
                page.click("#finalize-block form button[type='submit']")
                page.wait_for_load_state("networkidle")
                snapshot("finalize-complete")
            except Exception as exc:
                print(f"[harness] Finalize click error: {exc}")

        # Final cost criterion
        final_cost = cost_log.total()
        if not any(r.criterion == "final_cost_within_ceiling" for r in findings.rows):
            findings.add(
                "final_cost_within_ceiling",
                "final",
                "PASS" if final_cost <= cost_ceiling_usd else "REVIEW",
                f"final cost ${final_cost:.4f}, ceiling ${cost_ceiling_usd:.2f}",
            )

        # Add missing file-badge findings if never triggered (no emission turn seen)
        if not file_badge_checked:
            findings.add("no_raw_file_block_in_dom", "N/A", "REVIEW", "No FILE block emission turn observed")
            findings.add("captured_file_badge_present", "N/A", "REVIEW", "No FILE block emission turn observed")
        if not nav_cost_incremented:
            findings.add("nav_cost_badge_increments", "N/A", "REVIEW", "Cost never incremented above 0")
        if not auto_scroll_checked:
            findings.add("auto_scroll_approaches_scroll_height", "N/A", "REVIEW", "Never reached turn 2")

        browser.close()

    # ── Write outputs ─────────────────────────────────────────────────────────
    # Transcript
    transcript_md_lines = [f"# Walkthrough Transcript\n\nGenerated: {datetime.now(UTC).isoformat()}\n"]
    for i, turn in enumerate(transcript_turns, start=1):
        role = turn["role"].upper()
        transcript_md_lines.append(f"## Turn {i} — {role}\n\n{turn['text']}\n")
    (output_dir / "transcript.md").write_text("\n".join(transcript_md_lines), encoding="utf-8")

    # Console messages
    (output_dir / "console-messages.json").write_text(
        json.dumps(console_messages, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # Cost summary
    (output_dir / "cost-summary.json").write_text(json.dumps(cost_log.to_summary(), indent=2), encoding="utf-8")

    # Findings
    (output_dir / "findings.md").write_text(findings.to_markdown(), encoding="utf-8")

    print(f"\n[harness] Done. Outputs in: {output_dir}")
    print(f"[harness] Final cost: ${cost_log.total():.4f}")
    print(f"[harness] Exit code: {findings.exit_code()}")

    return findings


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Autonomous Playwright walkthrough harness for findajob onboarding.")
    repo_root = Path(__file__).resolve().parent.parent
    default_corpus = repo_root / "tests" / "fixtures" / "walkthrough" / "corpus_transcript.md"
    default_help = f"Path to replay corpus transcript (default: {default_corpus.relative_to(repo_root)})"
    parser.add_argument("--base-url", required=True, help="Base URL of the findajob instance")
    parser.add_argument("--replay-from", type=Path, default=default_corpus, help=default_help)
    parser.add_argument("--output-dir", type=Path, required=True, help="Directory for output artifacts")
    parser.add_argument(
        "--secrets-file",
        type=Path,
        default=Path("~/.secrets").expanduser(),
        help="Path to secrets file (default: ~/.secrets)",
    )
    parser.add_argument(
        "--max-turns",
        type=int,
        default=150,
        help="Hard ceiling on interview turns (default: 150)",
    )
    parser.add_argument(
        "--cost-ceiling-usd",
        type=float,
        default=7.0,
        help="Cost ceiling in USD before harness stops (default: 7.0)",
    )
    parser.add_argument(
        "--browser-channel",
        default=None,
        help=(
            "Playwright browser channel (e.g. 'chrome' to use system-installed Google Chrome). "
            "Default: bundled chromium."
        ),
    )
    args = parser.parse_args()

    # Load secrets (never from argv — stays out of ps output)
    try:
        secrets = load_secrets(args.secrets_file)
    except (FileNotFoundError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(2)

    # Load corpus
    try:
        corpus = load_corpus(args.replay_from)
    except (FileNotFoundError, ValueError) as exc:
        print(f"ERROR loading corpus: {exc}", file=sys.stderr)
        sys.exit(2)

    print(f"[harness] Corpus loaded: {corpus.turn_count} turns, anchors: {corpus.phase_anchors}")

    findings = run_walkthrough(
        base_url=args.base_url,
        corpus=corpus,
        output_dir=args.output_dir,
        secrets=secrets,
        max_turns=args.max_turns,
        cost_ceiling_usd=args.cost_ceiling_usd,
        browser_channel=args.browser_channel,
    )

    sys.exit(findings.exit_code())


if __name__ == "__main__":
    main()
