#!/usr/bin/env python3
# ~/JobSearchPipeline/scripts/manual_prep.py
"""
Read a manual job file and kick off prep_application.py.

Usage:
    python3 scripts/manual_prep.py [path_to_file]

Default file: <repo_root>/manual_job.txt

File format:
    company: CompanyName
    title: Job Title
    url: https://linkedin.com/jobs/view/...
    ---
    Full JD text below the --- separator
"""

import hashlib
import os
import subprocess
import sys
import uuid
from datetime import UTC, datetime

from findajob.db import connect
from findajob.paths import BASE, IMAGE_ROOT

DB_PATH = f"{BASE}/data/pipeline.db"
DEFAULT_FILE = f"{BASE}/manual_job.txt"


def parse_file(path):
    with open(path) as f:
        raw = f.read()

    if "---" not in raw:
        print("ERROR: File must contain '---' separator between header and JD text.")
        sys.exit(1)

    header, jd_text = raw.split("---", 1)
    jd_text = jd_text.strip()

    meta = {}
    for line in header.strip().splitlines():
        if ":" in line:
            key, val = line.split(":", 1)
            meta[key.strip().lower()] = val.strip()

    for required in ("company", "title", "url"):
        if required not in meta or not meta[required]:
            print(f"ERROR: Missing required field '{required}' in header.")
            sys.exit(1)

    if len(jd_text) < 50:
        print(f"WARNING: JD text is only {len(jd_text)} chars — prep may produce weak output.")

    return meta["company"], meta["title"], meta["url"], jd_text


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_FILE
    if not os.path.exists(path):
        print(f"ERROR: File not found: {path}")
        sys.exit(1)

    company, title, url, jd_text = parse_file(path)
    job_id = f"manual-{uuid.uuid4().hex[:8]}"
    now = datetime.now(UTC).isoformat()

    # Insert into DB
    conn = connect(DB_PATH, timeout=30)
    conn.execute(
        """INSERT INTO jobs (id, fingerprint, source, title, company, url, raw_jd_text, stage, created_at, updated_at)
        VALUES (?, ?, 'manual', ?, ?, ?, ?, 'discovered', ?, ?)""",
        (job_id, hashlib.sha256(url.encode()).hexdigest()[:16], title, company, url, jd_text, now, now),
    )
    conn.commit()
    conn.close()

    print(f"Inserted {job_id}: {company} / {title}")
    print(f"JD length: {len(jd_text)} chars")
    print("Launching prep_application.py ...")

    # Kick off prep
    result = subprocess.run(
        [sys.executable, f"{IMAGE_ROOT}/scripts/prep_application.py", company, title, url, job_id], text=True
    )
    sys.exit(result.returncode)


if __name__ == "__main__":
    main()
