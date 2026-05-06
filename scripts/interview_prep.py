#!/usr/bin/env python3
# scripts/interview_prep.py
# Args: company, title, job_id
"""Generate the interview-prep artifact for a job that just transitioned to interview.

Launched as a detached subprocess from POST /board/jobs/{fp}/interview (see
findajob.web.routes.board_actions). Re-clicking "Interviewing" on the board
regenerates a fresh artifact with a new timestamp.

Reads the existing prep folder (briefing, tailored resume, cover letter, optional
recruiter critique) and EXPANDS the briefing's interview-questions and stories
sections into a structured prep document. Does NOT re-derive STAR or questions
from scratch; the briefing is canonical.
"""

import os
import re
import sqlite3
import subprocess
import sys
import time
from datetime import datetime

from findajob.cost_tracking import log_call, role_model
from findajob.llm.openrouter import OpenRouterError, complete
from findajob.paths import BASE, PANDOC
from findajob.utils import (
    load_env,
    log_event,
    read_file_prefix,
    safe_filename_part,
)

DB_PATH = f"{BASE}/data/pipeline.db"
PROFILE_PATH = f"{BASE}/candidate_context/profile.md"
MASTER_RESUME_PATH = f"{BASE}/candidate_context/master_resume.md"

SENTINEL_NAME = ".interview_prep_in_progress"
# Treat any sentinel older than this as orphaned from a killed run.
# Well above typical Opus 4.7 generation time (~2 min observed) but well below
# the operator-noticing threshold for a stuck Interviewing button.
SENTINEL_STALE_AFTER_SECONDS = 600


def _sentinel_blocks_run(sentinel_path: str, *, log_kwargs: dict[str, object]) -> bool:
    """Return True iff a fresh in-flight sentinel exists at ``sentinel_path``.

    A sentinel older than ``SENTINEL_STALE_AFTER_SECONDS`` is treated as
    orphaned from a killed run: removed in place and a
    ``interview_prep_sentinel_stale_removed`` event logged so the recovery
    is auditable in pipeline.jsonl. Returns False after removal so the
    caller proceeds.
    """
    if not os.path.exists(sentinel_path):
        return False
    try:
        age = time.time() - os.path.getmtime(sentinel_path)
    except OSError:
        return False
    if age < SENTINEL_STALE_AFTER_SECONDS:
        return True
    log_event(
        "interview_prep_sentinel_stale_removed",
        age_seconds=int(age),
        **log_kwargs,
    )
    try:
        os.remove(sentinel_path)
    except OSError:
        pass
    return False


load_env()


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


def _latest(folder: str, pattern: re.Pattern[str]) -> str | None:
    """Return the absolute path of the most recently modified file in `folder`
    whose basename matches `pattern`, or None if no match."""
    if not folder or not os.path.isdir(folder):
        return None
    matches = [
        os.path.join(folder, name)
        for name in os.listdir(folder)
        if pattern.search(name) and os.path.isfile(os.path.join(folder, name))
    ]
    if not matches:
        return None
    matches.sort(key=os.path.getmtime, reverse=True)
    return matches[0]


def _read_or_empty(path: str | None) -> str:
    if not path:
        return ""
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return ""


def notify(message: str) -> None:
    topic = None
    try:
        with open(f"{BASE}/config/ntfy_topic.txt") as f:
            topic = f.read().strip()
    except FileNotFoundError:
        pass
    if not topic:
        try:
            with open(f"{BASE}/data/.env") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("NTFY_TOPIC") and "=" in line:
                        topic = line.split("=", 1)[1].strip().strip("'\"")
                        break
        except Exception:
            pass
    if not topic:
        return
    try:
        subprocess.run(
            ["curl", "-s", "-d", message, f"https://ntfy.sh/{topic}"],
            capture_output=True,
            timeout=10,
        )
    except Exception:
        pass


def main() -> None:
    if len(sys.argv) < 4:
        print("Usage: interview_prep.py <company> <title> <job_id>", file=sys.stderr)
        sys.exit(2)

    company, title, job_id = sys.argv[1], sys.argv[2], sys.argv[3]

    # ── Look up job + prep folder ──
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT prep_folder_path, raw_jd_text, stage FROM jobs WHERE id=?",
        (job_id,),
    ).fetchone()

    if not row:
        conn.close()
        log_event("interview_prep_error", job_id=job_id, reason="job_not_found")
        return

    try:
        prep_folder = row["prep_folder_path"]
        if not prep_folder or not os.path.isdir(prep_folder):
            log_event(
                "interview_prep_error",
                job_id=job_id,
                company=company,
                title=title,
                reason="no_prep_folder",
                folder=prep_folder,
            )
            notify(f"INTERVIEW PREP SKIPPED: {company} — {title}\nNo prep folder; apply was likely manual.")
            return

        # ── Concurrency guard: refuse if a fresh run is already in flight for this folder ──
        sentinel = os.path.join(prep_folder, SENTINEL_NAME)
        log_kwargs: dict[str, object] = {
            "job_id": job_id,
            "company": company,
            "title": title,
            "folder": prep_folder,
        }
        if _sentinel_blocks_run(sentinel, log_kwargs=log_kwargs):
            log_event("interview_prep_skipped_in_flight", **log_kwargs)
            return

        # Touch sentinel; remove on exit.
        try:
            with open(sentinel, "w") as f:
                f.write(datetime.now().isoformat())
        except OSError:
            pass

        try:
            _generate(prep_folder, company, title, job_id, row["raw_jd_text"] or "", conn=conn)
        finally:
            try:
                os.remove(sentinel)
            except OSError:
                pass
    finally:
        conn.close()


