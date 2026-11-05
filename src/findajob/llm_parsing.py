"""LLM-output JSON extraction + schema validation.

Two functions for the "the model returned text but we want a parsed
dict" problem:

- :func:`extract_json_payload` strips markdown fences and prose
  surrounding the JSON body. Best-effort recovery for scorer responses
  that drift from "JSON only" prompt instructions.
- :func:`validate_llm_json` parses + validates against a JSON Schema
  loaded from disk. Returns ``(parsed, None)`` on success or
  ``(None, error_message)`` on parse / schema failure — never raises.

Extracted from ``utils.py`` in M4.E2.I2 (#550). No logic changes.
"""

from __future__ import annotations

import json
import re

_FENCED_JSON_RE: re.Pattern[str] = re.compile(r"```(?:json|JSON)?\s*\n(.*?)\n```", re.DOTALL)

# Markdown-bold-around-keys drift (#638). DeepSeek V3.2 intermittently emits
# JSON with keys wrapped in `**` despite the prompt forbidding markdown.
# Matches both observed shapes from 2026-05-11 pipeline.jsonl:
#   Variant A: **score_status**: "scored",     (bold around bare keyword)
#   Variant B: **"relevance_score": 1,          (bold prefix on quoted key, no closing)
# Anchored on a line start or JSON delimiter (`{`, `,`) so it cannot match
# inside a JSON string value where legitimate `**` may appear as prose.
_BOLD_KEY_RE: re.Pattern[str] = re.compile(
    r'(?P<anchor>^|[{,]\s*)\*\*"?(?P<key>\w+)"?(?:\*\*)?\s*:',
    re.MULTILINE,
)


def extract_json_payload(raw_output: str) -> str:
    """Pull the JSON payload out of an LLM response that may wrap it in
    markdown fences, prose, or both.

    Handles, in order:
    1. Whole response is a fenced block (```...```, possibly with a
       language tag like ```json). Strip the opening fence and cut at
       a closing fence on its own line, discarding any trailing prose
       after it.
    2. Response contains a fenced ```json…``` block somewhere inside
       prose. Return the contents of the first such block.
    3. Response has prose before the JSON. Scan every '{' and '['
       character in order of occurrence and collect every position
       where `json.JSONDecoder().raw_decode` actually produces a value,
       then anchor on whichever of those decodes the longest span of
       text (ties go to the earliest position). This skips a bracket in
       lead-in prose that doesn't itself decode (e.g. a bracketed label
       or a markdown link before the real object or array); it also
       isn't fooled by a short decodable bracket in lead-in prose (e.g.
       "Score [7]: {...}"), since the real payload is normally the
       largest JSON value in the text. A bracket inside a string value
       or a nested list inside a bare object never out-competes the
       enclosing object/array, since the enclosing value's span always
       covers it.
    4. Otherwise return the input unchanged.

    This is best-effort recovery for scorer responses that drift from
    "JSON only" in the prompt despite explicit instructions; the parser
    that consumes the output still gates on `json.loads` + schema
    validation.
    """
    text = raw_output.strip()

    # Case 1: whole-response fenced block
    if text.startswith("```"):
        # Drop the opening fence line (which may carry a language tag
        # like "```json"), then cut at a closing fence that sits on its
        # own line rather than requiring it to be the last thing in the
        # string — a response can add prose after the closing fence.
        # Anchoring on a line-start fence (vs. a bare substring search)
        # means a "```" that appears mid-line inside a JSON string value
        # doesn't get mistaken for the close.
        first_newline = text.find("\n")
        if first_newline != -1:
            inner = text[first_newline + 1 :]
            close_match = re.search(r"^```", inner, re.MULTILINE)
            if close_match:
                return inner[: close_match.start()].strip()
            # No closing fence found — fall through to case 2 instead of
            # returning the unclosed block with trailing prose attached.

    # Case 2: fenced block embedded in prose. Match the first ```json
    # or ``` (with optional lang tag) ... ``` block.
    fence_match = _FENCED_JSON_RE.search(text)
    if fence_match:
        return fence_match.group(1).strip()

    # Defensive: strip markdown-bold around keys (#638) before the case-3
    # scan below so that raw_decode sees real JSON syntax instead of
    # failing to decode a bold-wrapped key and leaving the lead-in prose
    # un-anchored. Anchored regex only matches at line-start or after
    # JSON delimiters, so legitimate `**` inside string values is
    # preserved.
    text = _BOLD_KEY_RE.sub(r'\g<anchor>"\g<key>":', text)

    # Case 3: bare JSON preceded by prose. Scan every '{' and '[' in
    # order of occurrence, collect every position where raw_decode
    # actually produces a value, and anchor on whichever decodes the
    # longest span (ties go to the earliest position). A short decodable
    # bracket in lead-in prose (e.g. "Score [7]: {...}") loses to the
    # real payload because the real payload is normally the larger span.
    decoder = json.JSONDecoder()
    best: tuple[int, int] | None = None
    for match in re.finditer(r"[{\[]", text):
        idx = match.start()
        try:
            _value, end = decoder.raw_decode(text, idx)
        except json.JSONDecodeError:
            continue
        if best is None or (end - idx) > (best[1] - best[0]):
            best = (idx, end)
    if best is not None:
        text = text[best[0] :].strip()

    return text


def validate_llm_json(raw_output: str, schema_path: str) -> tuple[dict | None, str | None]:
    import jsonschema

    text = extract_json_payload(raw_output)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as e:
        return None, f"JSON parse: {e}"
    try:
        with open(schema_path) as f:
            schema = json.load(f)
        jsonschema.validate(parsed, schema)
    except jsonschema.ValidationError as e:
        return None, f"Schema: {e.message}"
    return parsed, None
