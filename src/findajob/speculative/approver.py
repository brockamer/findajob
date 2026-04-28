"""Approver: on operator approve, write 1 jobs row per kept role card.

Synthetic rows ship with:
- synthetic=1
- source='web_speculative'
- title prefixed with [SPEC]
- stage='scored'
- ai_notes populated from the card's why_this_fits + team

Approving with kept_indices=[] is equivalent to trash — sets status='trashed'.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import UTC, datetime

from findajob.cleaning import fingerprint
from findajob.speculative.parser import parse_role_cards
from findajob.utils import log_event, write_audit


def approve_request(
    conn: sqlite3.Connection,
    *,
    request_id: int,
    kept_indices: list[int],
) -> list[str]:
    """Approve a ready_for_review request, writing one jobs row per kept card.

    Returns the list of fingerprints written. Empty list when kept_indices is empty
    (status='trashed' instead of 'approved').

    Raises ValueError if the request is not in 'ready_for_review' status.
    """
    row = conn.execute(
        "SELECT id, company, status, role_cards_json, briefing_folder FROM speculative_requests WHERE id=?",
        (request_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"speculative_requests id={request_id} not found")
    if row["status"] != "ready_for_review":
        raise ValueError(
            f"cannot approve request id={request_id}: status is {row['status']!r}, expected 'ready_for_review'"
        )

    if not kept_indices:
        conn.execute(
            "UPDATE speculative_requests SET status='trashed' WHERE id=?",
            (request_id,),
        )
        conn.commit()
        log_event("speculative_request_trashed", request_id=request_id, company=row["company"])
        return []

    cards = parse_role_cards(row["role_cards_json"])
    company = row["company"]
    now = datetime.now(UTC).isoformat()
    fingerprints: list[str] = []

    for idx in kept_indices:
        if idx < 0 or idx >= len(cards):
            raise ValueError(f"kept_indices contains out-of-range index {idx} (have {len(cards)} cards)")
        card = cards[idx]
        title = f"[SPEC] {card.title}"
        ai_notes = (
            f"WHY THIS FITS: {card.why_this_fits_candidate}\n\n"
            f"LIKELY TEAM: {card.likely_team_or_org}\n\n"
            f"SUGGESTED CONTACT: {card.suggested_contact_type}"
        )
        # Speculative rows have no URL — synthesize a sentinel that's distinct.
        url = f"speculative://{company}/{idx}/{request_id}"
        # Using fingerprint() of (title, company, '') — same hashing as real rows.
        fp = fingerprint(title, company, "")
        job_id = str(uuid.uuid4())
        conn.execute(
            """INSERT INTO jobs (id, fingerprint, url, title, company, location, source,
                                  raw_jd_text, relevance_score, score_status,
                                  ai_notes, stage, stage_updated, synthetic,
                                  speculative_briefing_folder,
                                  created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, '', 'web_speculative', ?, 7, 'scored',
                       ?, 'scored', ?, 1, ?, ?, ?)""",
            (
                job_id,
                fp,
                url,
                title,
                company,
                card.description,
                ai_notes,
                now,
                row["briefing_folder"],
                now,
                now,
            ),
        )
        write_audit(conn, job_id, "stage", "", "scored")
        fingerprints.append(fp)

    conn.execute(
        """UPDATE speculative_requests
           SET status='approved', approved_at=?, approved_role_count=?
           WHERE id=?""",
        (now, len(kept_indices), request_id),
    )
    conn.commit()
    log_event(
        "speculative_request_approved",
        request_id=request_id,
        company=company,
        approved_count=len(kept_indices),
        fingerprints=fingerprints,
    )
    return fingerprints
