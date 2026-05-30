"""Parser tests for ``findajob.critique_aggregator.parse`` (#265).

The recruiter_critic role emits ≤150 words of free prose labeled with three
inline bold sections — Generic / Weak / Missing — in TWO inconsistent formats
observed in the live corpus:

  Format A (numbered):   ``1. **Generic.**  ...``
  Format B (colon):      ``**Generic:**  ...``

The parser must split either format into the three sections. All fixture
content is synthetic (fictional candidate "Jordan Riley") — tracked test
files must not carry real operator resume content.
"""

from __future__ import annotations

from findajob.critique_aggregator.parse import extract_quotes, parse_critique

# Format A — numbered + bold + trailing period (numbered-format sample)
CRITIQUE_NUMBERED = """\
1. **Generic.** "passionate about building scalable systems" — reads like every
other cover letter. The opener "the cloud is being reshaped in real time" is filler.

2. **Weak.** "a former manager called me a force multiplier" — unattributed
hearsay, cut it. "improved efficiency by a lot" has no number.

3. **Missing.** Nothing in the bullets shows Kubernetes depth despite the JD
leaning on it. No SLO or error-budget language either.
"""


# Format B — bold + colon, no numbering (colon-format sample)
CRITIQUE_COLON = """\
**Generic:** "excited to bring my passion to your mission" is boilerplate close.
The opener "the PE round and a hot market" is context every applicant drops.

**Weak:** "a former director called me the connective tissue" — unverifiable
secondhand quote, drop it. "prevented thousands of hours" — estimated by whom?

**Missing:** You admit no Lustre depth, but the JD is parallel filesystems.
Lead harder on storage NPI or this gap sinks you.
"""


def test_parses_numbered_bold_format_into_three_sections():
    sections = parse_critique(CRITIQUE_NUMBERED)

    assert "passionate about building scalable systems" in sections.generic
    assert "force multiplier" in sections.weak
    assert "Kubernetes depth" in sections.missing
    # Section boundaries are respected — the hearsay line is Weak, not Generic.
    assert "force multiplier" not in sections.generic
    assert "Kubernetes depth" not in sections.weak


def test_parses_colon_bold_format_into_three_sections():
    sections = parse_critique(CRITIQUE_COLON)

    assert "boilerplate close" in sections.generic
    assert "connective tissue" in sections.weak
    assert "parallel filesystems" in sections.missing
    assert "connective tissue" not in sections.missing


def test_extract_quotes_pulls_cited_fragments_in_order():
    section = (
        '"passionate about building scalable systems" — reads like every other '
        'cover letter. The opener "the cloud is being reshaped in real time" is filler.'
    )

    quotes = extract_quotes(section)

    assert quotes == [
        "passionate about building scalable systems",
        "the cloud is being reshaped in real time",
    ]


def test_extract_quotes_empty_when_no_citation():
    # A "Missing" gap is often stated without quoting a line.
    assert extract_quotes("Nothing shows Kubernetes depth despite the JD.") == []
