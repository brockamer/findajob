"""Shared manual-ingest helper — the one place that turns a (company, title,
url, jd, ...) tuple from an operator into a row in ``jobs``.

Used by ``src/findajob/web/routes/ingest.py`` — the `/ingest/manual` web
form (#62). The legacy ``scripts/ingest_form.py`` Google-Form polling
loop still carries its own inline ingest logic; its timer was disabled
in #62 and the script is kept around only as a frozen manual-run fallback
until the Google Form is fully retired.

Behavior:

- Cleans title/company via ``findajob.cleaning``.
- Computes strict + loose fingerprints and runs the same two-tier dedup
  ``scripts/triage.py`` uses: strict ``fingerprint`` → URL fallback →
  ``loose_fingerprint`` when either side has a coarse location (#182).
- Inserts with ``stage='scored'``, ``relevance_score=8``, ``apply_flag=0``.
  Writes ``raw_jd_text`` when provided, so ``prep_application.py`` uses
  the pasted JD directly and never re-curls the URL (#79 absorption).
- When ``generate_folder=True``, launches ``prep_application.py`` via
  ``subprocess.Popen(start_new_session=True)`` so the caller returns
  immediately.

The caller decides the ``source`` label — ``'web_manual'`` for the web
form, distinct from the legacy script's ``'manual_form'`` so the two
paths stay distinguishable in ``jobs.source`` post-retirement.
"""

from __future__ import annotations

import sqlite3
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from findajob.actions import reactivate_from_ingest, refresh_active_job, un_reject_job
from findajob.cleaning import (
    clean_company,
    clean_title,
    fingerprint,
    is_coarse_location,
    loose_fingerprint,
)
from findajob.paths import BASE
from findajob.utils import log_event


@dataclass(frozen=True)
class IngestResult:
    """Outcome of a single ``ingest_manual_job`` call.

    - ``status="ingested"``: new row inserted; ``job_id`` is the new id.
    - ``status="duplicate"``: existing row matched by an unhandled state
      (should not occur in practice after _handle_duplicate is wired up).
    - ``status="resurfaced"``: existing row was un-rejected / reactivated /
      refreshed; job is now on the Dashboard.
    - ``status="already_applied"``: existing row is post-application; no
      mutation. Link to /board/applied.
    - ``status="not_selected"``: company rejected the application; no
      mutation. Link to /board/rejected and materials folder.
    """

    status: Literal["ingested", "duplicate", "resurfaced", "already_applied", "not_selected"]
    job_id: str
    company: str
    title: str
    fingerprint: str | None = None
    existing_match: str | None = None  # "strict" / "url" / "loose"
    existing_stage: str | None = None  # stage of the row at submission time
    prep_folder_path: str | None = None  # for not_selected materials link
    prep_launched: bool = False


_APPLIED_STAGES = frozenset({"applied", "interview", "offer", "withdrew"})


def _handle_duplicate(
    conn: sqlite3.Connection,
    existing_id: str,
    overwrite_fields: dict[str, str],
) -> IngestResult:
    """Fetch the existing row and route to the right resurface path."""
    row = conn.execute(
        """SELECT id, fingerprint, title, company, stage, relevance_score,
                  reject_reason, prep_folder_path
           FROM jobs WHERE id=?""",
        (existing_id,),
    ).fetchone()

    stage = row["stage"]
    common = {
        "job_id": existing_id,
        "fingerprint": row["fingerprint"],
        "company": row["company"],
        "title": row["title"],
        "existing_stage": stage,
    }

    if stage in _APPLIED_STAGES:
        return IngestResult(status="already_applied", **common)

    if stage == "not_selected":
        return IngestResult(
            status="not_selected",
            prep_folder_path=row["prep_folder_path"],
            **common,
        )

    if stage == "rejected":
        un_reject_job(conn, row, overwrite_fields)
        return IngestResult(status="resurfaced", **common)

    if stage == "waitlisted":
        reactivate_from_ingest(conn, row, overwrite_fields)
        return IngestResult(status="resurfaced", **common)

    # scored / manual_review / prep_in_progress / materials_drafted
    refresh_active_job(conn, row, overwrite_fields)
    return IngestResult(status="resurfaced", **common)


def ingest_manual_job(
    conn: sqlite3.Connection,
    *,
    company: str,
    title: str,
    url: str,
    location: str = "",
    remote_status: str = "Unknown",
    notes: str = "",
    known_contacts: str = "",
    raw_jd_text: str = "",
    generate_folder: bool = False,
    source: str,
) -> IngestResult:
    """Insert one manually-submitted job into ``jobs`` (or report a dup).

    ``conn`` is committed on insert; no commit on duplicate.

    Inputs are trimmed; ``title`` and ``company`` pass through
    ``clean_title`` / ``clean_company`` so fingerprints line up with
    automated-ingest rows for the same posting.
    """
    company = clean_company(company.strip())
    title = clean_title(title.strip())
    url = url.strip()
    location = location.strip()
    remote_status = remote_status.strip() or "Unknown"
    notes = notes.strip()
    known_contacts = known_contacts.strip()
    raw_jd_text = raw_jd_text.strip()

    fp = fingerprint(title, company, location)
    lfp = loose_fingerprint(title, company)

    existing = conn.execute("SELECT id FROM jobs WHERE fingerprint=?", (fp,)).fetchone()

    if not existing and url:
        existing = conn.execute("SELECT id FROM jobs WHERE url=?", (url,)).fetchone()

    if not existing:
        incoming_coarse = is_coarse_location(location)
        for row in conn.execute("SELECT id, location FROM jobs WHERE loose_fingerprint=?", (lfp,)).fetchall():
            if incoming_coarse or is_coarse_location(row["location"] or ""):
                existing = row
                break

    if existing:
        overwrite_fields = {
            "url": url,
            "location": location,
            "remote_status": remote_status,
            "raw_jd_text": raw_jd_text,
            "notes": notes,
            "known_contacts": known_contacts,
        }
        return _handle_duplicate(conn, existing["id"], overwrite_fields)

    now = datetime.now(UTC).isoformat()
    job_id = f"{source}-{fp}"

    conn.execute(
        """
        INSERT INTO jobs (
            id, fingerprint, loose_fingerprint, url, title, company, location,
            source, raw_jd_text, remote_status, known_contacts, ai_notes,
            relevance_score, stage, apply_flag,
            created_at, updated_at, dupe_of
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 8, 'scored', 0, ?, ?, '')
        """,
        (
            job_id,
            fp,
            lfp,
            url,
            title,
            company,
            location,
            source,
            raw_jd_text or None,
            remote_status,
            known_contacts,
            notes,
            now,
            now,
        ),
    )
    conn.commit()

    log_event(
        "manual_job_ingested",
        job_id=job_id,
        source=source,
        company=company,
        title=title,
        url=url,
        has_jd=bool(raw_jd_text),
    )

    prep_launched = False
    if generate_folder:
        subprocess.Popen(
            [
                sys.executable,
                f"{BASE}/scripts/prep_application.py",
                company,
                title,
                url,
                job_id,
            ],
            start_new_session=True,
        )
        prep_launched = True

    return IngestResult(
        status="ingested",
        job_id=job_id,
        fingerprint=fp,
        company=company,
        title=title,
        prep_launched=prep_launched,
    )
