"""Source-line anchoring tests for ``findajob.critique_aggregator.anchor`` (#265).

The critic paraphrases the same underlying resume/profile line differently in
every prep, so clustering on the quote *string* under-clusters fatally. The
fix: fuzzy-anchor each quoted critique fragment to the small, fixed set of
lines in master_resume.md + profile.md, then cluster by anchored-line identity.

This is the load-bearing claim of #265 — verified here with the real-corpus
failure case (two "glue" paraphrases that exact-match would scatter into noise).
All fixture content is synthetic (fictional candidate "Jordan Riley").
"""

from __future__ import annotations

from findajob.critique_aggregator.anchor import SourceLine, anchor_quote

# Synthetic stand-ins for master_resume.md + profile.md lines.
SOURCE = [
    SourceLine(
        "master_resume.md",
        360,
        '"Jordan acts as the glue across so many teams, bringing the lab teams and '
        'Ops teams together." | Sam, Director, Infrastructure | H1 2016',
    ),
    SourceLine(
        "master_resume.md",
        160,
        "Lab rack volume grew from ~350/year to ~700/year (2x) as NPI scope expanded.",
    ),
    SourceLine(
        "profile.md",
        12,
        "Comfortable working from bench to exec; translating between hardware engineering and operators.",
    ),
]


def test_paraphrased_quotes_anchor_to_same_source_line():
    # Two different critic paraphrases of the same testimonial line.
    q1 = "One of my directors once described me as the glue across lab and ops teams"
    q2 = "A former director called me the glue across lab teams"

    a1 = anchor_quote(q1, SOURCE)
    a2 = anchor_quote(q2, SOURCE)

    assert a1 is not None and a2 is not None
    # Both must land on the SAME source line — the whole point of #265.
    assert (a1.file, a1.line_no) == (a2.file, a2.line_no)
    assert a1.line_no == 360


def test_terse_realistic_paraphrase_still_anchors():
    # Real corpus paraphrases of the "glue" line score ~60-64 against their
    # source — too close to a 60 threshold for natural variance. A terser but
    # still-genuine paraphrase must anchor, not vanish into the noise tail.
    # Its best wrong-line distractor scores ~43, so the threshold has headroom.
    q = "I've been told I'm the glue holding the lab and ops teams together"

    anchored = anchor_quote(q, SOURCE)

    assert anchored is not None
    assert anchored.line_no == 360


def test_generated_opener_does_not_false_anchor():
    # A cover-letter opener generated fresh each prep — not in any source line.
    # It must NOT anchor, so it routes to the prompt-fix bucket, not a content edit.
    opener = "the cloud is being reshaped by AI in real time"

    assert anchor_quote(opener, SOURCE) is None
