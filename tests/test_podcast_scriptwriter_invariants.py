"""Static invariants for podcast scriptwriter prompt and format instructions (#872).

The podcast scriptwriter's role prompt is instructions to an LLM — no Python
parser to test against. The failure mode #872 documents (LLM fabricates "no EU
experience" despite EU DC experience appearing in the master resume) is
behavioral and only verifiable empirically via a full podcast run.

These tests guard against the **structural cause**: the prompt encouraged
gap-surfacing but lacked an explicit anti-fabrication guard. Once the guard is
present, fabricated gaps are the LLM's mistake, not the prompt's omission.

Tests are static-only (regex/string grep over files). Cheap, no network.
"""

from __future__ import annotations

import re
from pathlib import Path

_ROLE_PATH = Path(__file__).parent.parent / "config" / "roles" / "podcast_scriptwriter.md"


def _read_role() -> str:
    return _ROLE_PATH.read_text(encoding="utf-8")


# ── #872: anti-fabrication guard in quality standards ──────────────────────


def test_grounding_standard_prohibits_fabricated_gaps() -> None:
    """Quality standard #2 must explicitly prohibit claiming the candidate
    lacks experience without verifying across the provided artifacts.
    This is the root cause of #872: the scriptwriter fabricated 'no EU
    experience' despite the master resume containing extensive EU DC work."""
    text = _read_role()
    assert re.search(
        r"[Nn]ever claim the candidate lacks",
        text,
    ), "quality standard must prohibit fabricated gap claims; see #872"


def test_grounding_standard_requires_multi_artifact_verification() -> None:
    """The anti-fabrication guard must instruct the model to verify absence
    across multiple artifacts (MASTER RESUME, CANDIDATE PROFILE, etc.),
    not just the tailored resume."""
    text = _read_role()
    assert "MASTER RESUME" in text, "anti-fabrication guard must reference MASTER RESUME for verification; see #872"
    assert "CANDIDATE PROFILE" in text, (
        "anti-fabrication guard must reference CANDIDATE PROFILE for verification; see #872"
    )


def test_grounding_standard_warns_about_hallucination_risk() -> None:
    """The guard should call out fabricated gaps as the highest-risk
    hallucination vector — this metacognitive framing helps the LLM
    self-check."""
    text = _read_role()
    assert re.search(
        r"hallucination",
        text,
        re.IGNORECASE,
    ), "quality standard must warn about hallucination risk for gap claims; see #872"


# ── #872: format-level guards in gap-surfacing formats ────────────────────


def test_critical_analysis_format_has_verification_guard() -> None:
    """The Critical Analysis format explicitly asks the model to identify
    gaps and objections. It must also require that those gaps be verifiable
    from the materials."""
    from findajob.interview.orchestrator import FORMAT_INSTRUCTIONS

    ca = FORMAT_INSTRUCTIONS["critical_analysis"]
    assert re.search(
        r"verifiable.*materials|verified.*materials|confirm.*materials",
        ca,
        re.IGNORECASE,
    ), "critical_analysis format must require gap claims to be verifiable from materials; see #872"


def test_deep_dive_format_has_verification_guard() -> None:
    """Deep Dive format surfaces gaps; must guard against fabrication."""
    from findajob.interview.orchestrator import FORMAT_INSTRUCTIONS

    dd = FORMAT_INSTRUCTIONS["deep_dive"]
    assert re.search(
        r"never claim.*lacks|confirmed by the materials|never claim.*candidate",
        dd,
        re.IGNORECASE,
    ), "deep_dive format must guard against fabricated gap claims; see #872"


def test_deep_dive_long_format_has_verification_guard() -> None:
    """Deep Dive Extended says 'be honest about gaps'; must also require
    those gaps be confirmed by materials."""
    from findajob.interview.orchestrator import FORMAT_INSTRUCTIONS

    ddl = FORMAT_INSTRUCTIONS["deep_dive_long"]
    assert re.search(
        r"confirmed by.*materials|verifiable.*materials|confirmed.*absence",
        ddl,
        re.IGNORECASE,
    ), "deep_dive_long format must guard against fabricated gap claims; see #872"


def test_qa_drill_format_has_verification_guard() -> None:
    """Q&A Drill probes 'genuine gap or weakness'; must require verification."""
    from findajob.interview.orchestrator import FORMAT_INSTRUCTIONS

    qa = FORMAT_INSTRUCTIONS["qa_drill"]
    assert re.search(
        r"verifiable.*materials|confirmed.*materials",
        qa,
        re.IGNORECASE,
    ), "qa_drill format must guard against fabricated gap claims; see #872"


# ── #872: prompt assembly includes master resume ──────────────────────────


def test_cached_prefix_includes_master_resume_label() -> None:
    """The scriptwriter receives the master resume via cached_prefix.
    If the label changes or is removed, the model may not recognize the
    full work history as authoritative input."""
    import inspect

    from findajob.interview.orchestrator import _generate

    source = inspect.getsource(_generate)
    assert "MASTER RESUME" in source, "cached_prefix must include MASTER RESUME label; see #872"
    assert "CANDIDATE PROFILE" in source, "cached_prefix must include CANDIDATE PROFILE label; see #872"
