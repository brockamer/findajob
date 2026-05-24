"""Config-change detector for hybrid-windowing causal attribution.

Hashes each tracked lever's content and writes a ``config_changes`` row
when the hash differs from the most recent row for that lever.

Called from three surfaces:
- ``src/findajob/triage/orchestrator.py`` — pre-scoring
- ``src/findajob/web/routes/config.py`` — after /config/ POST
- ``src/findajob/onboarding/injector.py`` — after paste-back
"""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import findajob.paths as _paths

_LEVERS: dict[str, str] = {
    "profile": "candidate_context/profile.md",
    "master_resume": "candidate_context/master_resume.md",
    "scorer_prompt": "config/roles/job_scorer.md",
    "resume_tailor_prompt": "config/roles/resume_tailor.md",
    "cover_letter_prompt": "config/roles/cover_letter_writer.md",
    "briefing_writer_prompt": "config/roles/briefing_writer.md",
    "outreach_drafter_prompt": "config/roles/outreach_drafter.md",
    "company_researcher_prompt": "config/roles/company_researcher.md",
    "queries": "config/jsearch_queries.txt",
    "excluded_employers": "config/excluded_employers.yaml",
    "feed_urls": "config/feed_urls.txt",
    "prefilter_rules": "config/prefilter_rules.yaml",
    "in_domain_patterns": "config/in_domain_patterns.yaml",
    "target_companies": "config/target_companies.md",
}


def _hash_file(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except (FileNotFoundError, OSError):
        return None


def _latest_hash(conn: sqlite3.Connection, lever: str) -> str | None:
    row = conn.execute(
        "SELECT content_hash FROM config_changes WHERE lever=? ORDER BY id DESC LIMIT 1",
        (lever,),
    ).fetchone()
    return row[0] if row else None


def detect_and_record(
    conn: sqlite3.Connection,
    *,
    changed_by: str = "manual",
    change_summary: str | None = None,
) -> list[str]:
    """Scan tracked levers and insert rows for any with changed hashes.

    Returns list of lever names that were recorded (empty if nothing changed).
    """
    recorded: list[str] = []
    base = Path(_paths.BASE)
    for lever, relpath in _LEVERS.items():
        current = _hash_file(base / relpath)
        if current is None:
            continue
        if current == _latest_hash(conn, lever):
            continue
        conn.execute(
            "INSERT INTO config_changes (lever, changed_by, change_summary, content_hash) VALUES (?, ?, ?, ?)",
            (lever, changed_by, change_summary, current),
        )
        conn.commit()
        recorded.append(lever)
    return recorded
