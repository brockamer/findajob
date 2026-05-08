#!/usr/bin/env python3
# scripts/rescore_all.py
"""
Re-score jobs in the DB that have JD text.
Useful after switching scorer model or updating the job_scorer role prompt.
Run manually — not a scheduled job.

Usage:
    rescore_all.py                    # rescore every job in scored/manual_review/enriched
    rescore_all.py --stage enriched   # only rescore jobs in a specific stage
    rescore_all.py --min-score 7      # only rescore jobs currently scored >=7
    rescore_all.py --min-score 7 --limit 40
    rescore_all.py --dry-run          # report what would be rescored, no LLM calls
"""

import argparse
import sqlite3
import sys
import time
from datetime import UTC, datetime

from findajob.audit import log_event, write_audit
from findajob.cost_tracking import log_call, role_model
from findajob.db import connect
from findajob.paths import BASE, load_env
from findajob.scoring import _build_feedback_block, score_job

DB_PATH = f"{BASE}/data/pipeline.db"
PROFILE_PATH = f"{BASE}/candidate_context/profile.md"

SCORER_MODEL = role_model("job_scorer")

load_env()

_FEEDBACK_BLOCK = _build_feedback_block()


def main():
    parser = argparse.ArgumentParser(description="Rescore jobs in the pipeline DB.")
    parser.add_argument(
        "--min-score", type=int, default=None, help="Only rescore jobs with current relevance_score >= this value"
    )
    parser.add_argument("--limit", type=int, default=None, help="Stop after rescoring this many jobs")
    parser.add_argument(
        "--stage",
        type=str,
        default=None,
        help="Only rescore jobs in this stage (scored, manual_review, enriched)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Report what would be rescored without making LLM calls")
    args = parser.parse_args()

    conn = connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")

    # Fetch jobs that have JD text and are in a re-scoreable stage.
    # Exclude jobs that have progressed past scoring (applied, interviewing, etc.) —
    # overwriting their stage would corrupt the pipeline state.
    valid_stages = ("scored", "manual_review", "enriched")
    if args.stage:
        if args.stage not in valid_stages:
            print(f"Error: --stage must be one of {valid_stages}")
            sys.exit(1)
        stage_filter = "AND stage = ?"
        params = [args.stage]
    else:
        stage_filter = f"AND stage IN {valid_stages}"
        params = []

    query = f"""
        SELECT id, title, company, location, raw_jd_text, stage, score_status, relevance_score
        FROM jobs
        WHERE raw_jd_text IS NOT NULL AND raw_jd_text != ''
          {stage_filter}
    """
    if args.min_score is not None:
        query += " AND relevance_score >= ?"
        params.append(args.min_score)
    query += " ORDER BY relevance_score DESC, created_at DESC"
    if args.limit is not None:
        query += " LIMIT ?"
        params.append(args.limit)

    rows = conn.execute(query, params).fetchall()

    with open(PROFILE_PATH) as f:
        candidate_profile = f.read()

    total = len(rows)
    filter_desc = []
    if args.min_score is not None:
        filter_desc.append(f"min_score={args.min_score}")
    if args.limit is not None:
        filter_desc.append(f"limit={args.limit}")
    if args.dry_run:
        filter_desc.append("DRY RUN")
    filter_str = f" ({', '.join(filter_desc)})" if filter_desc else ""
    print(f"Jobs to rescore: {total}{filter_str}")

    if args.dry_run:
        print()
        print("Would rescore:")
        for r in rows[:20]:
            print(f"  [{r['relevance_score']}] {r['title'][:50]} @ {r['company']}")
        if total > 20:
            print(f"  ... (+{total - 20} more)")
        conn.close()
        return

    log_event("rescore_started", total=total, min_score=args.min_score, limit=args.limit)

    scored_count = 0
    manual_count = 0
    error_count = 0
    prefilter_count = 0

    for i, row in enumerate(rows, 1):
        job_id = row["id"]
        title = row["title"]
        company = row["company"] or ""
        location = row["location"] or ""
        jd_text = row["raw_jd_text"]
        old_stage = row["stage"]

        print(f"[{i}/{total}] {title} @ {company}", flush=True)

        try:
            scored, latency_ms, completion = score_job(
                title, company, location, jd_text, candidate_profile, _FEEDBACK_BLOCK
            )
        except Exception as e:
            print(f"  ERROR: {e}")
            log_event("rescore_error", job_id=job_id, error=str(e))
            error_count += 1
            continue

        now = datetime.now(UTC).isoformat()
        new_stage = "manual_review" if scored.get("score_status") == "manual_review" else "scored"
        new_status = "manual_review" if new_stage == "manual_review" else "active"

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
                new_stage,
                now,
                new_status,
                now,
                job_id,
            ),
        )
        conn.commit()

        if old_stage != new_stage:
            write_audit(conn, job_id, "stage", old_stage, new_stage)

        # cost_usd_override + token overrides come from response.usage when
        # the LLM was actually called (#470). All three travel together so the
        # row is fully API-authoritative on the wrapper path. Prefilter hits
        # (completion=None) fall back to the heuristic against the reconstructed
        # input/output text — matches the pre-#470 behavior of the local
        # score_job duplicate this commit replaced.
        scoring_input = (jd_text or "") + candidate_profile + (_FEEDBACK_BLOCK or "")
        scoring_output = str(scored)
        log_call(
            conn,
            job_id=job_id,
            operation="rescore",
            model=SCORER_MODEL,
            input_text=scoring_input,
            output_text=scoring_output,
            latency_ms=latency_ms,
            success=True,
            cost_usd_override=(completion.cost_usd if completion is not None else None),
            input_tokens_override=(completion.prompt_tokens if completion is not None else None),
            output_tokens_override=(completion.completion_tokens if completion is not None else None),
        )
        conn.commit()

        score = scored.get("relevance_score")
        prefiltered = latency_ms == 0
        if prefiltered:
            prefilter_count += 1
        print(f"  score={score} stage={new_stage} [{latency_ms}ms]{'  [prefilter]' if prefiltered else ''}", flush=True)

        if new_stage == "manual_review":
            manual_count += 1
        else:
            scored_count += 1

        if not prefiltered:
            time.sleep(0.3)  # Rate limit only applies to LLM calls

    conn.close()

    print(
        f"\nDone. scored={scored_count} manual_review={manual_count} errors={error_count} prefiltered={prefilter_count}"
    )
    log_event(
        "rescore_complete",
        total=total,
        scored=scored_count,
        manual_review=manual_count,
        errors=error_count,
        prefiltered=prefilter_count,
    )

    print("Done.")


if __name__ == "__main__":
    main()
