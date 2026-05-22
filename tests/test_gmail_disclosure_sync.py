"""Asserts the disclosure language stays in sync between two surfaces.

The Jinja partial `src/findajob/web/templates/_gmail_disclosure.html` is
the source of truth for the disclosure rendered on `/config/gmail/` and
the onboarding gate. `docs/getting-started/gmail.md` repeats the same
language statically so the GitHub-rendered view shows the disclosure
*before* a stranger deploys findajob (the moment they're deciding whether
to trust the project with Gmail credentials — #796 sub-finding B).

Both files must say the same thing. This test normalizes both to a flat
token stream (HTML tags stripped, entities resolved, markdown syntax
stripped, lowercased, non-alphanumeric collapsed) and asserts the
partial's token sequence appears as a subsequence of the doc's token
sequence — i.e., every word from the partial appears in the doc in the
same order, with extra contextual words allowed in the doc (H2 heading
text, footer pointer, first-time-reader framing).

When the test fails, the message names the specific token that broke the
match and the surrounding context, so maintainers can locate the diverged
sentence without re-reading both files.
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


def test_disclosure_text_matches_partial() -> None:
    """The partial's tokens must appear as a subsequence of the doc's tokens.

    Asymmetric, not strict equality: the doc may include extra framing
    the partial doesn't carry (H2 heading text, maintainer footer,
    first-time-reader context). What must NOT happen is the partial
    saying something the doc omits — that's a disclosure-gap bug on the
    pre-deploy surface, exactly the failure mode this test exists to
    catch.
    """
    partial_tokens = _normalize(PARTIAL.read_text(encoding="utf-8"))
    doc_section = _extract_doc_section()
    doc_section = _DOC_FOOTER_RE.sub("", doc_section)
    doc_tokens = _normalize(doc_section)

    doc_cursor = 0
    for partial_idx, token in enumerate(partial_tokens):
        try:
            found_at = doc_tokens.index(token, doc_cursor)
        except ValueError:
            partial_context = " ".join(partial_tokens[max(0, partial_idx - 5) : partial_idx + 6])
            doc_context = " ".join(doc_tokens[max(0, doc_cursor - 5) : doc_cursor + 6])
            raise AssertionError(
                "Disclosure drift: gmail.md is missing prose from the partial.\n"
                f"Partial token {partial_idx} ({token!r}) not found in gmail.md "
                f"after position {doc_cursor}.\n\n"
                f"Partial context (around token {partial_idx}):\n  …{partial_context}…\n\n"
                f"Doc context (around cursor {doc_cursor}):\n  …{doc_context}…\n\n"
                "Either restore the partial's wording in gmail.md, or update the "
                "partial if the doc's wording is the new canonical version."
            ) from None
        doc_cursor = found_at + 1
