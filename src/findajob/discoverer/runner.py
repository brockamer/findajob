"""Orchestration for the company_discoverer pipeline (#284).

Reads the candidate profile, builds the prompt, calls the OpenRouter wrapper,
strips think-block residue, parses, validates, and atomically writes
the output pair. On any failure: logs to pipeline.jsonl, optionally
ntfys, and returns a failure RunResult without raising. No
``cached_prefix`` / ``pin_provider`` are passed because Perplexity does
not honor ``cache_control``.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import NamedTuple

from findajob.audit import log_event
from findajob.cost_tracking import log_call, role_model
from findajob.db import connect
from findajob.discoverer.parser import DiscoveryParseError, parse_markdown
from findajob.discoverer.prompt import build_prompt
from findajob.discoverer.writer import commit_atomically
from findajob.llm.openrouter import OpenRouterError, complete
from findajob.paths import BASE

_DEFAULT_TIMEOUT_S = 540  # under cron's 600s timeout, room for IO
_DEFAULT_COST_THRESHOLD_USD = 1.00
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


class RunResult(NamedTuple):
    success: bool
    count: int  # type: ignore[assignment]  # NamedTuple field shadows tuple.count method
    error: str | None
    cost_usd: float | None


def _send_ntfy(title: str, body: str, kind: str = "discovery_run") -> None:
    """Best-effort ntfy via scripts/notify.py send-raw.

    Uses subprocess to call the existing notify.py CLI; suppresses any
    error so a notification failure cannot mask a successful run. `kind`
    flows through to the persisted notifications row (#440).
    """
    try:
        subprocess.run(
            [
                sys.executable,
                str(Path(BASE) / "scripts" / "notify.py"),
                "send-raw",
                title,
                body,
                "--kind",
                kind,
            ],
            check=False,
            capture_output=True,
            timeout=15,
        )
    except Exception:
        pass


def _send_success_ntfy(companies: list) -> None:
    """Success-path ntfy after a discovery run commits.

    Body lists the top-5 names; an empty run still fires so a silent cron
    is observable instead of indistinguishable from a stuck schedule.
    """
    count = len(companies)
    title = f"findajob: discovered {count} companies"
    if count == 0:
        body = "(no novel companies surfaced this run)"
    else:
        body = ", ".join(c.name for c in companies[:5])
    _send_ntfy(title, body)


def _cost_threshold() -> float:
    raw = os.environ.get("DISCOVERY_COST_THRESHOLD_USD", "")
    try:
        return float(raw) if raw else _DEFAULT_COST_THRESHOLD_USD
    except ValueError:
        return _DEFAULT_COST_THRESHOLD_USD


def _log_cost_safely(
    db_path: Path,
    *,
    operation: str,
    model: str,
    input_text: str,
    output_text: str,
    latency_ms: int,
    success: bool,
    cost_usd_override: float | None = None,
    input_tokens_override: int | None = None,
    output_tokens_override: int | None = None,
) -> None:
    """Best-effort cost_log write. Swallows all errors so cost tracking
    can never break the discovery run's never-raise contract.
    """
    try:
        conn = connect(db_path, timeout=5.0)
        try:
            log_call(
                conn,
                job_id=None,
                operation=operation,
                model=model,
                input_text=input_text,
                output_text=output_text,
                latency_ms=latency_ms,
                success=success,
                cost_usd_override=cost_usd_override,
                input_tokens_override=input_tokens_override,
                output_tokens_override=output_tokens_override,
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001 — cost tracking is best-effort
        log_event("cost_log_failed", operation=operation, error=f"{type(e).__name__}: {e}")


def run(
    base_root: Path,
    profile_path: Path | None = None,
    ntfy_enabled: bool = True,
    db_path: Path | None = None,
) -> RunResult:
    """Run the full discovery pipeline. Never raises.

    Returns a :class:`RunResult` describing success/failure and metadata.
    """
    profile = profile_path or (base_root / "candidate_context" / "profile.md")
    resolved_db = db_path or (base_root / "data" / "pipeline.db")
    if not profile.is_file():
        msg = f"profile not found at {profile}"
        log_event("discovery_failed", reason="profile_missing", path=str(profile))
        if ntfy_enabled:
            _send_ntfy("discovery: profile missing", msg)
        return RunResult(success=False, count=0, error=msg, cost_usd=None)

    try:
        profile_text = profile.read_text(encoding="utf-8")
        prompt = build_prompt(profile_text)
        start = time.time()
        result = complete(
            role="company_discoverer",
            prompt=prompt,
            # Perplexity (sonar-reasoning-pro) does not honor cache_control —
            # do NOT pass cached_prefix or pin_provider here.
            timeout_s=_DEFAULT_TIMEOUT_S,
        )
        latency_ms = int((time.time() - start) * 1000)

        raw_md = _THINK_RE.sub("", result.text).strip()
        if raw_md == "INSUFFICIENT_PROFILE":
            log_event("discovery_failed", reason="insufficient_profile")
            if ntfy_enabled:
                _send_ntfy("discovery: insufficient profile", "LLM returned INSUFFICIENT_PROFILE")
            return RunResult(success=False, count=0, error="LLM returned INSUFFICIENT_PROFILE", cost_usd=None)

        parsed = parse_markdown(raw_md)
        json_payload: dict = {
            "generated_at": datetime.now(UTC).strftime("%Y-%m-%d"),
            "model": "openrouter:perplexity/sonar-reasoning-pro",
            "companies": [
                {
                    "name": c.name,
                    "cluster": c.cluster,
                    "channel": c.channel,
                    "reasoning": c.reasoning,
                    "citations": list(c.citations),
                }
                for c in parsed.companies
            ],
        }
        commit_atomically(base_root, parsed.markdown_clean + "\n", json_payload)

        _log_cost_safely(
            resolved_db,
            operation="company_discoverer",
            model=role_model("company_discoverer"),
            input_text=prompt,
            output_text=result.text,
            latency_ms=latency_ms,
            success=True,
            cost_usd_override=result.cost_usd,
            input_tokens_override=result.prompt_tokens,
            output_tokens_override=result.completion_tokens,
        )

        cost = result.cost_usd
        log_event(
            "discovery_complete",
            count=len(parsed.companies),
            cost_usd=cost,
        )
        if ntfy_enabled:
            _send_success_ntfy(parsed.companies)
        threshold = _cost_threshold()
        if cost is not None and cost > threshold and ntfy_enabled:
            _send_ntfy(
                "discovery: cost exceeded threshold",
                f"run cost ${cost:.2f} > threshold ${threshold:.2f} (still wrote {len(parsed.companies)} companies)",
            )
        return RunResult(success=True, count=len(parsed.companies), error=None, cost_usd=cost)

    except OpenRouterError as e:
        msg = f"OpenRouter error ({e.kind}): {str(e)[:300]}"
        log_event(
            "discovery_failed",
            reason="openrouter_error",
            kind=e.kind,
            status_code=e.status_code,
            message=str(e)[:300],
        )
        if ntfy_enabled:
            _send_ntfy("discovery: openrouter error", msg[:200])
        return RunResult(success=False, count=0, error=msg, cost_usd=None)
    except DiscoveryParseError as e:
        msg = str(e)
        log_event("discovery_failed", reason="parse_error", message=msg)
        if ntfy_enabled:
            _send_ntfy("discovery: parse error", msg[:200])
        return RunResult(success=False, count=0, error=msg, cost_usd=None)
    except Exception as e:  # noqa: BLE001 — guarantee never-raise contract
        msg = f"{type(e).__name__}: {e}"
        log_event("discovery_failed", reason="unhandled", message=msg)
        if ntfy_enabled:
            _send_ntfy("discovery: unhandled error", msg[:200])
        return RunResult(success=False, count=0, error=msg, cost_usd=None)
