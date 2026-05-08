"""Voice sample cleaning + redaction for onboarding (#262).

The onboarding interview can ask the user to paste long-form personal prose for
voice calibration of the cover_letter_writer and outreach_drafter roles.
This module turns that paste into a clean, generalized voice-samples.md file:

1. ``clean_voice_samples`` — pure-Python regex strip of structural markdown
   (headers, images, link syntax, bold/italic, blockquotes, code fences,
   horizontal rules, footnote markers, tables, HTML tags, frontmatter).
   Deterministic, no LLM, fully testable.

2. ``redact_voice_samples`` — LLM-backed (Opus 4.7) generalization of personal
   identifiers the operator may not have thought to scrub: specific dates,
   named third parties, exact geographic specifiers, named institutions,
   exact dollar amounts. Conservative bias: preserve prose flow above all.

3. ``process_voice_samples`` — convenience that runs both and returns the
   final text + a redaction-success flag (so the caller can warn the user
   if the LLM call failed and we wrote cleaned-but-not-redacted output).

The cleaning pass MUST NOT rephrase, summarize, condense, or correct prose.
Voice signal lives in unaided writing.
"""

from __future__ import annotations

import re
import sqlite3
import time

from findajob.audit import log_event
from findajob.cost_tracking import log_call, role_model
from findajob.llm.openrouter import OpenRouterError, complete

