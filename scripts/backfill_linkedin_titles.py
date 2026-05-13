#!/usr/bin/env python3
"""Backfill degenerate `jobs.title` values for gmail_linkedin rows (#656).

When the operator self-emails a LinkedIn job URL from the Android or iOS
share flow, the email-anchor parser stores the URL string (or the company
name) as `jobs.title`. The real title is in the LinkedIn API response that
triage already pays for — this script re-fetches it for pre-existing rows
that landed before the autofix shipped.

Usage:
    backfill_linkedin_titles.py --dry-run         # report candidates only
    backfill_linkedin_titles.py                   # apply fixes
    backfill_linkedin_titles.py --limit 5         # cap how many to process

The autofix mirrors the triage flow: re-fetch via fetch_linkedin_job_data,
swap in the real title, recompute fingerprint, mark as dupe if a collision
exists. audit_log gets one `title_backfill` event per row.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import UTC, datetime

from findajob.audit import log_event, write_audit
from findajob.cleaning import (
    extract_linkedin_job_id,
    fingerprint,
    is_coarse_location,
    is_degenerate_title,
    loose_fingerprint,
)
from findajob.db import connect
from findajob.fetchers import fetch_linkedin_job_data
from findajob.paths import BASE, load_env

DB_PATH = f"{BASE}/data/pipeline.db"


def find_candidates(conn: sqlite3.Connection, limit: int | None = None) -> list[sqlite3.Row]:
    """Return gmail_linkedin rows whose stored title is degenerate.

    `dupe_of` is filtered for both NULL and empty-string because legacy rows
    (pre-#xxx column normalization) carry `''` rather than NULL — observed on
    operator's stack 2026-05-13.
    """
    rows = conn.execute(
        """
        SELECT id, title, company, url, location, fingerprint, loose_fingerprint, stage, dupe_of
        FROM jobs
        WHERE source = 'gmail_linkedin'
          AND (dupe_of IS NULL OR dupe_of = '')
          AND stage != 'rejected'
        """
    ).fetchall()
    candidates = [r for r in rows if is_degenerate_title(r["title"], r["company"] or "", r["url"] or "")]
    if limit is not None:
        candidates = candidates[:limit]
    return candidates


def backfill_row(conn: sqlite3.Connection, row: sqlite3.Row, dry_run: bool) -> str:
    """Attempt to recover the real title for one row.

    Returns a status string for reporting: 'fixed', 'duplicated', 'no_api_id',
    'api_no_title', 'still_degenerate', 'skipped_dry_run'.
    """
    api_id = extract_linkedin_job_id(row["url"])
    if not api_id:
        return "no_api_id"

    result = fetch_linkedin_job_data(api_id)
    cached_title = result.get("title")
    if not cached_title:
        return "api_no_title"
    if is_degenerate_title(cached_title, row["company"] or "", row["url"] or ""):
        return "still_degenerate"

    if dry_run:
        print(f"  would fix: {row['id'][:8]}…  '{row['title'][:60]}' → '{cached_title[:60]}'")
        return "skipped_dry_run"

    old_title = row["title"]
    company = row["company"] or ""
    location = row["location"] or ""
    new_fp = fingerprint(cached_title, company, location)
    new_lfp = loose_fingerprint(cached_title, company)
    now = datetime.now(UTC).isoformat()

    # Mirror the triage post-resolution dedupe check
    existing = conn.execute("SELECT id FROM jobs WHERE fingerprint = ? AND id != ?", (new_fp, row["id"])).fetchone()
    if not existing:
        incoming_coarse = is_coarse_location(location)
        loose_matches = conn.execute(
            "SELECT id, location FROM jobs WHERE loose_fingerprint = ? AND id != ?",
            (new_lfp, row["id"]),
        ).fetchall()
        for lm in loose_matches:
            if incoming_coarse or is_coarse_location(lm["location"] or ""):
                existing = lm
                break

    if existing:
        conn.execute(
            "UPDATE jobs SET dupe_of=?, stage=?, stage_updated=?, updated_at=? WHERE id=?",
            (existing["id"], "rejected", now, now, row["id"]),
        )
        conn.commit()
        write_audit(conn, row["id"], "stage", row["stage"], "rejected")
        log_event(
            "title_backfill_duplicated",
            job_id=row["id"],
            old_title=old_title,
            new_title=cached_title,
            dupe_of=existing["id"],
        )
        print(f"  duplicated: {row['id'][:8]}…  '{old_title[:60]}' → matches existing {existing['id'][:8]}…")
        return "duplicated"

    conn.execute(
        "UPDATE jobs SET title=?, fingerprint=?, loose_fingerprint=?, updated_at=? WHERE id=?",
        (cached_title, new_fp, new_lfp, now, row["id"]),
    )
    conn.commit()
    write_audit(conn, row["id"], "title", old_title, cached_title)
    log_event(
        "title_backfill",
        job_id=row["id"],
        old_title=old_title,
        new_title=cached_title,
    )
    print(f"  fixed:    {row['id'][:8]}…  '{old_title[:60]}' → '{cached_title[:60]}'")
    return "fixed"


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill degenerate gmail_linkedin titles (#656)")
    parser.add_argument("--dry-run", action="store_true", help="Report candidates without writing")
    parser.add_argument("--limit", type=int, default=None, help="Cap candidates processed")
    args = parser.parse_args()

    load_env()
    conn = connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row

    candidates = find_candidates(conn, limit=args.limit)
    print(f"Candidates: {len(candidates)} gmail_linkedin rows with degenerate titles")
    if args.dry_run:
        print("(dry-run — no writes will occur)")

    counts: dict[str, int] = {}
    for row in candidates:
        status = backfill_row(conn, row, dry_run=args.dry_run)
        counts[status] = counts.get(status, 0) + 1

    print("\nSummary:")
    for status, n in sorted(counts.items()):
        print(f"  {status}: {n}")

    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
