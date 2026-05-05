"""Tests for findajob.cost_tracking."""

from __future__ import annotations

from pathlib import Path

import pytest

from findajob.cost_tracking import _rates, role_model


def test_role_model_resolves_known_role(tmp_path: Path) -> None:
    """A role file with model: in frontmatter returns that model string."""
    roles_dir = tmp_path / "roles"
    roles_dir.mkdir()
    (roles_dir / "scorer.md").write_text(
        "---\nmodel: openrouter:deepseek/deepseek-v3.2\nmax_tokens: 1024\n---\n\nbody\n"
    )
    assert role_model("scorer", roles_dir=roles_dir) == "openrouter:deepseek/deepseek-v3.2"


def test_role_model_missing_file_returns_unknown(tmp_path: Path) -> None:
    """Missing role file returns 'unknown' rather than raising."""
    roles_dir = tmp_path / "roles"
    roles_dir.mkdir()
    assert role_model("nonexistent", roles_dir=roles_dir) == "unknown"


def test_role_model_no_frontmatter_returns_unknown(tmp_path: Path) -> None:
    """Role file without model: field falls back to 'unknown'."""
    roles_dir = tmp_path / "roles"
    roles_dir.mkdir()
    (roles_dir / "broken.md").write_text("# A role with no frontmatter at all.\n")
    assert role_model("broken", roles_dir=roles_dir) == "unknown"


def test_role_model_frontmatter_without_model_key_returns_unknown(tmp_path: Path) -> None:
    """Frontmatter present but no model: line falls back to 'unknown'."""
    roles_dir = tmp_path / "roles"
    roles_dir.mkdir()
    (roles_dir / "partial.md").write_text("---\nmax_tokens: 1024\n---\n\nbody\n")
    assert role_model("partial", roles_dir=roles_dir) == "unknown"


# ── Pricing-table coverage for OpenRouter-prefixed model strings (#458) ─────
#
# The post-#48 verification run revealed every production opus / sonnet /
# perplexity / gemini call was falling through to the default rate ($1/$5)
# because the pricing table only had legacy `claude:` / `gemini:` /
# `perplexity:` entries — production routes through `openrouter:*`.
# These assertions hard-pin the per-model rate so a future config tweak
# that breaks the longest-prefix match doesn't silently regress to the
# default again.


@pytest.mark.parametrize(
    "model,expected_in,expected_out",
    [
        # Rates empirically derived from 2026-05-05 CoreWeave prep run on
        # the operator's stack (#460). Opus 4.7 + Gemini 3 Flash were corrected
        # downward (Opus 4.7 ≠ Opus 4 pricing) and upward (Gemini 3 Flash ≠
        # Gemini 2 Flash pricing) respectively from #459's initial values.
        ("openrouter:anthropic/claude-opus-4.7", 5.0, 25.0),
        ("openrouter:anthropic/claude-sonnet-4.6", 3.0, 15.0),
        ("openrouter:google/gemini-3-flash-preview", 0.55, 2.20),
        ("openrouter:perplexity/sonar-reasoning-pro", 2.0, 8.0),
        ("openrouter:perplexity/sonar-deep-research", 5.0, 20.0),
        ("openrouter:deepseek/deepseek-v3.2", 0.27, 1.10),
    ],
)
def test_pricing_table_has_entry_for_production_models(model: str, expected_in: float, expected_out: float) -> None:
    rates = _rates(model)
    assert rates["input_per_mtok"] == expected_in, f"{model} input rate"
    assert rates["output_per_mtok"] == expected_out, f"{model} output rate"