def _generate(
    prep_folder: str,
    company: str,
    title: str,
    job_id: str,
    jd_text: str,
    *,
    conn: sqlite3.Connection | None = None,
) -> None:
    log_event(
        "interview_prep_started",
        job_id=job_id,
        company=company,
        title=title,
        folder=prep_folder,
    )

    # ── Discover existing artifacts in the prep folder ──
    # `Resume Changes` must NOT match the resume regex — it's a different doc.
    briefing_path = _latest(prep_folder, re.compile(r"Briefing.*\.md$"))
    resume_path = _latest(prep_folder, re.compile(r"(?<!Changes )Resume(?! Changes).*\.md$"))
    cover_path = _latest(prep_folder, re.compile(r"Cover.*\.md$"))
    critique_path = _latest(prep_folder, re.compile(r"Critique.*\.md$"))

    briefing = _read_or_empty(briefing_path)
    resume = _read_or_empty(resume_path)
    cover = _read_or_empty(cover_path)
    critique = _read_or_empty(critique_path)

    if not briefing:
        log_event(
            "interview_prep_error",
            job_id=job_id,
            company=company,
            title=title,
            reason="no_briefing_in_prep_folder",
            folder=prep_folder,
        )
        notify(f"INTERVIEW PREP FAILED: {company} — {title}\nNo briefing found in prep folder; cannot expand.")
        return

    # ── Load profile + master resume — injected directly, never via RAG ──
    profile = _read_or_empty(PROFILE_PATH)
    master = _read_or_empty(MASTER_RESUME_PATH)

    if not profile or not master:
        log_event(
            "interview_prep_error",
            job_id=job_id,
            company=company,
            title=title,
            reason="missing_candidate_files",
            profile=bool(profile),
            master=bool(master),
        )
        notify(f"INTERVIEW PREP FAILED: {company} — {title}\nMissing profile.md or master_resume.md.")
        return

    # ── Build prompt ──
    # cached_prefix: profile + master_resume — stable across all jobs in a day;
    # enables same-role cache hits. Per-job content (JD, briefing, company) goes in prompt.
    cached_prefix = f"CANDIDATE PROFILE:\n{profile}\n\nMASTER RESUME:\n{master}"

    cover_section = f"\nCOVER LETTER (the version submitted):\n{cover}\n" if cover else ""
    critique_section = f"\nRECRUITER CRITIQUE:\n{critique}\n" if critique else ""
    briefing_header = (
        "COMPANY BRIEFING (canonical — your STAR section MUST expand its questions+stories, not re-derive):"
    )
    prompt = (
        f"Company: {company}\nTitle: {title}\n\n"
        f"JOB DESCRIPTION:\n{jd_text}\n\n"
        f"{briefing_header}\n{briefing}\n\n"
        f"TAILORED RESUME (the version actually submitted):\n{resume}\n"
        f"{cover_section}"
        f"{critique_section}"
    )

    # ── Generate ──
    output_md = run_role(
        "interview_prep",
        prompt,
        cached_prefix=cached_prefix,
        pin_provider="anthropic",
        conn=conn,
        job_id=job_id,
    )

    if not output_md or len(output_md) < 500:
        log_event(
            "interview_prep_error",
            job_id=job_id,
            company=company,
            title=title,
            reason="empty_or_short_output",
            chars=len(output_md) if output_md else 0,
        )
        notify(f"INTERVIEW PREP FAILED: {company} — {title}\nLLM returned empty/short output.")
        return

    # ── Write artifact ──
    file_prefix = read_file_prefix()
    co = safe_filename_part(company, 40)
    t = safe_filename_part(title, 60)
    timestamp_fn = datetime.now().strftime("%Y%m%d-%H%M%S")
    base = f"{file_prefix} Interview Prep - {co} - {t} - {timestamp_fn}"
    md_path = os.path.join(prep_folder, f"{base}.md")
    docx_path = os.path.join(prep_folder, f"{base}.docx")

    with open(md_path, "w") as f:
        f.write(output_md)

    subprocess.run(
        [
            PANDOC,
            md_path,
            "--lua-filter",
            f"{BASE}/config/strip-bookmarks.lua",
            "--reference-doc",
            f"{BASE}/config/reference.docx",
            "-o",
            docx_path,
        ],
        check=False,
    )

    log_event(
        "interview_prep_complete",
        job_id=job_id,
        company=company,
        title=title,
        folder=prep_folder,
        md=os.path.basename(md_path),
        chars=len(output_md),
    )
    notify(f"Interview prep ready: {company} — {title}\n{md_path}")
    print(f"INTERVIEW_PREP_COMPLETE:{md_path}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        job_id = sys.argv[3] if len(sys.argv) > 3 else "unknown"
        company = sys.argv[1] if len(sys.argv) > 1 else "unknown"
        title = sys.argv[2] if len(sys.argv) > 2 else "unknown"
        log_event(
            "interview_prep_failed",
            job_id=job_id,
            company=company,
            title=title,
            error=f"{type(exc).__name__}: {exc}",
        )
        raise
