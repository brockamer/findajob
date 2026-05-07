"""cost_log row writer for `findajob.llm.openrouter` callers.

Wrap-pattern for new call sites: after `complete()` returns successfully,
call `log_call(conn, job_id=..., operation=role, model=role_model(role),
input_text=prompt, output_text=result.text, latency_ms=..., success=True,
cost_usd_override=result.cost_usd, input_tokens_override=result.prompt_tokens,
output_tokens_override=result.completion_tokens)`.

The override-trio carries API-authoritative cost; without overrides, the
heuristic at `cost_usd()` is used (legacy paths only — every production
site as of Phase 2 (#471) uses overrides). Calibration multiplier (#467)
still governs the heuristic path until Phase 3 (#472) retires it.

Usage:
    from findajob.cost_tracking import log_call, role_model
    log_call(conn, job_id="...", operation="score",
             model=role_model("job_scorer"),
             input_text=prompt, output_text=response,
             latency_ms=2300, success=True,
             cost_usd_override=result.cost_usd,
             input_tokens_override=result.prompt_tokens,
             output_tokens_override=result.completion_tokens)

The cost_usd is computed at insert time from prompt/response character
length and the model's rate in config/model_pricing.yaml.

Empirical precision floor (verified 2026-05-05, 3-run series on the
operator's stack against OpenRouter dashboard):

- Heuristic systematically underestimates by ~25–30% on real prep runs.
- Source: ``estimate_tokens`` uses ``chars / 4`` but Anthropic's
  tokenizer is ~chars/3.3 for English. The ~20% input-token undercount
  compounds slightly through the pricing math.
- Per-stage attribution IS correct: every Opus call lands at the same
  ~30-35% under ratio, every Sonar call within ±15%, Gemini within ±20%.
- Consumers should treat absolute amounts as biased ~25% low; relative
  comparisons (which prep stage cost most, week-over-week trend, per-job
  total comparisons) are reliable.

A future tokenizer refinement (chars/3.5 instead of chars/4) would close
the gap to ~10% but was deferred — the bias is documented, predictable,
and consistently in one direction. Migrate to direct-HTTP
``usage:{include:true}`` (#32 Option B) only if absolute precision
becomes load-bearing for tuning decisions.

Cost-write override (#470 forward):

Callers using the native ``findajob.llm.openrouter`` wrapper pass
``cost_usd_override=completion_result.cost_usd`` to skip the heuristic
entirely — the override is the API-authoritative billed amount from
``response.usage.cost``. As of Phase 2 (#471), all production call sites
use the override trio (wrapper-driven calls). The heuristic remains
active for the legacy path but no production site uses it. The
calibration multiplier in ``cost_rollups`` governs heuristic-path rows
until Phase 3 (#472) retires that path.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import yaml

from findajob.paths import BASE

_PRICING_PATH = Path(BASE) / "config" / "model_pricing.yaml"
_ROLES_DIR = Path(BASE) / "config" / "roles"


def role_model(role_name: str, roles_dir: Path | None = None) -> str:
    """Read the ``model:`` field from a role's YAML frontmatter.

    Returns ``"unknown"`` if the role file is missing or has no ``model:``
    line; the heuristic in ``estimate_cost_usd`` then falls back to the
    conservative default rate from ``config/model_pricing.yaml``.

    ``roles_dir`` is for tests; production callers omit it and read from
    ``$BASE/config/roles/``.
    """
    base = roles_dir if roles_dir is not None else _ROLES_DIR
    role_path = base / f"{role_name}.md"
    try:
        with open(role_path) as f:
            in_front = False
            for line in f:
                if line.strip() == "---":
                    in_front = not in_front
                    continue
                if in_front and line.startswith("model:"):
                    return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return "unknown"


def _load_pricing() -> tuple[dict, dict]:
    """Return (models_dict, default_dict) from model_pricing.yaml.

    Falls back to a conservative default if the file is missing so the
    pipeline doesn't break on a fresh install.
    """
    try:
        with open(_PRICING_PATH) as f:
            data = yaml.safe_load(f) or {}
    except FileNotFoundError:
        return {}, {"input_per_mtok": 1.0, "output_per_mtok": 5.0}
    models = data.get("models", {}) or {}
    default = data.get("default", {}) or {"input_per_mtok": 1.0, "output_per_mtok": 5.0}
    return models, default


_MODELS, _DEFAULT = _load_pricing()


def estimate_tokens(text: str | None) -> int:
    """Estimate token count from character length (chars/4 heuristic)."""
    if not text:
        return 0
    return max(1, len(text) // 4)


def _rates(model: str) -> dict:
    """Look up pricing for a model. Prefers longest prefix match."""
    if model in _MODELS:
        return _MODELS[model]
    # Longest prefix match — e.g. "claude:claude-opus-4-6:thinking" without
    # an explicit entry falls back to "claude:claude-opus-4-6".
    best_key = None
    for key in _MODELS:
        if model.startswith(key) and (best_key is None or len(key) > len(best_key)):
            best_key = key
    if best_key is not None:
        return _MODELS[best_key]
    return _DEFAULT


def estimate_cost_usd(model: str, input_text: str | None, output_text: str | None) -> tuple[int, int, float]:
    """Return (input_tokens, output_tokens, cost_usd) for a call."""
    rates = _rates(model)
    in_tok = estimate_tokens(input_text)
    out_tok = estimate_tokens(output_text)
    cost = in_tok * rates.get("input_per_mtok", 0) / 1_000_000 + out_tok * rates.get("output_per_mtok", 0) / 1_000_000
    return in_tok, out_tok, round(cost, 6)


def log_call(
    conn: sqlite3.Connection,
    *,
    job_id: str | None,
    operation: str,
    model: str,
    input_text: str | None = None,
    output_text: str | None = None,
    latency_ms: int | None = None,
    success: bool = True,
    error_message: str | None = None,
    cost_usd_override: float | None = None,
    input_tokens_override: int | None = None,
    output_tokens_override: int | None = None,
) -> None:
    """Insert a cost_log row.

    By default, every column comes from the chars/4 heuristic + pricing
    table. Wrapper-driven callers (``findajob.llm.openrouter``) pass the
    three ``*_override`` kwargs together so the row is fully
    API-authoritative — billed dollars and token counts both from
    ``response.usage``. Mixing overrides (e.g. cost from API but tokens
    from heuristic) creates rows where ``cost / token`` ratios are
    inconsistent, so use the trio together when on the wrapper path.
    """
    heuristic_in, heuristic_out, heuristic_cost = estimate_cost_usd(model, input_text, output_text)
    in_tok = input_tokens_override if input_tokens_override is not None else heuristic_in
    out_tok = output_tokens_override if output_tokens_override is not None else heuristic_out
    cost = cost_usd_override if cost_usd_override is not None else heuristic_cost
    conn.execute(
        """
        INSERT INTO cost_log
            (job_id, operation, model, latency_ms, success, error_message,
             input_tokens, output_tokens, cost_usd)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            job_id,
            operation,
            model,
            latency_ms,
            1 if success else 0,
            error_message,
            in_tok,
            out_tok,
            cost,
        ),
    )
