"""Prior-application history per company — powers the Dashboard + Waitlist
history cell added in #234.

A row on /board/dashboard or /board/waitlist shows, alongside the job, the
operator's prior dealings with the same company: how many applications are
currently pending, and how many have been marked not_selected by the company.

Company matching uses the first normalized word (`normalize(company).split()[0]`)
so "Meta" and "Meta Platforms" collapse (AC5). Operator-side `rejected` jobs
are excluded (AC7 — noise for this decision, not signal). Not_selected within
90 days flags yellow; an offer anywhere at the company flags green (AC3).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from findajob.cleaning import normalize

# Stages counted as "pending" per AC1 — strictly post-application, excluding
# materials_drafted/prep_in_progress which are pre-submission.
_PENDING_STAGES = ("applied", "interview", "offer")

_NOT_SELECTED_RECENT_DAYS = 90


def _company_key(company: str | None) -> str:
    """First normalized word — the loose-match key for company history.

    Collapses "Meta" and "Meta Platforms", "Google" and "Google Cloud", etc.
    Returns an empty string for blank input.
    """
    if not company:
        return ""
    words = normalize(company).split()
    return words[0] if words else ""


@dataclass(frozen=True)
class _HistoryEntry:
    id: str
    fingerprint: str
    title: str
    company: str
    stage: str
    prep_folder_path: str | None
    not_selected_at: str | None  # naive-space timestamp from audit_log, or None


def _parse_audit_timestamp(raw: str | None) -> datetime | None:
    """audit_log.changed_at is naive space-format ('YYYY-MM-DD HH:MM:SS')."""
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
    except ValueError:
        return None


def fetch_company_history(db: sqlite3.Connection) -> dict[str, dict]:
    """Return {company_key: {entries: [...], has_offer: bool, has_recent_not_selected: bool}}.

    One SQL scan of the jobs table, grouped in Python by first-normalized-word
    of `company`. Each entry carries enough data for the row-level annotator
    to build counts, color flags, and the expand-detail listing.
    """
    sql = """
    SELECT j.id, j.fingerprint, j.title, j.company, j.stage, j.prep_folder_path,
           al.changed_at AS not_selected_at
    FROM jobs j
    LEFT JOIN (
      SELECT job_id, MAX(changed_at) AS changed_at
      FROM audit_log
      WHERE field_changed = 'stage' AND new_value = 'not_selected'
      GROUP BY job_id
    ) al ON al.job_id = j.id
    WHERE j.stage IN ('applied','interview','offer','not_selected')
    """
    rows = db.execute(sql).fetchall()

    cutoff = datetime.now(UTC) - timedelta(days=_NOT_SELECTED_RECENT_DAYS)
    buckets: dict[str, dict] = {}
    for row in rows:
        key = _company_key(row["company"])
        if not key:
            continue
        bucket = buckets.setdefault(key, {"entries": [], "has_offer": False, "has_recent_not_selected": False})
        entry = _HistoryEntry(
            id=row["id"],
            fingerprint=row["fingerprint"],
            title=row["title"],
            company=row["company"],
            stage=row["stage"],
            prep_folder_path=row["prep_folder_path"] if "prep_folder_path" in row.keys() else None,
            not_selected_at=row["not_selected_at"],
        )
        bucket["entries"].append(entry)
        if entry.stage == "offer":
            bucket["has_offer"] = True
        if entry.stage == "not_selected":
            ts = _parse_audit_timestamp(entry.not_selected_at)
            if ts is not None and ts >= cutoff:
                bucket["has_recent_not_selected"] = True
    return buckets


def history_for_row(
    row: sqlite3.Row,
    company_history: dict[str, dict],
) -> dict:
    """Per-row history annotation — counts, flag color, and expand-detail list.

    Excludes the row's own fingerprint from its own history (no self-count).
    The returned dict is template-ready:
        {
          "pending_count": int,
          "not_selected_count": int,
          "flag": "green" | "yellow" | "",
          "entries": [ {title, stage, fingerprint, prep_folder_path, not_selected_at}, ... ],
        }
    """
    key = _company_key(row["company"])
    bucket = company_history.get(key)
    if not bucket:
        return {"pending_count": 0, "not_selected_count": 0, "flag": "", "entries": []}

    entries: Sequence[_HistoryEntry] = bucket["entries"]
    own_fp = row["fingerprint"]
    pending = [e for e in entries if e.fingerprint != own_fp and e.stage in _PENDING_STAGES]
    not_selected = [e for e in entries if e.fingerprint != own_fp and e.stage == "not_selected"]

    cutoff = datetime.now(UTC) - timedelta(days=_NOT_SELECTED_RECENT_DAYS)
    has_offer = any(e.stage == "offer" for e in pending)
    has_recent_ns = any(
        (_parse_audit_timestamp(e.not_selected_at) or datetime.min.replace(tzinfo=UTC)) >= cutoff for e in not_selected
    )
    flag = "green" if has_offer else ("yellow" if has_recent_ns else "")

    return {
        "pending_count": len(pending),
        "not_selected_count": len(not_selected),
        "flag": flag,
        "entries": [
            {
                "title": e.title,
                "stage": e.stage,
                "fingerprint": e.fingerprint,
                "prep_folder_path": e.prep_folder_path,
                "not_selected_at": e.not_selected_at,
            }
            for e in (pending + not_selected)
        ],
    }


def build_history_by_fp(
    rows: Sequence[sqlite3.Row],
    company_history: dict[str, dict],
) -> dict[str, dict]:
    """Precompute per-row history annotations keyed by fingerprint so templates
    can look up with a single dict access instead of re-running the filter."""
    return {row["fingerprint"]: history_for_row(row, company_history) for row in rows}
