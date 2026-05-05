"""Speculative research runner.

Invoked as a detached subprocess via scripts/run_speculative_research.py
(itself spawned from POST /ingest/speculative). Single entry point:
``run_research(conn, request_id, profile_path, master_resume_path, companies_dir)``.

Lifecycle:
    status='researching'  ->  call briefing role  ->  call synth role  ->
    write briefing folder + briefing.md  ->  status='ready_for_review'

On any failure: status='failed' + error_message, partial state
(briefing_md if briefing call succeeded) preserved for retry.
"""

from __future__ import annotations

import re
import sqlite3
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

from findajob.cost_tracking import log_call, role_model
from findajob.paths import AICHAT
from findajob.speculative.parser import parse_role_cards
from findajob.speculative.storage import write_briefing
from findajob.utils import log_event

_BRIEFING_ROLE = "candidate_led_briefing"
_SYNTH_ROLE = "speculative_roles_synth"
_BRIEFING_PROMPT_VERSION = f"{_BRIEFING_ROLE}@v1"
_SYNTH_PROMPT_VERSION = f"{_SYNTH_ROLE}@v1"


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
        try:
            briefing_md = _call_aichat(
                _BRIEFING_ROLE,
                vars_={
                    "company": company,
                    "hint": hint,
                    "personal_notes": personal_notes,
                    "candidate_profile": profile,
                    "master_resume": master_resume,
                },
                conn=conn,
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
    try:
        synth_raw = _call_aichat(
            _SYNTH_ROLE,
            vars_={
                "candidate_profile": profile,
                "master_resume": master_resume,
                "briefing": briefing_md,
            },
            conn=conn,
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


def _call_aichat(role: str, *, vars_: dict[str, str], conn: sqlite3.Connection | None = None) -> str:
    """Invoke aichat-ng with the named role, passing template vars as a single prompt string.

    Convention matches prep_application.py and interview_prep.py:
    ``aichat-ng --role <role> -S <prompt_body>``

    The prompt body is a concatenation of all template variables as labeled
    sections, matching the pattern used elsewhere in the pipeline for direct
    context injection.

    When ``conn`` is provided, a cost_log row is written after a successful
    subprocess return (operation = role name). Cost-log failures are
    swallowed so they cannot break the speculative-research pipeline.
    """
    body = "\n\n".join(f"# {k}\n{v}" for k, v in vars_.items())
    start = time.time()
    proc = subprocess.run(
        [AICHAT, "--role", role, "-S", body],
        capture_output=True,
        text=True,
        timeout=600,  # 10 min: deep-research can take 1-5 min, plus margin
    )
    latency_ms = int((time.time() - start) * 1000)
    if proc.returncode != 0:
        raise RuntimeError(f"aichat-ng exit {proc.returncode}: {proc.stderr.strip()[-500:]}")
    output = proc.stdout.strip()
    if conn is not None:
        try:
            log_call(
                conn,
                job_id=None,
                operation=role,
                model=role_model(role),
                input_text=body,
                output_text=output,
                latency_ms=latency_ms,
                success=True,
            )
            conn.commit()
        except Exception as e:  # noqa: BLE001 — cost tracking is best-effort
            log_event("cost_log_failed", operation=role, error=f"{type(e).__name__}: {e}")
    # Strip <think>...</think> blocks that leak from reasoning models
    output = re.sub(r"<think>.*?</think>", "", output, flags=re.DOTALL).strip()
    return output
