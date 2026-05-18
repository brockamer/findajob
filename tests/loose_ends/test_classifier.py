"""Tests for findajob.loose_ends.classifier (#572).

The classifier's most load-bearing invariant: exclusions are a pre-filter,
not LLM guidance. An excluded path must NEVER reach the LLM.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

from findajob.loose_ends.classifier import Finding, classify_gaps
from findajob.loose_ends.coverage_map import SurfaceRef
from findajob.loose_ends.surface_map import CallSite


def test_exclusions_pre_filter_path_never_reaches_llm() -> None:
    """A path listed in exclusions must not appear in any LLM call argument."""
    excluded_site = CallSite(file="src/findajob/foo.py", line=5, snippet="x = 'config/excluded_thing.yaml'")
    real_site = CallSite(file="src/findajob/bar.py", line=10, snippet="y = 'config/real_gap.yaml'")
    surface = {
        "config/excluded_thing.yaml": [excluded_site],
        "config/real_gap.yaml": [real_site],
    }
    coverage: dict[str, list[SurfaceRef]] = {}  # nothing covered
    exclusions = {"config/excluded_thing.yaml": "rationale"}

    fake_llm = MagicMock()
    fake_llm.return_value.text = (
        '{"confidence": "high", "rationale": "no UI", "suggested_surface": "/settings/real-gap/"}'
    )
    fake_llm.return_value.cost_usd = 0.0  # explicit, or float() raises TypeError on the Mock

    with patch("findajob.loose_ends.classifier.openrouter.complete", fake_llm):
        findings, total_cost = classify_gaps(
            surface_map=surface,
            coverage_map=coverage,
            exclusions=exclusions,
        )

    # The excluded path must NEVER appear in any LLM call's prompt arg.
    for call in fake_llm.call_args_list:
        prompt = call.kwargs.get("prompt", "") + str(call.args)
        assert "config/excluded_thing.yaml" not in prompt, "Excluded path leaked to LLM"
    # The real gap should produce a Finding.
    assert any(f.path == "config/real_gap.yaml" for f in findings)
    # Cost is 0.0 with mocked LLM (MagicMock returns Mock for cost_usd; float coerces to 0.0).
    assert isinstance(total_cost, float)


def test_report_has_both_required_sections(tmp_path: Path) -> None:
    """The report must contain ## Findings and ## Exclusions fired this run."""
    from datetime import date as _date

    from findajob.loose_ends.classifier import write_report

    findings = [
        Finding(
            path="config/x.yaml",
            confidence="high",
            rationale="no UI",
            suggested_surface="/settings/x/",
            call_sites=[CallSite(file="src/findajob/foo.py", line=1, snippet="'config/x.yaml'")],
        )
    ]
    surface = {"config/excluded.yaml": [CallSite(file="src/findajob/bar.py", line=1, snippet="'config/excluded.yaml'")]}
    exclusions = {"config/excluded.yaml": "operator-asserted CLI-only"}

    fake_llm = MagicMock()
    fake_llm.return_value.text = "- `config/x.yaml` (high): no UI"
    fake_llm.return_value.cost_usd = 0.05
    with patch("findajob.loose_ends.classifier.openrouter.complete", fake_llm):
        report, prose_cost = write_report(
            findings=findings,
            surface_map=surface,
            exclusions=exclusions,
            output_dir=tmp_path,
            today=_date(2026, 5, 18),
        )

    assert report.exists()
    assert report.name == "2026-05-18-static-audit.md"
    body = report.read_text()
    assert "## Findings" in body
    assert "## Exclusions fired this run" in body
    # Excluded path that has a consumer should be listed.
    assert "config/excluded.yaml" in body
    assert "operator-asserted CLI-only" in body
    # Cost was tracked from the mock.
    assert prose_cost == 0.05


def test_classifier_strips_markdown_fences_around_json() -> None:
    """LLMs (notably Haiku) wrap JSON in ```json ... ``` fences despite role-prompt
    instructions to the contrary. The parser must tolerate this — strip fences
    before json.loads(). Without this, every Finding gets demoted to low-confidence
    via the JSONDecodeError fallback path (observed in #572 Task 5's first run)."""
    surface = {
        "config/real_gap.yaml": [CallSite(file="src/findajob/bar.py", line=10, snippet="y = 'config/real_gap.yaml'")],
    }
    coverage: dict[str, list[SurfaceRef]] = {}
    exclusions: dict[str, str] = {}

    fake_llm = MagicMock()
    # Fenced JSON — the production failure mode.
    fenced_json = (
        '```json\n{"confidence": "high", "rationale": "no UI", "suggested_surface": "/settings/real-gap/"}\n```'
    )
    fake_llm.return_value.text = fenced_json
    fake_llm.return_value.cost_usd = 0.0

    with patch("findajob.loose_ends.classifier.openrouter.complete", fake_llm):
        findings, _ = classify_gaps(
            surface_map=surface,
            coverage_map=coverage,
            exclusions=exclusions,
        )

    assert len(findings) == 1
    # MUST parse correctly through the fences, NOT fall through to "low".
    assert findings[0].confidence == "high"
    assert findings[0].rationale == "no UI"
    assert findings[0].suggested_surface == "/settings/real-gap/"
