#!/usr/bin/env python3
# ~/JobSearchPipeline/scripts/triage.py
"""
Daily triage pipeline. Fetches jobs, deduplicates, enriches, scores,
and writes results to SQLite. Sheet sync is a separate script called at the end.
"""

import csv
import os
import shutil
import signal
import sqlite3
import subprocess
import sys
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime, timedelta
from pathlib import Path

from findajob.cleaning import fingerprint, is_coarse_location, loose_fingerprint, normalize
from findajob.cost_tracking import log_call
from findajob.fetchers import (
    fetch_ashby_jobs,
    fetch_gmail_jobs,
    fetch_greenhouse_jobs,
    fetch_jd,
    fetch_lever_jobs,
    get_linkedin_rate_limit_stats,
    reset_linkedin_rate_limit_stats,
)
from findajob.fetchers.adapters import iter_configured_adapters
from findajob.onboarding import is_complete as _onboarding_is_complete
from findajob.paths import BASE
from findajob.scoring import _build_feedback_block, score_job
from findajob.utils import (
    is_aggregator_company,
    is_ingest_noise_title,
    load_env,
    log_event,
    write_audit,
)


# ── Signal handler: log a termination event before exiting ───────────────────
# systemd sends SIGTERM when the service hits TimeoutStartSec (default: 30min).
# Without this handler the process dies silently and pipeline_complete never
# fires, causing notify.py health-check to miss a real failure.
def _on_sigterm(signum, frame):
    log_event("pipeline_terminated", signal="SIGTERM", note="Received SIGTERM — likely systemd timeout or manual stop.")
    sys.exit(143)  # 128 + SIGTERM(15)


signal.signal(signal.SIGTERM, _on_sigterm)

DB_PATH = f"{BASE}/data/pipeline.db"
CONNECTIONS = f"{BASE}/data/connections.csv"
PROFILE_PATH = f"{BASE}/candidate_context/profile.md"


def _role_model(role_name):
    """Read the model: field from a role's YAML frontmatter."""
    role_path = f"{BASE}/config/roles/{role_name}.md"
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


SCORER_MODEL = _role_model("job_scorer")
SCORE_WORKERS = 6  # concurrent LLM scoring threads (each spawns aichat subprocess)

load_env()

# Cache feedback block at module load — rebuilt each triage run
_FEEDBACK_BLOCK = _build_feedback_block()


NULL_SCORE_RETRY_LIMIT = 50  # max null-score rows retried per triage run
NULL_SCORE_RETRY_DAYS = 7  # rows older than this are skipped (genuinely broken JD)


def score_null_manual_review_rows(
    conn: sqlite3.Connection,
    candidate_profile: str,
    feedback_block: str,
    limit: int = NULL_SCORE_RETRY_LIMIT,
) -> int:
    """Re-score manual_review rows that have relevance_score=NULL (prior scorer failure).

    Only considers rows updated within NULL_SCORE_RETRY_DAYS to avoid retrying
    genuinely unparseable JDs forever. Returns the count of successfully rescored rows.

    stage_updated is stored as Python ISO format ("2026-04-23T17:19:57+00:00");
    compute the cutoff in Python so both sides of the comparison use the same format.
    SQLite's datetime('now', ...) returns space-separated form which misorders against
    ISO-T on same-day rows (T > space at pos 10).
    """
    cutoff = (datetime.now(UTC) - timedelta(days=NULL_SCORE_RETRY_DAYS)).isoformat()
    rows = conn.execute(
        """
        SELECT id, title, company, location, raw_jd_text FROM jobs
        WHERE stage = 'manual_review'
          AND relevance_score IS NULL
          AND stage_updated > ?
          AND (dupe_of = '' OR dupe_of IS NULL)
        LIMIT ?
        """,
        (cutoff, limit),
    ).fetchall()

    rescored = 0
    for row in rows:
        job_id = row["id"]
        try:
            scored, _ = score_job(
                row["title"],
                row["company"] or "",
                row["location"] or "",
                row["raw_jd_text"] or "",
                candidate_profile,
                feedback_block=feedback_block,
            )
        except Exception as e:
            log_event("null_score_retry_error", job_id=job_id, error=str(e))
            continue

        now = datetime.now(UTC).isoformat()
        stage = "manual_review" if scored.get("score_status") == "manual_review" else "scored"
        conn.execute(
            """
            UPDATE jobs SET
                relevance_score=?, interview_likelihood=?, strengths_alignment=?,
                industry_sector=?, comp_estimate=?, ai_notes=?,
                score_status=?, score_flag_reason=?, remote_status=?,
                stage=?, stage_updated=?, updated_at=?
            WHERE id=?
            """,
            (
                scored.get("relevance_score"),
                scored.get("interview_likelihood"),
                scored.get("strengths_alignment"),
                scored.get("industry_sector", ""),
                scored.get("comp_estimate", ""),
                scored.get("ai_notes", ""),
                scored.get("score_status", "manual_review"),
                scored.get("score_flag_reason", ""),
                scored.get("remote_status", "Unknown"),
                stage,
                now,
                now,
                job_id,
            ),
        )
        conn.commit()
        write_audit(conn, job_id, "stage", "manual_review", stage)
        rescored += 1

    return rescored


