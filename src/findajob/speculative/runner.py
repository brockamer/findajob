"""Speculative research runner.

Invoked as a detached subprocess via scripts/run_speculative_research.py
(itself spawned from POST /ingest/speculative). Single entry point:
``run_research(conn, request_id, profile_path, master_resume_path, companies_dir)``.

Lifecycle:
    status='researching'  ->  call briefing role  ->  call synth role  ->
    write briefing folder + briefing.md  ->  status='ready_for_review'

On any failure: status='failed' + error_message, partial state
(briefing_md if briefing call succeeded) preserved for retry.

Port note (#471 Phase 2): previously spawned aichat-ng as a subprocess
via _call_aichat(); now calls findajob.llm.openrouter.complete() directly
via _invoke_role(). Cost comes from result.cost_usd (API-authoritative).
"""

from __future__ import annotations

import re
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path

from findajob.cost_tracking import log_call, role_model
from findajob.llm.openrouter import OpenRouterError, complete
from findajob.speculative.parser import parse_role_cards
from findajob.speculative.storage import write_briefing
from findajob.utils import log_event

_BRIEFING_ROLE = "candidate_led_briefing"
_SYNTH_ROLE = "speculative_roles_synth"
_BRIEFING_PROMPT_VERSION = f"{_BRIEFING_ROLE}@v1"
_SYNTH_PROMPT_VERSION = f"{_SYNTH_ROLE}@v1"

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)

# Timeout for deep-research calls (Perplexity sonar-deep-research can take 1-5 min).
_BRIEFING_TIMEOUT_S = 600
# Timeout for synthesis calls (Anthropic Sonnet — faster).
_SYNTH_TIMEOUT_S = 300


def run_research(
    *,
    conn: sqlite3.Connection,
    request_id: int,
    profile_path: Path,
    master_resume_path: Path,
    companies_dir: Path,
) -> None:
    """Run briefing + role-synth for the given speculative_requests row.

    Idempotency: caller is responsible for ensuring the row exists with
    status='researching'. This function updates it to 'ready_for_review'
    or 'failed'.
    """
    row = conn.execute(
        "SELECT id, company, hint, personal_notes, briefing_md FROM speculative_requests WHERE id=?",
        (request_id,),
    ).fetchone()
    if row is None:
        raise RuntimeError(f"speculative_requests id={request_id} not found")

    company = row["company"]
    hint = row["hint"] or ""
    personal_notes = row["personal_notes"] or ""

    profile = profile_path.read_text() if profile_path.exists() else ""
    master_resume = master_resume_path.read_text() if master_resume_path.exists() else ""

    log_event("speculative_research_started", request_id=request_id, company=company)

    # Step 1: briefing (skip if already cached on retry)
    briefing_md = row["briefing_md"]
    if not briefing_md:
        briefing_prompt = "\n\n".join(
            f"# {k}\n{v}"
            for k, v in {
                "company": company,
                "hint": hint,
                "personal_notes": personal_notes,
                "candidate_profile": profile,
                "master_resume": master_resume,
            }.items()
        )
        try:
            briefing_md = _invoke_role(
                _BRIEFING_ROLE,
                briefing_prompt,
                conn=conn,
                # Perplexity (sonar-deep-research) does not honor cache_control —
                # do NOT pass cached_prefix or pin_provider here.
                timeout=_BRIEFING_TIMEOUT_S,
            )
        except Exception as e:
            _mark_failed(conn, request_id, f"briefing failed: {e}")
            log_event("speculative_research_failed", request_id=request_id, stage="briefing", error=str(e))
            return
        conn.execute(
            "UPDATE speculative_requests SET briefing_md=?, briefing_prompt_version=? WHERE id=?",
            (briefing_md, _BRIEFING_PROMPT_VERSION, request_id),
        )
        conn.commit()

    # Step 2: role synth
    # cached_prefix = profile + master_resume (byte-stable across requests).
    # The briefing is per-request and varies — it goes in the prompt body.
    # Plan said profile+briefing; actual call shape confirms briefing varies
    # per request (different company each time), so only profile+master_resume
    # qualify for cross-request cache hits on Anthropic Sonnet.
    synth_cached_prefix = "\n\n".join(
        [
            f"# candidate_profile\n{profile}",
            f"# master_resume\n{master_resume}",
        ]
    )
    synth_prompt = f"# briefing\n{briefing_md}"
    try:
        synth_raw = _invoke_role(
            _SYNTH_ROLE,
            synth_prompt,
            conn=conn,
            # Anthropic Sonnet honors cache_control; pin to anthropic provider.
            # cached_prefix is profile+master_resume (stable across requests).
            cached_prefix=synth_cached_prefix,
            pin_provider="anthropic",
            timeout=_SYNTH_TIMEOUT_S,
        )
        # Validate parses cleanly so the review page never sees garbage.
        _ = parse_role_cards(synth_raw)
    except Exception as e:
        _mark_failed(conn, request_id, f"synth failed: {e}")
        log_event("speculative_research_failed", request_id=request_id, stage="synth", error=str(e))
        return

    # Step 3: write briefing folder
    folder = write_briefing(base_dir=companies_dir, company=company, briefing_md=briefing_md)
    folder_name = folder.name

    # Step 4: finalize
    now = datetime.now(UTC).isoformat()
    conn.execute(
        """UPDATE speculative_requests
           SET role_cards_json=?, synth_prompt_version=?, briefing_folder=?,
               status='ready_for_review', research_completed_at=?
           WHERE id=?""",
        (synth_raw, _SYNTH_PROMPT_VERSION, folder_name, now, request_id),
    )
    conn.commit()
    log_event("speculative_research_complete", request_id=request_id, company=company, folder=folder_name)


def _mark_failed(conn: sqlite3.Connection, request_id: int, msg: str) -> None:
    conn.execute(
        "UPDATE speculative_requests SET status='failed', error_message=? WHERE id=?",
        (msg, request_id),
    )
    conn.commit()


def _invoke_role(
    role: str,
    prompt: str,
    *,
    cached_prefix: str | None = None,
    pin_provider: str | None = None,
    conn: sqlite3.Connection | None = None,
    timeout: int = 300,
) -> str:
    """Call openrouter.complete() with the named role and return assistant text.

    Matches the canonical run_role() shape from scripts/prep_application.py.

    When ``conn`` is provided, a cost_log row is written after a successful
    response (operation = role name). Cost-log failures are swallowed so they
    cannot break the speculative-research pipeline.
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
        raise RuntimeError(str(e)) from e
    latency_ms = int((time.time() - start) * 1000)

    # Strip <think>...</think> blocks that leak from reasoning models
    text = _THINK_RE.sub("", result.text).strip()

    if conn is not None and text:
        try:
            log_call(
                conn,
                job_id=None,
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
