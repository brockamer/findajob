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
import subprocess

from findajob.paths import AICHAT

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


_REDACTION_PROMPT_HEADER = (
    "You are processing voice samples (the candidate's own personal long-form prose) "
    "to be used as STYLE calibration for cover letters and outreach. The text below "
    "has already been stripped of markdown structure. Your job is to generalize "
    "personal identifiers that the candidate may not have thought to scrub, while "
    "preserving their natural voice and prose flow.\n"
    "\n"
    "REDACT (replace with generic equivalents):\n"
    "- Specific dates that anchor the candidate in time → "
    '"around that time", "a few years ago", "in those days"\n'
    "- Named third parties (friends, partners, doctors, teachers, colleagues by "
    'name) → "a friend", "a teacher", "a colleague"\n'
    "- Exact geographic specifiers (cities, states, regions, named neighborhoods, "
    'named freeways, named landmarks) → "the city", "a small town", '
    '"another state", "the freeway"\n'
    "- Named institutions (treatment facilities, hospitals, universities, specific "
    'employers, named programs) → "the program", "the hospital", "a university", '
    '"an employer"\n'
    '- Exact dollar amounts → "a lot", "a meaningful amount"\n'
    "- Exact durations that combined with other context would identify the candidate "
    '(e.g., "the six year relationship I had until 2019") → "a long relationship"\n'
    "- Phone numbers, email addresses, URLs → strip entirely\n"
    "\n"
    "PRESERVE EXACTLY:\n"
    "- Every word of the actual prose that is not a specific identifier\n"
    "- Sentence structure, rhythm, parenthetical asides, em-dashes, contractions\n"
    "- Typos, idioms, idiosyncratic word choices, deliberate emphasis (CAPS, italics)\n"
    "- Paragraph breaks (double newlines)\n"
    "- The candidate's own name if it appears (it is their voice)\n"
    "- Generic vocabulary: industry terms, common nouns, public figures, well-known "
    "concepts, named recovery programs (AA, NA, SMART Recovery), books, methodologies\n"
    "\n"
    "RULES:\n"
    "- Conservative bias: when in doubt, keep the prose as-is. False positives on "
    "stripping are worse than false positives on keeping.\n"
    '- Do NOT rephrase, summarize, condense, or "improve" any sentence. Voice signal '
    "lives in unaided writing.\n"
    "- Do NOT correct typos, grammar, or punctuation.\n"
    "- Output only the redacted text. No preamble, no commentary, no markdown code "
    "fences, no closing notes about what you changed.\n"
    "\n"
    "TEXT TO REDACT:\n"
)


def redact_voice_samples(cleaned: str, timeout: int = 120) -> tuple[str, bool]:
    """Generalize PII via Opus 4.7, preserving voice.

    Returns ``(redacted_text, success)``. On LLM failure (binary missing,
    timeout, non-zero exit, empty stdout) returns ``(cleaned, False)`` so the
    caller can degrade gracefully and warn the user.
    """
    if not cleaned:
        return "", True

    prompt = _REDACTION_PROMPT_HEADER + cleaned
    cmd = [AICHAT, "-m", "openrouter:anthropic/claude-opus-4.7", "-S", prompt]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return cleaned, False

    if result.returncode != 0:
        return cleaned, False

    redacted = result.stdout.strip()
    if not redacted:
        return cleaned, False

    return redacted, True


def process_voice_samples(raw: str, redact: bool = True, timeout: int = 120) -> tuple[str, bool]:
    """Clean structural markdown, then optionally LLM-redact PII.

    Returns ``(final_text, redaction_succeeded)``. When ``redact=False`` or the
    cleaned text is empty, ``redaction_succeeded`` is ``True`` (nothing to do).
    When the LLM call fails, returns the cleaned text with ``False`` so the
    caller can flag the degradation.
    """
    cleaned = clean_voice_samples(raw)
    if not cleaned or not redact:
        return cleaned, True
    return redact_voice_samples(cleaned, timeout=timeout)
