#!/usr/bin/env python3
# ~/JobSearchPipeline/scripts/backfill_jd.py
"""
Backfill job descriptions in the pipeline DB.

Modes:
    backfill_jd.py              -- fetch missing JDs for gmail_linkedin jobs
    backfill_jd.py --truncated  -- re-fetch JDs truncated at old 8k cap (all sources)

Flags:
    --rescore    rescore affected jobs after backfill
    --dry-run    report what would be fetched (--truncated mode only)
"""

import os
import re
import sqlite3
import sys
import time
from datetime import UTC, datetime

from findajob.audit import log_event
from findajob.classification import JD_MAX_CHARS, strip_jd_boilerplate
from findajob.db import connect
from findajob.paths import BASE, load_env

DB_PATH = f"{BASE}/data/pipeline.db"

load_env()

_LINKEDIN_JOB_ID_RE = re.compile(r"linkedin\.com/(?:comm/)?jobs/view/(\d+)", re.IGNORECASE)


def extract_job_id(url):
    m = _LINKEDIN_JOB_ID_RE.search(url or "")
    return m.group(1) if m else None


def clean_company(raw):
    """Minimal company cleaning — strip trailing metadata."""
    if not raw:
        return ""
    raw = re.sub(r"\s*\d[\d,]+ followers\s*$", "", raw, flags=re.IGNORECASE)
    raw = re.sub(r"\s*·.*$", "", raw)
    return raw.strip()


def fetch_linkedin_jd(api_id):
    """Fetch JD and company from LinkedIn API. Returns (description, company) or (None, None)."""
    import requests as req

    api_key = os.environ.get("RAPIDAPI_KEY", "")
    if not api_key or not api_id:
        return None, None
    try:
        response = req.get(
            "https://jobs-api14.p.rapidapi.com/v2/linkedin/get",
            headers={
                "x-rapidapi-host": "jobs-api14.p.rapidapi.com",
                "x-rapidapi-key": api_key,
            },
            params={"id": str(api_id)},
            timeout=15,
        )
        response.raise_for_status()
        data = response.json()
        if data.get("hasError"):
            return None, None
        payload = data.get("data", {})
        description = payload.get("description", "") or ""
        company = (
            payload.get("companyName")
            or payload.get("company")
            or payload.get("organizationName")
            or (payload.get("hiringOrganization") or {}).get("name")
            or ""
        )
        desc = strip_jd_boilerplate(description)[:JD_MAX_CHARS] if description else None
        co = clean_company(company) if company else None
        return desc, co
    except Exception:
        return None, None


def fetch_greenhouse_jd(url):
    """Re-fetch JD from a Greenhouse URL. Returns stripped text or None."""
    import subprocess as sp

    from findajob.paths import PANDOC

    try:
        raw = sp.run(["curl", "-sL", "--max-time", "15", url], capture_output=True, text=True).stdout
        if not raw or len(raw.strip()) < 50:
            return None
        text = sp.run(
            [PANDOC, "-f", "html", "-t", "plain"], input=raw, capture_output=True, text=True, timeout=10
        ).stdout
        text = strip_jd_boilerplate(text)[:JD_MAX_CHARS]
        return text if len(text.strip()) >= 50 else None
    except Exception:
        return None


def fetch_curl_jd(url):
    """Re-fetch JD by curling a public URL. Returns stripped text or None."""
    import subprocess as sp

    from findajob.paths import PANDOC

    try:
        raw = sp.run(["curl", "-sL", "--max-time", "15", url], capture_output=True, text=True).stdout
        if not raw or len(raw.strip()) < 50:
            return None
        text = sp.run(
            [PANDOC, "-f", "html", "-t", "plain"], input=raw, capture_output=True, text=True, timeout=10
        ).stdout
        text = strip_jd_boilerplate(text)[:JD_MAX_CHARS]
        return text if len(text.strip()) >= 50 else None
    except Exception:
        return None


