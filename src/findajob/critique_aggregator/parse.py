"""Parse a recruiter_critic ``critique.md`` body into its three sections (#265).

The critic emits ≤150 words of free prose under three inline bold labels —
Generic / Weak / Missing — in two formats seen in the live corpus:

    Format A:  ``1. **Generic.**  ...``
    Format B:  ``**Generic:**  ...``

``parse_critique`` splits either format. Section text is preserved verbatim
(whole sentences, not stripped fragments) so the recruiter's voice carries
through into the aggregate report.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Matches a section label at line start, with or without a leading "N." and
# with either a trailing period or colon before the closing ``**``.
_LABEL_RE = re.compile(
    r"(?:^|\n)[ \t]*(?:\d+\.[ \t]*)?\*\*(Generic|Weak|Missing)[.:]?\*\*",
    re.IGNORECASE,
)

# The critic cites offending lines in straight or curly double quotes.
_QUOTE_RE = re.compile(r"[\"“]([^\"“”]+)[\"”]")


@dataclass
class CritiqueSections:
    """The three labeled sections of one critique, verbatim."""

    generic: str
    weak: str
    missing: str


def parse_critique(text: str) -> CritiqueSections:
    """Split a critique body into Generic / Weak / Missing sections.

    Unmatched sections come back as empty strings. The text following each
    label runs until the next label (or end of body).
    """
    matches = list(_LABEL_RE.finditer(text))
    out = {"generic": "", "weak": "", "missing": ""}
    for i, match in enumerate(matches):
        label = match.group(1).lower()
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        out[label] = text[start:end].strip()
    return CritiqueSections(generic=out["generic"], weak=out["weak"], missing=out["missing"])


def extract_quotes(section_text: str) -> list[str]:
    """Return the double-quoted fragments the critic cited, in order.

    These are the verbatim offending lines — the best target for source-line
    anchoring. A section with no citation (common for "Missing" gaps) returns
    an empty list.
    """
    return [m.group(1).strip() for m in _QUOTE_RE.finditer(section_text)]


def sentence_for_quote(section_text: str, quote: str) -> str:
    """Return the recruiter's sentence containing ``quote``, verbatim.

    Carries the skeptical-recruiter voice into the report rather than a bare
    fragment. Falls back to the quote itself if it isn't found.
    """
    idx = section_text.find(quote)
    if idx == -1:
        return quote
    start = 0
    for sep in (". ", "\n"):
        prev = section_text.rfind(sep, 0, idx)
        if prev != -1:
            start = max(start, prev + len(sep))
    end = len(section_text)
    after = idx + len(quote)
    for sep in (". ", "\n"):
        nxt = section_text.find(sep, after)
        if nxt != -1:
            end = min(end, nxt + (1 if sep == ". " else 0))
    return section_text[start:end].strip()
