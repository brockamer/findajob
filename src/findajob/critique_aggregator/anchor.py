"""Fuzzy-anchor a quoted critique fragment to a source line (#265).

The critic paraphrases the same resume/profile line differently each prep, so
clustering on the quote string scatters one defect across many count-1 buckets.
Anchoring instead matches each quote against the small fixed set of lines in
master_resume.md + profile.md and clusters by anchored-line identity.

Matching ~150 known source lines is bounded and reliable; matching quotes
against each other is not. A quote that anchors to nothing (e.g. a cover-letter
opener generated fresh each prep) is intentionally left unanchored so it routes
to the prompt-fix bucket rather than a phantom content edit.
"""

from __future__ import annotations

from dataclasses import dataclass

from rapidfuzz import fuzz

# Calibrated against the real corpus: genuine paraphrases of a source line
# score ~52-64 against it via token_set_ratio, wrong-line distractors stay
# ≤~46, and freshly-generated text (cover-letter openers) sits ~33. 50 clears
# the positives with headroom while excluding both distractors and generated
# text — so a terse-but-real paraphrase anchors instead of vanishing as noise.
DEFAULT_THRESHOLD = 50.0


@dataclass(frozen=True)
class SourceLine:
    """One line of a source file, 1-based line number."""

    file: str
    line_no: int
    text: str


def anchor_quote(
    quote: str,
    source_lines: list[SourceLine],
    *,
    threshold: float = DEFAULT_THRESHOLD,
) -> SourceLine | None:
    """Return the best-matching source line for ``quote``, or ``None``.

    Uses token-set similarity so reordered/abridged paraphrases still match
    their origin line. Returns ``None`` when no line clears ``threshold``.
    """
    best: SourceLine | None = None
    best_score = 0.0
    for line in source_lines:
        score = fuzz.token_set_ratio(quote, line.text)
        if score > best_score:
            best_score = score
            best = line
    return best if best_score >= threshold else None
