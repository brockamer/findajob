#!/usr/bin/env python3
"""Backfill jobs.scored_by for existing rows. Idempotent.

Heuristic classification from score_flag_reason and ai_notes.
Ambiguous rows default to 'llm'.
"""

from __future__ import annotations

from findajob.db import connect
from findajob.paths import BASE

DB_PATH = f"{BASE}/data/pipeline.db"


def classify(score_flag_reason: str | None, ai_notes: str | None) -> str:
    sfr = (score_flag_reason or "").lower()
    notes = (ai_notes or "").lower()
    if "pre-filter hard reject" in sfr or "pre-filter hard reject" in notes:
        return "prefilter_stage1"
    if "excluded employer" in sfr or "excluded employer" in notes:
        return "prefilter_stage1"
    if "pre-filter in-domain/no-jd" in sfr or "pre-filter in-domain/no-jd" in notes:
        return "prefilter_stage2"
    return "llm"


def main() -> None:
    conn = connect(DB_PATH, timeout=30)
    rows = conn.execute("SELECT id, score_flag_reason, ai_notes FROM jobs").fetchall()
    for job_id, sfr, notes in rows:
        conn.execute("UPDATE jobs SET scored_by=? WHERE id=?", (classify(sfr, notes), job_id))
    conn.commit()
    conn.close()
    print(f"Backfilled scored_by on {len(rows)} rows.")


if __name__ == "__main__":
    main()
