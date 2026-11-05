"""Tests for findajob.cost_tracking."""

from __future__ import annotations

import ast
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
        ("openrouter:anthropic/claude-opus-4.8", 5.0, 25.0),
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


# ── cost_usd_override path (#470) ────────────────────────────────────────


def test_log_call_with_cost_usd_override_bypasses_heuristic(tmp_path: Path) -> None:
    """cost_usd_override -> row's cost_usd is the override, not the heuristic estimate."""
    import sqlite3

    from findajob.cost_tracking import log_call

    db = tmp_path / "test.db"
    conn = sqlite3.connect(db)
    conn.execute(
        """
        CREATE TABLE cost_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT,
            operation TEXT,
            model TEXT,
            latency_ms INTEGER,
            success INTEGER,
            error_message TEXT,
            input_tokens INTEGER,
            output_tokens INTEGER,
            cost_usd REAL,
            created_at TEXT DEFAULT (datetime('now'))
        )
        """
    )

    log_call(
        conn,
        job_id="j-1",
        operation="score",
        model="openrouter:deepseek/deepseek-v3.2",
        input_text="x" * 4000,
        output_text="y" * 200,
        cost_usd_override=0.001234,
        input_tokens_override=1000,
        output_tokens_override=50,
    )
    row = conn.execute("SELECT cost_usd, input_tokens, output_tokens FROM cost_log").fetchone()
    assert row[0] == pytest.approx(0.001234)
    assert row[1] == 1000
    assert row[2] == 50


def test_log_call_with_token_overrides_writes_authoritative_counts(tmp_path: Path) -> None:
    """input_tokens_override + output_tokens_override bypass the heuristic.

    When all three overrides travel together (cost + input + output), the row
    is fully API-authoritative — cost / token ratios stay internally consistent.
    """
    import sqlite3

    from findajob.cost_tracking import log_call

    db = tmp_path / "test.db"
    conn = sqlite3.connect(db)
    conn.execute(
        """
        CREATE TABLE cost_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT, operation TEXT, model TEXT, latency_ms INTEGER,
            success INTEGER, error_message TEXT, input_tokens INTEGER,
            output_tokens INTEGER, cost_usd REAL,
            created_at TEXT DEFAULT (datetime('now'))
        )
        """
    )

    log_call(
        conn,
        job_id="j-3",
        operation="score",
        model="openrouter:anthropic/claude-opus-4-7",
        input_text="x" * 4000,
        output_text="y" * 200,
        cost_usd_override=0.005,
        input_tokens_override=2500,
        output_tokens_override=42,
    )
    row = conn.execute("SELECT cost_usd, input_tokens, output_tokens FROM cost_log WHERE job_id = 'j-3'").fetchone()
    assert row[0] == pytest.approx(0.005)
    assert row[1] == 2500
    assert row[2] == 42


def test_log_call_without_override_raises(tmp_path: Path) -> None:
    """Tightened contract (#1145): the override trio is required, not optional.

    log_call no longer falls back to the chars/4 heuristic when an override
    is omitted — a caller that cannot supply a real cost/token value is a
    bug, so Python's own missing-required-keyword-argument error surfaces
    immediately instead of silently writing an estimate.
    """
    import sqlite3

    from findajob.cost_tracking import log_call

    db = tmp_path / "test.db"
    conn = sqlite3.connect(db)
    conn.execute(
        """
        CREATE TABLE cost_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT, operation TEXT, model TEXT, latency_ms INTEGER,
            success INTEGER, error_message TEXT, input_tokens INTEGER,
            output_tokens INTEGER, cost_usd REAL,
            created_at TEXT DEFAULT (datetime('now'))
        )
        """
    )
    with pytest.raises(TypeError):
        log_call(
            conn,
            job_id="j-2",
            operation="score",
            model="openrouter:deepseek/deepseek-v3.2",
            input_text="x" * 4000,
            output_text="y" * 200,
        )


# ── Every non-test log_call call site passes its overrides explicitly (#1145) ──
#
# log_call's override trio has no default (see test_log_call_without_override_raises
# above) so an omitted override is already a hard TypeError at call time. This
# walks the source tree as a second line of defense against a caller that
# supplies the keyword but with a literal ``None`` value, which the required-
# keyword-argument change above would not catch.

_REPO_ROOT = Path(__file__).resolve().parents[1]
_OVERRIDE_KWARGS = ("cost_usd_override", "input_tokens_override", "output_tokens_override")


def _log_call_sites(py_file: Path) -> list[ast.Call]:
    tree = ast.parse(py_file.read_text(), filename=str(py_file))
    sites = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            if name == "log_call":
                sites.append(node)
    return sites


def test_log_call_explicit_cost_sites() -> None:
    """Every non-test call to log_call() passes all three overrides, each a
    non-None expression — none can fall through to a heuristic estimate."""
    search_roots = [_REPO_ROOT / "src" / "findajob", _REPO_ROOT / "scripts"]
    py_files = [f for root in search_roots for f in root.rglob("*.py")]
    assert py_files, "expected to find source files to scan"

    checked_sites = 0
    for py_file in py_files:
        for call in _log_call_sites(py_file):
            checked_sites += 1
            passed_kwargs = {kw.arg: kw.value for kw in call.keywords if kw.arg is not None}
            for required in _OVERRIDE_KWARGS:
                assert required in passed_kwargs, f"{py_file}:{call.lineno} log_call() missing {required}"
                value = passed_kwargs[required]
                is_literal_none = isinstance(value, ast.Constant) and value.value is None
                assert not is_literal_none, f"{py_file}:{call.lineno} log_call() passes {required}=None"

    assert checked_sites > 0, "expected at least one log_call() call site in src/ or scripts/"
