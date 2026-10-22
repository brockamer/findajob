"""Checked resume change report (#1099).

``resume_change_reviewer`` used to compare the master and tailored resumes
free-form, and it reported edits the tailored resume does not contain. It now
gets ground truth — ``resume_diff_summary``, computed here — and returns JSON
whose every quoted passage ``check_change_report`` looks up in the document
its field names. Quotes that are not found stay in the report, marked
``[UNVERIFIED]``, so the defect is visible instead of hidden.

Callers pass the master resume with marked sections removed
(``resume_markers.split_marked_sections``), or every marked line would read
as a removal.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from findajob.llm_parsing import extract_json_payload
from findajob.prep.resume_markers import normalize_for_match

PARSE_FAILED_NOTE = (
    "The change reviewer's output could not be parsed, so none of it was checked. "
    "Treat every entry below as unverified."
)
_CHECKED_NOTE = (
    "Every quoted passage below was checked against the master and tailored resumes. "
    "A line marked [UNVERIFIED] quotes text the check could not find in the right document: "
    "do not rely on it."
)
_UNVERIFIED = "[UNVERIFIED] "
_LINE_MARKER_RE = re.compile(r"^\s*(?:#{1,6}\s+|[-*+]\s+|\d+\.\s+)")


@dataclass(frozen=True)
class CheckedReport:
    markdown: str
    claims: int
    unsupported: int
    parse_failed: bool


def _line_identity(line: str) -> str:
    return normalize_for_match(_LINE_MARKER_RE.sub("", line, count=1))


def _section_order(text: str) -> str:
    return " | ".join(line[3:].strip() for line in text.splitlines() if line.startswith("## "))


def _only_in(text: str, other: str) -> list[str]:
    other_ids = {_line_identity(line) for line in other.splitlines()}
    return [
        line.strip() for line in text.splitlines() if _line_identity(line) and _line_identity(line) not in other_ids
    ]


def resume_diff_summary(master_text: str, tailored_text: str) -> str:
    """Section order of both documents and every line found in only one of
    them. Lines are compared after dropping list/heading markers, case and
    formatting, so a moved or re-bulleted line is not reported."""
    added = [f"+ {line}" for line in _only_in(tailored_text, master_text)] or ["(none)"]
    removed = [f"- {line}" for line in _only_in(master_text, tailored_text)] or ["(none)"]
    return (
        f"SECTION ORDER IN MASTER: {_section_order(master_text)}\n"
        f"SECTION ORDER IN TAILORED: {_section_order(tailored_text)}\n\n"
        "LINES ONLY IN TAILORED (new or reworded):\n" + "\n".join(added) + "\n\n"
        "LINES ONLY IN MASTER (removed or reworded):\n" + "\n".join(removed) + "\n"
    )


def _field(item: object, key: str) -> str:
    if not isinstance(item, dict):
        return ""
    value = item.get(key)
    return value.strip() if isinstance(value, str) else ""


def _found(needle: str, haystack_norm: str) -> bool:
    n = normalize_for_match(needle)
    return bool(n) and n in haystack_norm


def _items(payload: dict[str, Any], key: str) -> list[object]:
    value = payload.get(key)
    return value if isinstance(value, list) else []


def _join(head: str, section: str, tail: str) -> str:
    line = head
    if section:
        line += f" ({section})"
    if tail:
        line += f": {tail}"
    return line


def _parse_json(raw_output: str) -> object:
    """Bare JSON first. ``extract_json_payload`` is only the fallback for
    fenced or prose-wrapped output: given a bare object that contains a list,
    it cuts the text at the first ``[``."""
    for candidate in (raw_output.strip(), extract_json_payload(raw_output)):
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    return None


def check_change_report(raw_output: str, master_text: str, tailored_text: str) -> CheckedReport:
    """Parse the reviewer's JSON, check each quoted passage, and render the
    ``Resume Changes`` Markdown. Unparseable output is kept verbatim under a
    note saying none of it was checked."""
    payload = _parse_json(raw_output)
    if not isinstance(payload, dict):
        return CheckedReport(f"# Resume Changes\n\n{PARSE_FAILED_NOTE}\n\n{raw_output}\n", 0, 0, True)

    master_norm = normalize_for_match(master_text)
    tailored_norm = normalize_for_match(tailored_text)
    claims = 0
    unsupported = 0

    def claim(ok: bool, line: str, item: object) -> str:
        nonlocal claims, unsupported
        claims += 1
        if not isinstance(item, dict):
            unsupported += 1
            return f"- {_UNVERIFIED}{item}"
        if not ok:
            unsupported += 1
            return f"- {_UNVERIFIED}{line}"
        return f"- {line}"

    sections: list[tuple[str, list[str]]] = []

    reordered = []
    for item in _items(payload, "reordered_sections"):
        reordered.append(
            f"- {_join(_field(item, 'section'), '', _field(item, 'note'))}" if isinstance(item, dict) else f"- {item}"
        )
    sections.append(("Reordered sections", reordered))

    added = []
    for item in _items(payload, "added"):
        text = _field(item, "text")
        added.append(
            claim(
                _found(text, tailored_norm), _join(f'"{text}"', _field(item, "section"), _field(item, "reason")), item
            )
        )
    sections.append(("Added", added))

    reworded = []
    for item in _items(payload, "reworded"):
        before, after = _field(item, "before"), _field(item, "after")
        ok = _found(before, master_norm) and _found(after, tailored_norm)
        reworded.append(claim(ok, _join(f'Before: "{before}" / After: "{after}"', _field(item, "section"), ""), item))
    sections.append(("Reworded", reworded))

    removed = []
    for item in _items(payload, "removed"):
        text = _field(item, "text")
        ok = _found(text, master_norm) and not _found(text, tailored_norm)
        removed.append(claim(ok, _join(f'"{text}"', "", _field(item, "reason")), item))
    sections.append(("Removed", removed))

    missing = []
    for item in _items(payload, "missing_info"):
        placeholder = _field(item, "placeholder")
        missing.append(claim(_found(placeholder, tailored_norm), _join(placeholder, "", _field(item, "needs")), item))
    sections.append(("Missing info", missing))

    body = "\n\n".join(f"## {title}\n" + "\n".join(lines or ["- None"]) for title, lines in sections)
    return CheckedReport(f"# Resume Changes\n\n{_CHECKED_NOTE}\n\n{body}\n", claims, unsupported, False)
