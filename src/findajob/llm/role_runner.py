"""LLM role runner — single OpenRouter call wrapped with cost-log persistence.

Consolidates the byte-equivalent copies that lived in
`findajob.prep.role_runner` and `findajob.interview.role_runner` after
M3's import-only extractions (#537). The two duplicates are removed in
this PR; their callsites now import from here.

This is the canonical surface for any future caller that needs:
- a single OpenRouter call (with optional prompt-cache prefix and
  provider pinning)
- API-authoritative cost logging via `cost_log` (when `conn` is provided)
- silent recovery from `OpenRouterError` (returns "" rather than raising)
- best-effort cost-log writes (failures don't break the caller)
"""

import re
import sqlite3
import time

from findajob.audit import log_event
from findajob.cost_tracking import log_call, role_model
from findajob.llm.openrouter import LLMSpendCeilingExceeded, OpenRouterError, complete


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
    response. Cost-log failures are swallowed — they cannot break the
    caller. Each call writes at most one row; caller-side retries
    (e.g. prep's briefing_writer retry) intentionally produce multiple
    rows by invoking run_role() multiple times.
    """
    start = time.time()
    try:
        result = complete(
            role=role,
            prompt=prompt,
            cached_prefix=cached_prefix,
            pin_provider=pin_provider,
            timeout_s=timeout,
            job_id=job_id,
        )
    except LLMSpendCeilingExceeded:
        raise
    except OpenRouterError as e:
        log_event("openrouter_failure", role=role, kind=e.kind, status_code=e.status_code, message=str(e)[:300])
        # #955: OpenRouter bills for consumed tokens even when the response is
        # unusable (null content on finish_reason=length, etc). Record the
        # billed cost when usage was returned (e.cost_usd is not None) so
        # spend_this_month() doesn't under-count during failure storms. A
        # pre-response failure (network/auth — e.cost_usd is None) never billed,
        # so no row is written and free failures don't inflate the spend total.
        if conn is not None and e.cost_usd is not None:
            try:
                log_call(
                    conn,
                    job_id=job_id,
                    operation=role,
                    model=role_model(role),
                    input_text=prompt,
                    output_text="",
                    latency_ms=int((time.time() - start) * 1000),
                    success=False,
                    error_message=f"{e.kind}: {str(e)[:200]}",
                    cost_usd_override=e.cost_usd,
                    input_tokens_override=e.prompt_tokens,
                    output_tokens_override=e.completion_tokens,
                )
                conn.commit()
            except Exception as log_err:  # noqa: BLE001 — cost tracking is best-effort
                log_event("cost_log_failed", operation=role, error=f"{type(log_err).__name__}: {log_err}")
        return ""
    latency_ms = int((time.time() - start) * 1000)

    # #737: openrouter_truncated emission moved into the wrapper so every
    # direct-complete() caller (run_role, discoverer, future probes) gets the
    # diagnostic uniformly. The wrapper fires the event for us when
    # result.finish_reason == "length"; the job_id we passed above is on the
    # event payload.

    text = re.sub(r"<think>.*?</think>", "", result.text, flags=re.DOTALL).strip()

    # #955: log on every terminating branch. A billed-but-empty response
    # (``text`` strips to "") still incurred cost; only ``conn`` presence
    # gates the write. The old ``and text`` guard silently dropped these
    # rows, under-counting spend_this_month() during empty-output storms.
    if conn is not None:
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
