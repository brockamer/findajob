"""cost_log row writer for `findajob.llm.openrouter` callers.

Wrap-pattern for every call site: after `complete()` returns successfully,
call `log_call(conn, job_id=..., operation=role, model=role_model(role),
input_text=prompt, output_text=result.text, latency_ms=..., success=True,
cost_usd_override=result.cost_usd, input_tokens_override=result.prompt_tokens,
output_tokens_override=result.completion_tokens)`.

The override trio carries API-authoritative cost from ``response.usage.cost``
(or 0.0 / 0 tokens for a deterministic prefilter hit that never called the
LLM). `log_call` no longer estimates missing values via a chars/4 heuristic
(#1145) — every caller must supply the trio explicitly. `estimate_cost_usd`
and `estimate_tokens` remain for test use only.

Usage:
    from findajob.cost_tracking import log_call, role_model
    log_call(conn, job_id="...", operation="score",
             model=role_model("job_scorer"),
             input_text=prompt, output_text=response,
             latency_ms=2300, success=True,
             cost_usd_override=result.cost_usd,
             input_tokens_override=result.prompt_tokens,
             output_tokens_override=result.completion_tokens)
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
    line.

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
    """Estimate token count from character length (chars/4 heuristic).

    Test-only (#1145) — ``log_call`` no longer calls this in production.
    """
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
    """Return (input_tokens, output_tokens, cost_usd) for a call.

    Test-only (#1145) — ``log_call`` no longer calls this in production.
    """
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
    cost_usd_override: float | None,
    input_tokens_override: int | None,
    output_tokens_override: int | None,
    input_text: str | None = None,
    output_text: str | None = None,
    latency_ms: int | None = None,
    success: bool = True,
    error_message: str | None = None,
) -> None:
    """Insert a cost_log row.

    ``cost_usd_override``, ``input_tokens_override`` and
    ``output_tokens_override`` are required (#1145) — every caller must
    supply the real cost/token counts explicitly. Wrapper-driven callers
    (``findajob.llm.openrouter``) pass ``response.usage``-derived values;
    a deterministic prefilter hit that never called the LLM passes
    ``0.0`` / ``0`` / ``0``. There is no chars/4 heuristic fallback —
    ``input_text``/``output_text`` are no longer used to compute cost and
    exist only so existing call sites keep working unchanged.
    """
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
            input_tokens_override,
            output_tokens_override,
            cost_usd_override,
        ),
    )
