"""Per-LLM-call cost tracking.

Scoped to #32 "Option D": char-based token estimation + pricing lookup.
Not precise — does not read actual API token counts — but gives a
first-order estimate good enough for weekly trend visibility.

Usage:
    from findajob.cost_tracking import log_call
    log_call(conn, job_id="...", operation="score",
             model="openrouter:deepseek/deepseek-v3.2",
             input_text=prompt, output_text=response,
             latency_ms=2300, success=True)

The cost_usd is computed at insert time from prompt/response character
length and the model's rate in config/model_pricing.yaml.
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
) -> None:
    """Insert a cost_log row with estimated token counts and cost."""
    in_tok, out_tok, cost = estimate_cost_usd(model, input_text, output_text)
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
