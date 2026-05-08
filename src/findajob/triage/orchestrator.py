"""Daily triage orchestrator — fetch, dedupe, enrich, score.

Extracted from `scripts/triage.py` in M3 (#537). Module-load side effects
(SIGTERM hijack, `_FEEDBACK_BLOCK = _build_feedback_block()` DB read,
`SCORER_MODEL = role_model(...)` file read, `load_env()`) all moved
into `main()` so this module can be imported safely from tests and other
modules without producing real I/O. The script-entry-point behavior is
preserved by `scripts/triage.py` which installs the SIGTERM handler and
calls `main()`.

`_on_sigterm` is exposed at module scope so the shim can install it.
"""

import os
import shutil
import sqlite3
import subprocess
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path

from findajob.cleaning import fingerprint, is_coarse_location, loose_fingerprint, normalize
from findajob.cost_tracking import log_call, role_model
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
from findajob.notifications.ntfy import quick_notify
from findajob.onboarding import is_complete as _onboarding_is_complete
from findajob.paths import BASE
from findajob.scoring import _build_feedback_block, score_job
from findajob.triage.contacts import find_contacts
from findajob.triage.null_score_retry import score_null_manual_review_rows
from findajob.utils import (
    is_aggregator_company,
    is_ingest_noise_title,
    load_env,
    log_event,
    write_audit,
)

DB_PATH = f"{BASE}/data/pipeline.db"
PROFILE_PATH = f"{BASE}/candidate_context/profile.md"

SCORE_WORKERS = 6  # concurrent LLM scoring threads


# ── Signal handler: log a termination event before exiting ───────────────────
# systemd sends SIGTERM when the service hits TimeoutStartSec (default: 30min).
# Without this handler the process dies silently and pipeline_complete never
# fires, causing notify.py health-check to miss a real failure.
#
# The handler is installed by the script entry point (scripts/triage.py), not
# at module import — keeps this module safely importable from tests.
def _on_sigterm(signum, frame):
    log_event("pipeline_terminated", signal="SIGTERM", note="Received SIGTERM — likely systemd timeout or manual stop.")
    sys.exit(143)  # 128 + SIGTERM(15)


# ── Main Pipeline ──
def main(gmail_since_days: int | None = None):
    # Module-load side effects deferred to here so import is safe:
    load_env()
    scorer_model = role_model("job_scorer")
    feedback_block = _build_feedback_block()

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

    # ── Fetch with retry ──────────────────────────────────────────────────
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
        gmail_jobs = fetch_gmail_jobs(since_days=gmail_since_days)

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
                # Company unresolvable — reject immediately, don't waste a scorer call.
                # reject_reason is "Other" (canonical) so it doesn't pollute the user-facing
                # vocabulary; per-row diagnostic lives in pipeline.jsonl via log_event below.
                conn.execute(
                    """
                    UPDATE jobs SET stage='rejected', stage_updated=?, status='rejected',
                           reject_reason='Other', updated_at=?
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
    # and score them concurrently. ThreadPoolExecutor is sufficient because each
    # worker spends its time blocked on an OpenRouter HTTP call.
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
            """Score a single job. Returns (job_id, scored, latency_ms, completion)."""
            return (
                row["id"],
                *score_job(
                    row["title"],
                    row["company"] or "",
                    row["location"] or "",
                    row["raw_jd_text"] or "",
                    candidate_profile,
                    feedback_block=feedback_block,
                ),
            )

        with ThreadPoolExecutor(max_workers=SCORE_WORKERS) as executor:
            futures = {executor.submit(_score_worker, row): row for row in to_score}

            for i, future in enumerate(as_completed(futures), 1):
                row = futures[future]
                try:
                    job_id, scored, latency_ms, completion = future.result()
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

                # cost_usd_override + token overrides come from response.usage when
                # the LLM was actually called (#470). All three travel together so
                # the row is fully API-authoritative on the wrapper path. Prefilter
                # hits (completion=None) fall back to the heuristic against the
                # reconstructed input/output text — same behavior as pre-#470.
                scoring_input = (row["raw_jd_text"] or "") + candidate_profile + (feedback_block or "")
                scoring_output = str(scored)
                log_call(
                    conn,
                    job_id=job_id,
                    operation="score",
                    model=scorer_model,
                    input_text=scoring_input,
                    output_text=scoring_output,
                    latency_ms=latency_ms,
                    success=True,
                    cost_usd_override=(completion.cost_usd if completion is not None else None),
                    input_tokens_override=(completion.prompt_tokens if completion is not None else None),
                    output_tokens_override=(completion.completion_tokens if completion is not None else None),
                )
                conn.commit()

                print(
                    f"  [{i}/{score_total}] score={scored.get('relevance_score')} "
                    f"{row['title'][:40]} @ {row['company'] or '?'} [{latency_ms}ms]",
                    flush=True,
                )

        log_event("scoring_complete", total=score_total, scored=scored_count, errors=score_errors)

    rescored = score_null_manual_review_rows(conn, candidate_profile, feedback_block)
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

    quick_notify(f"Triage done: {new_count} new, {dupe_count} dupes, {scored_count} scored ({SCORE_WORKERS} workers)")
