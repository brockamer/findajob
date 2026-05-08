"""LLM role runner — single OpenRouter call wrapped with cost-log persistence.

Byte-equivalent copy of `findajob.prep.role_runner.run_role`. Both copies
exist transiently per the M3 import-only discipline; the cleanup PR (M3+
or M3's 6th child) consolidates them into `findajob.llm.role_runner`.
"""

import re
import sqlite3
import time

from findajob.cost_tracking import log_call, role_model
from findajob.llm.openrouter import OpenRouterError, complete
from findajob.utils import log_event


def run_role(
    role: str,
    prompt: str,
    *,
    cached_prefix: str | None = None,
    pin_provider: str | None = None,
    conn: sqlite3.Connection | None = None,
    job_id: str | None = None,
    timeout: int = 300,
) -> str:
    """Call openrouter.complete() and return assistant text.

    When ``conn`` is provided, a cost_log row is written after a successful
    response. Cost-log failures are swallowed so they cannot break
    interview-prep generation.
    """
    start = time.time()
    try:
        result = complete(
            role=role,
            prompt=prompt,
            cached_prefix=cached_prefix,
            pin_provider=pin_provider,
            timeout_s=timeout,
        )
    except OpenRouterError as e:
        log_event("openrouter_failure", role=role, kind=e.kind, status_code=e.status_code, message=str(e)[:300])
        return ""
    latency_ms = int((time.time() - start) * 1000)

    text = re.sub(r"<think>.*?</think>", "", result.text, flags=re.DOTALL).strip()

    if conn is not None and text:
        try:
            log_call(
                conn,
                job_id=job_id,
                operation=role,
                model=role_model(role),
                input_text=prompt,
                output_text=result.text,
                latency_ms=latency_ms,
                success=True,
                cost_usd_override=result.cost_usd,
                input_tokens_override=result.prompt_tokens,
                output_tokens_override=result.completion_tokens,
            )
            conn.commit()
        except Exception as e:  # noqa: BLE001 — cost tracking is best-effort
            log_event("cost_log_failed", operation=role, error=f"{type(e).__name__}: {e}")
    return text
