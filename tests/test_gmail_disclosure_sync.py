"""Asserts the disclosure language stays in sync between two surfaces.

The Jinja partial `src/findajob/web/templates/_gmail_disclosure.html` is
the source of truth for the disclosure rendered on `/config/gmail/` and
the onboarding gate. `docs/getting-started/gmail.md` repeats the same
language statically so the GitHub-rendered view shows the disclosure
*before* a stranger deploys findajob (the moment they're deciding whether
to trust the project with Gmail credentials — #796 sub-finding B).

Both files must say the same thing. This test normalizes both to a flat
token stream (HTML tags stripped, entities resolved, markdown syntax
stripped, lowercased, non-alphanumeric collapsed) and asserts the two
token sequences match as a bidirectional subsequence — every word in
each file appears in the other in the same order, with no excess.

The bidirectional check matters: the partial is the in-app
credential-entry surface (the page where the user types their Gmail
app password). If only the doc-direction were checked, someone could
quietly weaken the partial — removing "never sent to any LLM" from the
in-app disclosure — and the test would still pass because the
partial's shorter token list is trivially a subsequence of the doc's.
That failure mode is the privacy-disclosure equivalent of the bug
that triggered #796 in the first place.

When the test fails, the message names the specific token that broke
the match in whichever direction failed, plus surrounding context from
both files, so maintainers can locate the diverged sentence without
re-reading both surfaces.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PARTIAL = REPO / "src" / "findajob" / "web" / "templates" / "_gmail_disclosure.html"
DOC = REPO / "docs" / "getting-started" / "gmail.md"

# The doc's disclosure section runs from the "## What findajob will and
# won't access" heading to the next H2.
_DOC_SECTION_START_RE = re.compile(r"^## What findajob will and won't access\s*$", re.MULTILINE)
_DOC_SECTION_END_RE = re.compile(r"^## ", re.MULTILINE)

# The maintainer-pointer paragraph at the bottom of the doc's disclosure
# section is not part of the partial; drop it before comparing.
_DOC_FOOTER_RE = re.compile(
    r"The single source of truth for this text.*?must touch both files\.",
    re.DOTALL,
)

_HTML_TAG_RE = re.compile(r"<[^>]+>")
_JINJA_RE = re.compile(r"\{\{[^}]+\}\}|\{%[^%]+%\}|\{#.*?#\}", re.DOTALL)
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_HTML_ENTITY_RE = re.compile(r"&#?[a-zA-Z0-9]+;")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
_WHITESPACE_RE = re.compile(r"\s+")


def _extract_doc_section() -> str:
    body = DOC.read_text(encoding="utf-8")
    start_match = _DOC_SECTION_START_RE.search(body)
    assert start_match is not None, "gmail.md is missing the disclosure H2 heading"
    after_heading = body[start_match.end() :]
    end_match = _DOC_SECTION_END_RE.search(after_heading)
    assert end_match is not None, "gmail.md disclosure section has no following H2 terminator"
    return after_heading[: end_match.start()]


def _normalize(text: str) -> list[str]:
    """Reduce text to a list of lowercase word tokens.

    Strips HTML tags, resolves the four common HTML entities so
    angle-bracketed placeholders like `&lt;your-handle&gt;` survive into
    the comparison (and are then stripped uniformly by HTML-tag removal,
    matching the doc's `<your-handle>` literal). Markdown emphasis and
    link syntax are stripped; the visible link text is preserved.
    """
    # Resolve the common HTML entities BEFORE stripping tags, so the
    # `<your-handle>` placeholder is treated symmetrically in both files.
    text = text.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&").replace("&nbsp;", " ")
    text = _JINJA_RE.sub("", text)
    text = _HTML_TAG_RE.sub("", text)
    text = _HTML_ENTITY_RE.sub("", text)
    text = _MD_LINK_RE.sub(r"\1", text)
    text = text.replace("**", "").replace("__", "")  # markdown bold
    text = text.lower()
    text = _NON_ALNUM_RE.sub(" ", text)
    text = _WHITESPACE_RE.sub(" ", text).strip()
    return text.split()


def test_disclosure_partial_exists() -> None:
    assert PARTIAL.exists(), f"Source-of-truth partial missing: {PARTIAL}"


def test_gmail_doc_exists() -> None:
    assert DOC.exists(), f"User-facing doc missing: {DOC}"


def _check_subsequence(
    source_tokens: list[str],
    target_tokens: list[str],
    source_label: str,
    target_label: str,
) -> None:
    """Assert source_tokens appears as a subsequence of target_tokens.

    On mismatch, raises AssertionError naming the diverging token, its
    index in the source, and context windows from both sides.
    """
    target_cursor = 0
    for source_idx, token in enumerate(source_tokens):
        try:
            found_at = target_tokens.index(token, target_cursor)
        except ValueError:
            source_context = " ".join(source_tokens[max(0, source_idx - 5) : source_idx + 6])
            target_context = " ".join(target_tokens[max(0, target_cursor - 5) : target_cursor + 6])
            raise AssertionError(
                f"Disclosure drift: {target_label} is missing prose from {source_label}.\n"
                f"{source_label} token {source_idx} ({token!r}) not found in {target_label} "
                f"after position {target_cursor}.\n\n"
                f"{source_label} context (around token {source_idx}):\n  …{source_context}…\n\n"
                f"{target_label} context (around cursor {target_cursor}):\n  …{target_context}…\n\n"
                f"Either restore {source_label}'s wording in {target_label}, or update "
                f"{source_label} if {target_label}'s wording is the new canonical version."
            ) from None
        target_cursor = found_at + 1


def test_disclosure_text_matches_partial() -> None:
    """Bidirectional subsequence equivalence between the partial and the doc section.

    The H2 heading and the maintainer footer in gmail.md are stripped
    before comparison — those are legitimate doc-only framing. Anything
    else that diverges in either direction is drift the test surfaces.
    """
    partial_tokens = _normalize(PARTIAL.read_text(encoding="utf-8"))
    doc_section = _extract_doc_section()
    doc_section = _DOC_FOOTER_RE.sub("", doc_section)
    doc_tokens = _normalize(doc_section)

    # Direction 1: every partial token must appear in the doc. Catches
    # "doc dropped a privacy claim the partial still asserts."
    _check_subsequence(partial_tokens, doc_tokens, "_gmail_disclosure.html", "gmail.md")

    # Direction 2: every doc-section token must appear in the partial.
    # Catches the inverse — "partial was quietly weakened; the in-app
    # credential-entry surface now says less than the doc promises."
    # This is the more privacy-sensitive direction, since the partial
    # is what users see when they're about to type their Gmail app
    # password.
    _check_subsequence(doc_tokens, partial_tokens, "gmail.md", "_gmail_disclosure.html")
