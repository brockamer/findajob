"""Interview-prep orchestrator.

Extracted from `scripts/interview_prep.py` in M3 (#537). Module-load
`load_env()` deferred into `main()`. `notify()` lightweight ntfy
wrapper preserved verbatim (also lives in `findajob.triage.orchestrator`
and `findajob.prep.orchestrator`); cleanup PR consolidates.
"""

import os
import re
import sqlite3
import subprocess
import sys
from datetime import datetime

from findajob.interview.role_runner import run_role
from findajob.interview.sentinel import SENTINEL_NAME, _sentinel_blocks_run
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
    # Module-load side effect deferred to here so import is safe.
    load_env()

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
