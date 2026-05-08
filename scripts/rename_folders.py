#!/usr/bin/env python3
# scripts/rename_folders.py
"""
Rename existing company folders to include abbreviated job title.
Old format: {Company}_{YYYY-MM-DD}_{HHMMSS}
New format: {Company}_{AbbrevTitle}_{YYYY-MM-DD}_{HHMMSS}

Matches folders to DB jobs via prep_folder_path. Folders without a DB match are skipped.
Updates prep_folder_path in DB after each rename.
Safe to re-run — skips folders already in the new format.
"""

import os
import re
import sqlite3

from findajob.db import connect
from findajob.paths import BASE

DB_PATH = f"{BASE}/data/pipeline.db"
COMPANIES = f"{BASE}/companies"

DATETIME_PAT = re.compile(r"^(.+)_(\d{4}-\d{2}-\d{2})_(\d{6})$")


def abbrev_title(title, max_words=3):
    title = re.sub(r"\s*\(.*?\)", "", title)
    title = re.sub(r"[^\w\s-]", "", title)
    words = [w for w in title.split() if w][:max_words]
    return "_".join(words) if words else "Job"


def main():
    conn = connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row

    # Build lookup: old_folder_path → (job_id, title)
    # Also index by basename to handle folders moved into subdirs
    jobs = conn.execute("""
        SELECT id, title, prep_folder_path FROM jobs
        WHERE prep_folder_path IS NOT NULL AND prep_folder_path != ''
    """).fetchall()
    path_to_job = {r["prep_folder_path"]: r for r in jobs}
    basename_to_job = {os.path.basename(r["prep_folder_path"]): r for r in jobs}

    renamed = 0
    skipped = 0

    # Walk companies/ and all stage subdirs
    search_dirs = [
        COMPANIES,
        os.path.join(COMPANIES, "_rejected"),
        os.path.join(COMPANIES, "_applied"),
        os.path.join(COMPANIES, "_waitlisted"),
    ]

    for search_dir in search_dirs:
        if not os.path.isdir(search_dir):
            continue
        for name in sorted(os.listdir(search_dir)):
            old_path = os.path.join(search_dir, name)
            if not os.path.isdir(old_path):
                continue
            if name.startswith("_"):
                continue

            m = DATETIME_PAT.match(name)
            if not m:
                print(f"SKIP (no timestamp pattern): {name}")
                skipped += 1
                continue

            company_part, date_part, time_part = m.group(1), m.group(2), m.group(3)

            # Look up job by exact path, then fall back to basename (handles subdir moves)
            job = path_to_job.get(old_path) or basename_to_job.get(name)
            if not job:
                print(f"SKIP (no DB match): {name}")
                skipped += 1
                continue

            title_abbrev = abbrev_title(job["title"])
            new_name = f"{company_part}_{title_abbrev}_{date_part}_{time_part}"
            new_path = os.path.join(search_dir, new_name)

            # Skip if the abbrev is already embedded in the company_part (already renamed)
            if f"_{title_abbrev}" in company_part or new_name == name:
                print(f"OK   (already renamed): {name}")
                continue

            if os.path.exists(new_path):
                print(f"SKIP (target exists): {name} → {new_name}")
                skipped += 1
                continue

            os.rename(old_path, new_path)
            conn.execute("UPDATE jobs SET prep_folder_path=? WHERE id=?", (new_path, job["id"]))
            conn.commit()
            print(f"RENAMED: {name}")
            print(f"      → {new_name}")
            renamed += 1

    conn.close()
    print(f"\nDone. {renamed} renamed, {skipped} skipped.")


if __name__ == "__main__":
    main()
