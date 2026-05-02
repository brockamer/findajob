#!/usr/bin/env python3
"""Parse a prior walkthrough transcript into a replay corpus.

Transcript format: alternating sections delimited by Markdown headings:
  ## Turn N — USER
  <user message text>

  ## Turn N — ASSISTANT
  <assistant message text>

Exposes load_corpus(path) -> ReplayCorpus.

CLI usage (for sanity-checking):
  uv run python scripts/walkthrough_replay_corpus.py <transcript.md> --print-summary
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

# Heading pattern: ## Turn N — USER or ## Turn N — ASSISTANT
_TURN_HEADER_RE = re.compile(r"^##\s+Turn\s+(\d+)\s+—\s+(USER|ASSISTANT)\s*$", re.IGNORECASE)

# Heuristic phrases the assistant uses at phase transitions.
_PHASE_ANCHORS: dict[str, list[str]] = {
    "phase_1_end": [
        "let's move to phase 2",
        "moving on to phase 2",
        "phase 2",
        "now let's collect your documents",
        "share your resume",
        "paste your master resume",
    ],
    "phase_2_end": [
        "let's move to phase 3",
        "moving on to phase 3",
        "phase 3",
        "now let's talk about notifications",
        "ntfy",
    ],
    "phase_3_end": [
        "let's move to phase 4",
        "moving on to phase 4",
        "phase 4",
        "now let's talk about what to exclude",
        "exclusion",
    ],
    "phase_4_end": [
        "let's move to phase 5",
        "moving on to phase 5",
        "phase 5",
        "ready to capture everything",
        "i'll emit",
        "i'll write",
    ],
    "phase_5_end": [
        "finalize",
        "click finalize",
        "all done",
        "onboarding is complete",
        "you're all set",
    ],
}


@dataclass
class ReplayCorpus:
    """Extracted replay data from a prior walkthrough transcript."""

    user_messages: list[str] = field(default_factory=list)
    # turn index (1-based) → anchor name detected by heuristic on assistant text
    phase_anchors: dict[str, int] = field(default_factory=dict)
    # assistant messages, indexed same as user_messages (parallel, may be empty string at gaps)
    assistant_messages: list[str] = field(default_factory=list)

    @property
    def turn_count(self) -> int:
        return len(self.user_messages)


def load_corpus(path: Path) -> ReplayCorpus:
    """Parse a transcript.md file and return a ReplayCorpus.

    Raises FileNotFoundError if path does not exist.
    Raises ValueError if the file does not contain any recognizable turn headers.
    """
    if not path.exists():
        raise FileNotFoundError(f"Transcript not found: {path}")

    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()

    # Split into (turn_num, role, content) blocks
    blocks: list[tuple[int, str, str]] = []
    current_turn: int | None = None
    current_role: str | None = None
    current_lines: list[str] = []

    def _flush() -> None:
        if current_turn is not None and current_role is not None:
            content = "\n".join(current_lines).strip()
            blocks.append((current_turn, current_role, content))

    for line in lines:
        m = _TURN_HEADER_RE.match(line)
        if m:
            _flush()
            current_turn = int(m.group(1))
            current_role = m.group(2).upper()
            current_lines = []
        else:
            current_lines.append(line)
    _flush()

    if not blocks:
        raise ValueError(f"No turn headers found in {path}. Expected '## Turn N — USER/ASSISTANT' format.")

    # Determine max turn number to size the output lists
    max_turn = max(b[0] for b in blocks)

    user_msgs: list[str] = [""] * max_turn
    asst_msgs: list[str] = [""] * max_turn
    for turn_num, role, content in blocks:
        idx = turn_num - 1  # 0-based
        if role == "USER":
            user_msgs[idx] = content
        else:
            asst_msgs[idx] = content

    # Detect phase anchors from assistant text
    phase_anchors: dict[str, int] = {}
    for turn_num, role, content in blocks:
        if role != "ASSISTANT":
            continue
        lower = content.lower()
        for anchor_name, phrases in _PHASE_ANCHORS.items():
            if anchor_name in phase_anchors:
                continue  # first occurrence wins
            for phrase in phrases:
                if phrase in lower:
                    phase_anchors[anchor_name] = turn_num
                    break

    return ReplayCorpus(
        user_messages=user_msgs,
        assistant_messages=asst_msgs,
        phase_anchors=phase_anchors,
    )


def _print_summary(corpus: ReplayCorpus, path: Path) -> None:
    print(f"Transcript: {path}")
    print(f"Turn count: {corpus.turn_count}")
    print(f"Phase anchors: {corpus.phase_anchors}")
    print()
    print("Sample user messages (first 5, truncated to 120 chars):")
    for i, msg in enumerate(corpus.user_messages[:5], start=1):
        preview = msg.replace("\n", " ")[:120]
        print(f"  Turn {i}: {preview!r}")
    print()
    populated = sum(1 for m in corpus.user_messages if m.strip())
    print(f"Non-empty user turns: {populated}/{corpus.turn_count}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Parse a walkthrough transcript for replay.")
    parser.add_argument("transcript", type=Path, help="Path to transcript.md")
    parser.add_argument("--print-summary", action="store_true", help="Print a human-readable summary and exit")
    args = parser.parse_args()

    try:
        corpus = load_corpus(args.transcript)
    except (FileNotFoundError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    if args.print_summary:
        _print_summary(corpus, args.transcript)
    else:
        print(f"Loaded {corpus.turn_count} turns, {len(corpus.phase_anchors)} phase anchors.")