_FRONTMATTER_RE = re.compile(r"\A---\n.*?\n---\n", re.DOTALL)
_FENCED_CODE_BLOCK_RE = re.compile(r"^```[^\n]*\n.*?\n```\s*$", re.MULTILINE | re.DOTALL)
_HEADER_RE = re.compile(r"^#{1,6}\s+.*$", re.MULTILINE)
_MD_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_HTML_IMG_RE = re.compile(r"<img[^>]*>", re.IGNORECASE)
_BRACKET_IMAGE_RE = re.compile(r"\[image:[^\]]*\]", re.IGNORECASE)
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_BOLD_ITALIC_AST_RE = re.compile(r"\*\*\*(.+?)\*\*\*", re.DOTALL)
_BOLD_AST_RE = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)
_ITALIC_AST_RE = re.compile(r"(?<!\*)\*(?!\s)(.+?)(?<!\s)\*(?!\*)", re.DOTALL)
_BOLD_ITALIC_UND_RE = re.compile(r"___(.+?)___", re.DOTALL)
_BOLD_UND_RE = re.compile(r"__(.+?)__", re.DOTALL)
_ITALIC_UND_RE = re.compile(r"(?<!_)_(?!\s)(.+?)(?<!\s)_(?!_)", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`([^`]+)`")
_BLOCKQUOTE_RE = re.compile(r"^>\s?", re.MULTILINE)
_HR_DASH_RE = re.compile(r"^-{3,}\s*$", re.MULTILINE)
_HR_AST_RE = re.compile(r"^\*{3,}\s*$", re.MULTILINE)
_HR_UND_RE = re.compile(r"^_{3,}\s*$", re.MULTILINE)
_FOOTNOTE_MARKER_RE = re.compile(r"\[\^[^\]]+\]")
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_TABLE_LINE_RE = re.compile(r"^\s*\|.*\|\s*$", re.MULTILINE)
_TRIPLE_BLANK_RE = re.compile(r"\n{3,}")


def clean_voice_samples(raw: str) -> str:
    """Strip structural markdown noise without altering prose.

    Removes: YAML frontmatter, fenced code blocks (entire content), headers
    (whole line), images, link syntax (keeps visible text), bold/italic/code
    inline marks (keeps content), blockquote markers, horizontal rules,
    footnote markers, HTML tags, table rows.

    Preserves: every word of actual prose, paragraph breaks, contractions,
    typos, idioms, em-dashes, parenthetical asides.

    Returns the empty string if the input is blank or only contained
    structural markdown.
    """
    text = raw
    text = _FRONTMATTER_RE.sub("", text)
    text = _FENCED_CODE_BLOCK_RE.sub("", text)
    text = _HEADER_RE.sub("", text)
    text = _MD_IMAGE_RE.sub("", text)
    text = _HTML_IMG_RE.sub("", text)
    text = _BRACKET_IMAGE_RE.sub("", text)
    text = _MD_LINK_RE.sub(r"\1", text)
    text = _BOLD_ITALIC_AST_RE.sub(r"\1", text)
    text = _BOLD_AST_RE.sub(r"\1", text)
    text = _ITALIC_AST_RE.sub(r"\1", text)
    text = _BOLD_ITALIC_UND_RE.sub(r"\1", text)
    text = _BOLD_UND_RE.sub(r"\1", text)
    text = _ITALIC_UND_RE.sub(r"\1", text)
    text = _INLINE_CODE_RE.sub(r"\1", text)
    text = _BLOCKQUOTE_RE.sub("", text)
    text = _HR_DASH_RE.sub("", text)
    text = _HR_AST_RE.sub("", text)
    text = _HR_UND_RE.sub("", text)
    text = _FOOTNOTE_MARKER_RE.sub("", text)
    text = _HTML_TAG_RE.sub("", text)
    text = _TABLE_LINE_RE.sub("", text)
    text = _TRIPLE_BLANK_RE.sub("\n\n", text)
    return text.strip()


def redact_voice_samples(
    cleaned: str,
    timeout: int = 120,
    conn: sqlite3.Connection | None = None,
) -> tuple[str, bool]:
    """Generalize PII via Opus 4.7, preserving voice.

    Returns ``(redacted_text, success)``. On LLM failure returns
    ``(cleaned, False)`` so the caller can degrade gracefully and warn the user.

    ``conn`` is optional; when supplied, a cost_log row is written with
    API-authoritative cost from ``response.usage.cost``.
    """
    if not cleaned:
        return "", True

    start = time.time()
    try:
        result = complete(role="voice_processor", prompt=cleaned, timeout_s=timeout)
    except OpenRouterError as e:
        log_event(
            "voice_processor_failure",
            kind=e.kind,
            status_code=e.status_code,
            message=str(e)[:300],
        )
        return cleaned, False

    latency_ms = int((time.time() - start) * 1000)
    redacted = result.text.strip()
    if not redacted:
        return cleaned, False

    if conn is not None:
        try:
            log_call(
                conn,
                job_id=None,
                operation="voice_processor",
                model=role_model("voice_processor"),
                input_text=cleaned,
                output_text=result.text,
                latency_ms=latency_ms,
                success=True,
                cost_usd_override=result.cost_usd,
                input_tokens_override=result.prompt_tokens,
                output_tokens_override=result.completion_tokens,
            )
            conn.commit()
        except Exception as e:  # noqa: BLE001 — cost tracking is best-effort
            log_event("cost_log_failed", operation="voice_processor", error=f"{type(e).__name__}: {e}")

    return redacted, True


def process_voice_samples(
    raw: str,
    redact: bool = True,
    timeout: int = 120,
    *,
    conn: sqlite3.Connection | None = None,
) -> tuple[str, bool]:
    """Clean structural markdown, then optionally LLM-redact PII.

    Returns ``(final_text, redaction_succeeded)``. When ``redact=False`` or the
    cleaned text is empty, ``redaction_succeeded`` is ``True`` (nothing to do).
    When the LLM call fails, returns the cleaned text with ``False`` so the
    caller can flag the degradation.

    ``conn`` is forwarded to :func:`redact_voice_samples` so a cost_log row is
    written for the LLM call when supplied. None disables cost-logging.
    """
    cleaned = clean_voice_samples(raw)
    if not cleaned or not redact:
        return cleaned, True
    return redact_voice_samples(cleaned, timeout=timeout, conn=conn)