def backfill_truncated(dry_run=False):
    """Re-fetch JDs that were truncated at the old 8000-char cap."""
    conn = connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")

    rows = conn.execute("""
        SELECT id, url, title, company, source, raw_jd_text, stage
        FROM jobs
        WHERE LENGTH(raw_jd_text) BETWEEN 7900 AND 8000
          AND (dupe_of = '' OR dupe_of IS NULL)
          AND stage NOT IN ('rejected', 'withdrawn')
    """).fetchall()

    print(f"Truncated JDs to backfill: {len(rows)}")

    from collections import Counter

    source_counts = Counter(r["source"] for r in rows)
    for src, cnt in source_counts.most_common():
        print(f"  {src}: {cnt}")

    if dry_run:
        print("\n--dry-run: no changes made.")
        conn.close()
        return 0

    log_event("backfill_truncated_started", total=len(rows), by_source=dict(source_counts))

    fetched = 0
    skipped = 0
    failed = 0

    for i, row in enumerate(rows, 1):
        source = row["source"]
        old_len = len(row["raw_jd_text"])
        label = f"[{i}/{len(rows)}] {row['title'][:40]} @ {row['company'] or '(blank)'} ({source})"

        if source in ("jobsapi_indeed", "manual", "manual_form"):
            print(f"{label} -- SKIP (no re-fetch path)")
            skipped += 1
            continue

        new_jd = None
        if source == "greenhouse_json":
            new_jd = fetch_greenhouse_jd(row["url"])
            time.sleep(0.1)
        elif source in ("jobsapi_linkedin", "gmail_linkedin"):
            api_id = extract_job_id(row["url"])
            if api_id:
                new_jd, _ = fetch_linkedin_jd(api_id)
            time.sleep(0.3)
        else:
            new_jd = fetch_curl_jd(row["url"])
            time.sleep(0.1)

        if new_jd and len(new_jd.strip()) > old_len:
            now = datetime.now(UTC).isoformat()
            conn.execute("UPDATE jobs SET raw_jd_text=?, updated_at=? WHERE id=?", (new_jd, now, row["id"]))
            conn.commit()
            fetched += 1
            print(f"{label} -- OK {old_len} -> {len(new_jd)}")
        elif new_jd:
            skipped += 1
            print(f"{label} -- SKIP (new={len(new_jd)} <= old={old_len})")
        else:
            failed += 1
            print(f"{label} -- FAIL (no JD returned)")

    print(f"\nDone: {fetched} updated, {skipped} skipped, {failed} failed")
    log_event("backfill_truncated_complete", fetched=fetched, skipped=skipped, failed=failed)
    conn.close()
    return fetched


def main():
    rescore = "--rescore" in sys.argv
    truncated = "--truncated" in sys.argv
    dry_run = "--dry-run" in sys.argv

    if truncated:
        count = backfill_truncated(dry_run=dry_run)
        if rescore and count and not dry_run:
            print(f"\nRescoring {count} backfilled jobs...")
            import subprocess

            subprocess.run([sys.executable, f"{BASE}/scripts/rescore_all.py"], check=False)
        return

    # Original behavior: backfill missing gmail_linkedin JDs
    conn = connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")

    rows = conn.execute("""
        SELECT id, url, title, company, raw_jd_text, stage
        FROM jobs
        WHERE source = 'gmail_linkedin'
          AND (dupe_of = '' OR dupe_of IS NULL)
          AND stage != 'rejected'
    """).fetchall()

    candidates = []
    for r in rows:
        api_id = extract_job_id(r["url"])
        if not api_id:
            continue
        jd = r["raw_jd_text"] or ""
        if jd.strip() and len(jd.strip()) >= 50 and "unavailable" not in jd.lower():
            continue
        candidates.append((r, api_id))

    print(f"Jobs to backfill: {len(candidates)}")
    log_event("backfill_jd_started", total=len(candidates))

    fetched = 0
    failed = 0
    company_updated = 0
    backfilled_ids = []

    for i, (row, api_id) in enumerate(candidates, 1):
        print(f"[{i}/{len(candidates)}] {row['title'][:50]} @ {row['company'] or '(blank)'}", end="", flush=True)

        desc, company = fetch_linkedin_jd(api_id)

        if desc and len(desc.strip()) >= 30:
            now = datetime.now(UTC).isoformat()
            conn.execute("UPDATE jobs SET raw_jd_text=?, updated_at=? WHERE id=?", (desc, now, row["id"]))
            if company and not row["company"]:
                conn.execute("UPDATE jobs SET company=? WHERE id=?", (company, row["id"]))
                company_updated += 1
            conn.commit()
            fetched += 1
            backfilled_ids.append(row["id"])
            print(f"  OK {len(desc)} chars" + (f" +company={company}" if company and not row["company"] else ""))
        else:
            failed += 1
            print("  FAIL no JD returned")

        time.sleep(0.3)

    print(f"\nBackfill complete: {fetched} fetched, {failed} failed, {company_updated} companies updated")
    log_event("backfill_jd_complete", fetched=fetched, failed=failed, company_updated=company_updated)

    conn.close()

    if rescore and backfilled_ids:
        print(f"\nRescoring {fetched} backfilled jobs...")
        import subprocess

        subprocess.run([sys.executable, f"{BASE}/scripts/rescore_all.py"], check=False)


if __name__ == "__main__":
    main()
