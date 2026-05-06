#!/usr/bin/env python3
# ~/JobSearchPipeline/scripts/probe_scorer.py
"""
Show raw scorer output for manual_review rows via findajob.llm.openrouter (#470).
Prints title, company, raw stdout, and parsed score_status. Run manually.
"""

import os
import sqlite3

from findajob.llm.openrouter import OpenRouterError, complete
from findajob.paths import BASE

DB_PATH = f"{BASE}/data/pipeline.db"
PROFILE_PATH = f"{BASE}/candidate_context/profile.md"


def load_env(path):
    with open(os.path.expanduser(path)) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ[k.strip()] = v.strip().strip("'\"")


load_env(f"{BASE}/data/.env")

_JD_WALL_SIGNALS = [
    "you need to enable javascript",
    "enable javascript to run this app",
    "403 forbidden",
    "cross-site request forgeries",
    "we're signing you in",
    "sign in to",
    "access denied",
    "job not found",
    "this job may have been",
    "our careers site has moved",
]


def jd_is_usable(jd):
    if not jd or len(jd.strip()) < 30:
        return False
    return not any(s in jd.lower() for s in _JD_WALL_SIGNALS)


with open(PROFILE_PATH) as f:
    profile = f.read()

conn = sqlite3.connect(DB_PATH, timeout=30)
conn.row_factory = sqlite3.Row

# Pull manual_review rows — prioritise obvious rejects by title keyword
rows = conn.execute("""
    SELECT id, title, company, location, raw_jd_text
    FROM jobs
    WHERE score_status = 'manual_review'
    ORDER BY
        CASE
            WHEN lower(title) LIKE '%software engineer%' THEN 0
            WHEN lower(title) LIKE '%swe%'               THEN 0
            WHEN lower(title) LIKE '%security analyst%'  THEN 1
            WHEN lower(title) LIKE '%controls engineer%' THEN 2
            WHEN lower(title) LIKE '%network%'           THEN 3
            WHEN lower(title) LIKE '%sales%'             THEN 4
            ELSE 5
        END,
        id
    LIMIT 20
""").fetchall()

conn.close()

print(f"Probing {len(rows)} manual_review rows\n{'=' * 60}")

for row in rows:
    title = row["title"]
    company = row["company"] or ""
    location = row["location"] or ""
    jd = row["raw_jd_text"]
    effective_jd = jd if jd_is_usable(jd) else "[Job description unavailable — score from title and company only]"

    cached_prefix = f"CANDIDATE PROFILE:\n{profile}\n\n---\n\n"
    job_tail = (
        f"Evaluate this job posting for the candidate described above.\n"
        f"Job: {title} at {company}\n"
        f"Location: {location}\n"
        f"JD:\n{effective_jd[:6000]}"
    )

    try:
        result = complete(
            role="job_scorer",
            prompt=job_tail,
            cached_prefix=cached_prefix,
            timeout_s=60,
        )
        raw = result.text.strip()
        # Try to parse score_status and relevance_score for quick summary
        import json as _json

        try:
            clean = raw
            if clean.startswith("```"):
                clean = "\n".join(clean.split("\n")[1:])
            if clean.endswith("```"):
                clean = clean[: clean.rfind("```")]
            parsed = _json.loads(clean.strip())
            summary = (
                f"score_status={parsed.get('score_status')} "
                f"score={parsed.get('relevance_score')} "
                f"flag={parsed.get('score_flag_reason')}"
            )
        except Exception as e:  # noqa: BLE001
            summary = f"PARSE ERROR: {e}"
    except OpenRouterError as e:
        raw = ""
        summary = f"WRAPPER ERROR: kind={e.kind} status={e.status_code}"

    print(f"\nTITLE: {title}")
    print(f"COMPANY: {company}")
    print(f"SUMMARY: {summary}")
    print(f"RAW OUTPUT:\n{raw}")
    print("-" * 60)