# ── Contact Lookup ──
def find_contacts(company):
    contacts = []
    if not company or not company.strip():
        return contacts
    try:
        with open(CONNECTIONS) as f:
            for row in csv.DictReader(f):
                contact_co = row.get("Company", "").strip()
                if not contact_co:
                    continue  # guard: '' in 'anything' is True in Python
                if company.lower() in contact_co.lower():
                    contacts.append(f"{row['First Name']} {row['Last Name']} ({row['Position']})")
    except Exception:
        pass
    return contacts


# ── Main Pipeline ──
def main():
    # Don't crash on stacks where the operator hasn't completed onboarding —
    # cron fires every day regardless. Sentinel-driven no-op keeps
    # pipeline.jsonl clean (was: pipeline_crash on missing profile.md every
    # tick, see #371).
    if not _onboarding_is_complete(Path(BASE)):
        log_event("triage_skipped", reason="not_onboarded")
        return

    log_event("pipeline_started")
    reset_linkedin_rate_limit_stats()

    if os.path.exists(DB_PATH):
        shutil.copy2(DB_PATH, f"{DB_PATH}.bak")

    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")

    with open(PROFILE_PATH) as f:
        candidate_profile = f.read()

    # ── Fetch with retry ─────────────��────────────────────────────────────
    # The triage runs once daily. If DNS or network is down at run time,
    # all sources return 0 jobs and the day is lost. Retry up to 3 times
    # with 2-minute gaps (well within the 3600s systemd timeout).
    MAX_FETCH_ATTEMPTS = 3
    FETCH_RETRY_DELAY = 120  # seconds

    for attempt in range(1, MAX_FETCH_ATTEMPTS + 1):
        feed_urls = f"{BASE}/config/feed_urls.txt"
        greenhouse_jobs = fetch_greenhouse_jobs(feed_urls)
        ashby_jobs = fetch_ashby_jobs(feed_urls)
        lever_jobs = fetch_lever_jobs(feed_urls)
        gmail_jobs = fetch_gmail_jobs()

        # Adapter-driven RapidAPI ingestion (#408)
        queries_path = Path(f"{BASE}/config/jsearch_queries.txt")
        queries = (
            [
                line.strip()
                for line in queries_path.read_text().splitlines()
                if line.strip() and not line.startswith("#")
            ]
            if queries_path.exists()
            else []
        )
        adapter_jobs: list[dict] = []
        adapter_counts: dict[str, int] = {}
        for adapter in iter_configured_adapters():
            rows = adapter.fetch(queries)
            adapter_jobs.extend(rows)
            adapter_counts[adapter.name] = len(rows)

        raw_jobs = greenhouse_jobs + ashby_jobs + lever_jobs + adapter_jobs + gmail_jobs
        log_event(
            "jobs_fetched",
            count=len(raw_jobs),
            greenhouse=len(greenhouse_jobs),
            ashby=len(ashby_jobs),
            lever=len(lever_jobs),
            adapters=adapter_counts,
            gmail=len(gmail_jobs),
            attempt=attempt,
        )

        if raw_jobs or attempt == MAX_FETCH_ATTEMPTS:
            break

        # Zero jobs — probe connectivity before retrying
        try:
            probe = subprocess.run(
                ["curl", "-s", "--max-time", "5", "-o", "/dev/null", "-w", "%{http_code}", "https://google.com"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            online = probe.stdout.strip().startswith(("2", "3"))
        except Exception:
            online = False

        if online:
            # Network is up but still 0 jobs — genuine empty day, stop retrying
            log_event("fetch_retry_skipped", reason="network_up_but_zero_jobs", attempt=attempt)
            break

        log_event(
            "fetch_retry", attempt=attempt, next_attempt_in=FETCH_RETRY_DELAY, reason="zero_jobs_and_network_down"
        )
        time.sleep(FETCH_RETRY_DELAY)

    if not raw_jobs:
        log_event("pipeline_complete", new=0, dupes=0, scored=0)
        conn.close()
        return

    new_count = 0
    dupe_count = 0
    scored_count = 0
    noise_count = 0

    for job in raw_jobs:
        if not job.get("title") or not job.get("url"):
            continue

        # ── Ingest noise filters ──
        # 1. LinkedIn "Jobs similar to" recommendations-carousel items.
        #    These aren't real jobs — the API returned a UI element.
        if is_ingest_noise_title(job.get("title", "")):
            log_event(
                "ingest_skipped",
                reason="jobs_similar_to",
                title=job.get("title", "")[:80],
                company=job.get("company", "")[:80],
            )
            noise_count += 1
            continue
        # 2. Aggregator / recruiter wrappers (Jobs via Dice, Robert Half, etc.).
        #    The "company" is the board, not the actual employer — unactionable.
        if is_aggregator_company(job.get("company", "")):
            log_event(
                "ingest_skipped",
                reason="aggregator_company",
                title=job.get("title", "")[:80],
                company=job.get("company", "")[:80],
            )
            noise_count += 1
            continue

        fp = fingerprint(job["title"], job.get("company", ""), job.get("location", ""))
        lfp = loose_fingerprint(job["title"], job.get("company", ""))

        existing = conn.execute("SELECT id FROM jobs WHERE fingerprint = ?", (fp,)).fetchone()

        # Fallback: URL-based dedup catches jobs whose fingerprint changed
        # due to cleaning rule updates (same URL, different fingerprint).
        if not existing and job.get("url"):
            existing = conn.execute("SELECT id FROM jobs WHERE url = ?", (job["url"],)).fetchone()

        # Tier 2 (#182 Bug C): cross-source syndication. When incoming OR any
        # existing same-(company,title) row has a coarse location, treat as
        # duplicate so the same req posted to Greenhouse ("US") and LinkedIn
        # ("Barstow, TX") dedupes. Distinct-city reqs (site managers in
        # different cities) still produce distinct strict fingerprints and
        # never reach this branch.
        if not existing:
            incoming_coarse = is_coarse_location(job.get("location", ""))
            loose_matches = conn.execute("SELECT id, location FROM jobs WHERE loose_fingerprint = ?", (lfp,)).fetchall()
            for row in loose_matches:
                if incoming_coarse or is_coarse_location(row["location"] or ""):
                    existing = row
                    log_event(
                        "dedupe_loose_match",
                        title=job["title"][:80],
                        company=job.get("company", "")[:80],
                        incoming_location=job.get("location", "")[:80],
                        existing_location=(row["location"] or "")[:80],
                    )
                    break

        if existing:
            conn.execute(
                "INSERT OR IGNORE INTO duplicate_groups (canonical_fingerprint, duplicate_job_id) VALUES (?, ?)",
                (fp, job.get("url", "")),
            )
            dupe_count += 1
            continue

        job_id = str(uuid.uuid4())
        now = datetime.now(UTC).isoformat()

        conn.execute(
            """
            INSERT INTO jobs (
                id, fingerprint, loose_fingerprint, url, title, company,
                location, source, stage, stage_updated, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'discovered', ?, ?)
        """,
            (
                job_id,
                fp,
                lfp,
                job["url"],
                job["title"],
                job.get("company", ""),
                job.get("location", ""),
                job.get("source", "rss"),
                now,
                now,
            ),
        )
        conn.commit()
        write_audit(conn, job_id, "stage", None, "discovered")
        new_count += 1

        jd_text = fetch_jd(job)

        # For gmail_linkedin jobs: if company was blank after HTML heuristics,
        # fetch_jd already called the LinkedIn API and cached the company — reuse it.
        if job.get("source") == "gmail_linkedin" and not job.get("company"):
            resolved = job.get("_linkedin_company")
            if resolved:
                job["company"] = resolved
                # Recompute fingerprint with resolved company and check for dupes
                new_fp = fingerprint(job["title"], job["company"], job.get("location", ""))
                new_lfp = loose_fingerprint(job["title"], job["company"])
                existing_resolved = conn.execute(
                    "SELECT id FROM jobs WHERE fingerprint = ? AND id != ?", (new_fp, job_id)
                ).fetchone()
                if not existing_resolved:
                    incoming_coarse = is_coarse_location(job.get("location", ""))
                    loose_matches = conn.execute(
                        "SELECT id, location FROM jobs WHERE loose_fingerprint = ? AND id != ?", (new_lfp, job_id)
                    ).fetchall()
                    for row in loose_matches:
                        if incoming_coarse or is_coarse_location(row["location"] or ""):
                            existing_resolved = row
                            break
                if existing_resolved:
                    # A copy with the resolved company already exists — mark this one as dupe
                    conn.execute(
                        "UPDATE jobs SET dupe_of=?, stage=?, stage_updated=?, updated_at=? WHERE id=?",
                        (existing_resolved["id"], "rejected", now, now, job_id),
                    )
                    conn.commit()
                    write_audit(conn, job_id, "stage", "discovered", "rejected")
                    log_event(
                        "dupe_after_enrichment",
                        job_id=job_id,
                        title=job["title"],
                        company=job["company"],
                        dupe_of=existing_resolved["id"],
                    )
                    dupe_count += 1
                    continue
                # No dupe — update company and fingerprint on the inserted row
                conn.execute(
                    "UPDATE jobs SET company=?, fingerprint=?, loose_fingerprint=? WHERE id=?",
                    (job["company"], new_fp, new_lfp, job_id),
                )
                conn.commit()
            else:
                # Company unresolvable — reject immediately, don't waste a scorer call
                conn.execute(
                    """
                    UPDATE jobs SET stage='rejected', stage_updated=?, status='rejected',
                           reject_reason='Blank Company', updated_at=?
                    WHERE id=?
                """,
                    (now, now, job_id),
                )
                conn.commit()
                write_audit(conn, job_id, "stage", "discovered", "rejected")
                log_event("blank_company_rejected", job_id=job_id, title=job["title"], source="gmail_linkedin")
                continue

        contacts = find_contacts(job.get("company", ""))
        network_depth = min(len(contacts), 2)
        known_contacts = ", ".join(contacts[:3])

        conn.execute(
            """
            UPDATE jobs SET raw_jd_text=?, network_depth=?, known_contacts=?,
                   stage='enriched', stage_updated=?, updated_at=?
            WHERE id=?
        """,
            (jd_text, network_depth, known_contacts, now, now, job_id),
        )
        conn.commit()
        write_audit(conn, job_id, "stage", "discovered", "enriched")

        # Fuzzy dedup: if an existing job with the same normalized title+company
        # is already at an advanced stage, mark this one as a duplicate.  Catches
        # reposts from different sources whose location text differs (different
        # fingerprints but same role).
        norm_title = normalize(job.get("title", ""))
        norm_company = normalize(job.get("company", ""))
        if norm_title and norm_company:
            advanced = conn.execute(
                """
                SELECT id FROM jobs
                WHERE id != ? AND (dupe_of = '' OR dupe_of IS NULL)
                  AND stage IN ('materials_drafted', 'applied', 'interview', 'offer')
            """,
                (job_id,),
            ).fetchall()
            for adv in advanced:
                adv_row = conn.execute("SELECT title, company FROM jobs WHERE id=?", (adv["id"],)).fetchone()
                if normalize(adv_row["title"]) == norm_title and normalize(adv_row["company"]) == norm_company:
                    conn.execute(
                        "UPDATE jobs SET dupe_of=?, stage='rejected', stage_updated=?, updated_at=? WHERE id=?",
                        (adv["id"], now, now, job_id),
                    )
                    conn.commit()
                    write_audit(conn, job_id, "stage", "enriched", "rejected")
                    log_event(
                        "dupe_advanced_stage",
                        job_id=job_id,
                        title=job["title"],
                        company=job["company"],
                        dupe_of=adv["id"],
                    )
                    dupe_count += 1
                    break

    # ── Phase 2: Parallel scoring ──────────────────────────────────────────
    # Collect all enriched jobs (newly ingested + orphans from prior crashed runs)
    # and score them concurrently. Each worker spawns an aichat subprocess;
    # ThreadPoolExecutor is sufficient because the GIL is released during subprocess.run().
    to_score = conn.execute("""
        SELECT id, title, company, location, raw_jd_text FROM jobs
        WHERE stage = 'enriched'
          AND (dupe_of = '' OR dupe_of IS NULL)
    """).fetchall()

    if to_score:
        score_total = len(to_score)
        log_event("scoring_started", total=score_total, workers=SCORE_WORKERS)
        score_errors = 0

        def _score_worker(row):
            """Score a single job. Returns (job_id, scored_dict, latency_ms)."""
            return (
                row["id"],
                *score_job(
                    row["title"],
                    row["company"] or "",
                    row["location"] or "",
                    row["raw_jd_text"] or "",
                    candidate_profile,
                    feedback_block=_FEEDBACK_BLOCK,
                ),
            )

        with ThreadPoolExecutor(max_workers=SCORE_WORKERS) as executor:
            futures = {executor.submit(_score_worker, row): row for row in to_score}

            for i, future in enumerate(as_completed(futures), 1):
                row = futures[future]
                try:
                    job_id, scored, latency_ms = future.result()
                except Exception as e:
                    log_event("score_error", job_id=row["id"], error=str(e))
                    score_errors += 1
                    print(f"  [{i}/{score_total}] ERROR {row['title'][:40]} @ {row['company'] or '?'}: {e}", flush=True)
                    continue

                now = datetime.now(UTC).isoformat()
                stage = "manual_review" if scored.get("score_status") == "manual_review" else "scored"
                status = "manual_review" if stage == "manual_review" else "active"

                conn.execute(
                    """
                    UPDATE jobs SET
                        relevance_score=?, interview_likelihood=?, strengths_alignment=?,
                        industry_sector=?, comp_estimate=?, ai_notes=?,
                        score_status=?, score_flag_reason=?, remote_status=?,
                        stage=?, stage_updated=?, status=?, updated_at=?
                    WHERE id=?
                """,
                    (
                        scored.get("relevance_score"),
                        scored.get("interview_likelihood"),
                        scored.get("strengths_alignment"),
                        scored.get("industry_sector", ""),
                        scored.get("comp_estimate", ""),
                        scored.get("ai_notes", ""),
                        scored.get("score_status", "manual_review"),
                        scored.get("score_flag_reason", ""),
                        scored.get("remote_status", "Unknown"),
                        stage,
                        now,
                        status,
                        now,
                        job_id,
                    ),
                )
                conn.commit()
                write_audit(conn, job_id, "stage", "enriched", stage)
                scored_count += 1

                # Input estimate: raw JD + profile + feedback block + title/company framing.
                # Output estimate: the JSON scorer response.
                scoring_input = (row["raw_jd_text"] or "") + candidate_profile + (_FEEDBACK_BLOCK or "")
                scoring_output = str(scored)
                log_call(
                    conn,
                    job_id=job_id,
                    operation="score",
                    model=SCORER_MODEL,
                    input_text=scoring_input,
                    output_text=scoring_output,
                    latency_ms=latency_ms,
                    success=True,
                )
                conn.commit()

                print(
                    f"  [{i}/{score_total}] score={scored.get('relevance_score')} "
                    f"{row['title'][:40]} @ {row['company'] or '?'} [{latency_ms}ms]",
                    flush=True,
                )

        log_event("scoring_complete", total=score_total, scored=scored_count, errors=score_errors)

    rescored = score_null_manual_review_rows(conn, candidate_profile, _FEEDBACK_BLOCK)
    if rescored:
        log_event("null_score_retry_complete", rescored=rescored)

    conn.close()

    linkedin_429_stats = get_linkedin_rate_limit_stats()
    if linkedin_429_stats["count"] > 0:
        log_event(
            "linkedin_rate_limited",
            count=linkedin_429_stats["count"],
            total_wait=linkedin_429_stats["total_wait"],
        )

    log_event(
        "pipeline_complete",
        new=new_count,
        dupes=dupe_count,
        scored=scored_count,
        noise_skipped=noise_count,
    )

    _run_sync_sheet()
    notify(f"Triage done: {new_count} new, {dupe_count} dupes, {scored_count} scored ({SCORE_WORKERS} workers)")


def _run_sync_sheet():
    """Run sync_sheet.py as a subprocess and surface a non-zero exit.

    sync_sheet.py logs its own ``sync_failed`` event for exceptions raised
    inside its main ``try:`` block, but pre-try failures (missing creds
    file, sqlite3 connect failure, import-time crash) bypass that log.
    Capturing the return code here guarantees an observable signal in
    ``pipeline.jsonl`` regardless of where sync_sheet died.

    Returns the exit code. Does not raise — triage's DB work is already
    complete by the time this runs, and we still want the final notify()
    ntfy ping to fire so the operator sees the run finished.
    """
    result = subprocess.run(
        [sys.executable, f"{BASE}/scripts/sync_sheet.py"],
        check=False,
    )
    if result.returncode != 0:
        log_event("triage_sync_failed", returncode=result.returncode)
    return result.returncode


def notify(message):
    topic = None
    try:
        with open(f"{BASE}/config/ntfy_topic.txt") as f:
            topic = f.read().strip()
    except FileNotFoundError:
        pass
    if not topic:
        # Fall back to data/.env NTFY_TOPIC
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
        subprocess.run(["curl", "-s", "-d", message, f"https://ntfy.sh/{topic}"], capture_output=True, timeout=10)
    except Exception:
        pass


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log_event("pipeline_crash", error=str(e), traceback=traceback.format_exc())
        raise
