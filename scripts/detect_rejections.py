"""Cron entry point for rejection detection (#362).

Runs every 30 min via supercronic. Per-stack stagger via
``FINDAJOB_DETECT_REJECTIONS_SCHEDULE`` in stack ``.env``.

Flow:
    1. ``load_config()`` — early exit if Gmail unconfigured (no-op).
    2. First-run backlog sweep when ``state.rejection_backlog_scan_complete``
       is False; otherwise incremental UID-checkpointed scan.
    3. ``classify_email()`` per message; ``match_job()`` against
       ``applied/interview/offer`` rows.
    4. Spec §4.8 corroborated path: when classifier flags a confident
       rejection but the matcher returns ``unmatched``, secondary lookup
       against ``not_selected``/``rejected`` rows. Hits log
       ``rejection_email_corroborated`` and skip the review queue.
    5. Persist new suggestions to ``rejection_suggestions``; one ntfy
       per cycle (§4.6 throttle).
    6. Advance ``state.rejection_last_uid`` on every successful run so
       steady-state cycles are incremental.

Spec: ``docs/superpowers/specs/2026-05-01-362-rejection-detection-design.md``
§4.1, §4.6, §4.7, §4.8.
"""

from __future__ import annotations

import argparse
import dataclasses
import re
import sqlite3
import sys
from typing import Any

from rapidfuzz import fuzz

from findajob.audit import cron_event_span, log_event
from findajob.db import connect
from findajob.gmail_imap import (
    TestResult,
    fetch_new_messages_for_rejection_scan,
    load_config,
    load_state,
    save_state,
)
from findajob.notifications import ntfy
from findajob.paths import BASE
from findajob.rejection_detector import classify_email, match_job

DB_PATH = f"{BASE}/data/pipeline.db"

# Spec §4.7: configurable, default 30 days, capped at 60. ``GmailState``
# ships ``rejection_backlog_window_days=0`` as the unset sentinel; falling
# back to this constant rather than bumping the dataclass default keeps
# already-deployed stacks from silently re-triggering backlog scans.
_DEFAULT_BACKLOG_WINDOW_DAYS = 30
_MAX_BACKLOG_WINDOW_DAYS = 60

# Mirrors ``rejection_detector.matcher._company_match`` (token_set_ratio >= 80).
# Pinned here rather than imported because corroboration is a private path
# in the orchestrator — the matcher's threshold is the design anchor, not
# a runtime dependency.
_CORROBORATION_FUZZ_THRESHOLD = 80

# Minimum length of a handled-stage ``jobs.company`` value to consider in the
# subject/body fallback (#586). Avoids "Co" / "IBM" / "HP" false positives
# where a 2- or 3-letter company name would match coincidentally in body
# text. The tradeoff is accepting false-negative corroboration on
# short-named companies (suggestion gets surfaced, operator dismisses) —
# preferable to false-positive corroboration (suggestion suppressed when
# it shouldn't be).
_CORROBORATION_MIN_COMPANY_LEN = 4


def main(since_days: int | None = None) -> int:
    """Run one detect-rejections cycle.

    ``since_days`` (None by default — supplied via the ``--since-days N`` CLI
    flag from the ``__main__`` block) triggers a one-shot historical rescan
    that bypasses the UID checkpoint and date-windows the IMAP search to the
    prior ``N`` days. Use case: an operator adds a sender to the allowlist
    after a rejection email has already been delivered, and needs the
    historical message resurfaced (#804). The flag is bounded by
    ``_MAX_BACKLOG_WINDOW_DAYS`` to keep IMAP search cost predictable.

    One-shot mode does NOT mutate ``rejection_backlog_scan_complete`` — that
    sentinel governs the first-run automatic backlog scan, which is a
    separate cycle the operator has presumably already crossed. The
    ``rejection_last_uid`` checkpoint is governed by the existing
    ``new_uid > current`` guard, so historical UIDs won't roll the
    checkpoint backward.
    """
    with cron_event_span("detect-rejections"):
        config = load_config()
        if config is None:
            log_event("rejection_scan_skipped", reason="gmail_unconfigured")
            return 0

        state = load_state()
        is_backlog_run = not state.rejection_backlog_scan_complete
        is_oneshot_rescan = since_days is not None

        if since_days is not None:
            window = min(since_days, _MAX_BACKLOG_WINDOW_DAYS)
            log_event("rejection_oneshot_rescan_started", days=window)
            outcome = fetch_new_messages_for_rejection_scan(config, state, since_days=window)
        elif is_backlog_run:
            window = state.rejection_backlog_window_days or _DEFAULT_BACKLOG_WINDOW_DAYS
            window = min(window, _MAX_BACKLOG_WINDOW_DAYS)
            log_event("rejection_backlog_scan_started", days=window)
            outcome = fetch_new_messages_for_rejection_scan(config, state, since_days=window)
        else:
            window = None
            outcome = fetch_new_messages_for_rejection_scan(config, state)

        if outcome.result is not TestResult.SUCCESS:
            log_event("rejection_scan_failed", reason=outcome.result.value)
            return 0

        suggestions_created = 0
        corroborated = 0

        conn = connect(DB_PATH, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            for _sender, raw in outcome.messages:
                suggestion = classify_email(raw)
                if suggestion is None:
                    continue

                match = match_job(
                    conn,
                    suggestion.extracted_company,
                    suggestion.extracted_role,
                    suggestion.received_at,
                )

                if match.status == "unmatched":
                    handled_id = _find_corroborating_handled_job(conn, suggestion.extracted_company)
                    if handled_id is None:
                        # Fallback (#586): extraction may have misfired entirely OR
                        # captured a wrong token (e.g. a role string instead of the
                        # company). The email's subject + body excerpt may still
                        # mention a handled-stage company verbatim — scan for it
                        # so already-resolved rejections don't leak through to the
                        # review queue. Independent of `extracted_company`, so
                        # robust to extraction-shape regressions we haven't seen.
                        handled_id = _find_corroborating_handled_job_fallback(conn, suggestion)
                    if handled_id is not None:
                        log_event(
                            "rejection_email_corroborated",
                            gmail_message_id=suggestion.gmail_message_id,
                            matched_job_id=handled_id,
                            confidence=suggestion.confidence,
                            sender_domain=suggestion.sender.split("@", 1)[-1] if "@" in suggestion.sender else "",
                        )
                        corroborated += 1
                        continue

                if _persist_suggestion(conn, suggestion, match):
                    suggestions_created += 1
        finally:
            conn.close()

        state_changed = False
        if outcome.new_uid is not None and outcome.new_uid > state.rejection_last_uid:
            state = dataclasses.replace(state, rejection_last_uid=outcome.new_uid)
            state_changed = True
        if is_backlog_run and not state.rejection_backlog_scan_complete:
            state = dataclasses.replace(state, rejection_backlog_scan_complete=True)
            state_changed = True
        if state_changed:
            save_state(state)

        if suggestions_created > 0:
            if is_oneshot_rescan:
                ntfy.send(
                    title="Rejection rescan complete",
                    body=(
                        f"One-shot rescan — {suggestions_created} rejection(s) detected "
                        f"over prior {window} days. Review queue updated."
                    ),
                    cta_url="/board/rejections-review/",
                    kind="rejection_detected",
                )
            elif is_backlog_run:
                ntfy.send(
                    title="Rejection backlog scan complete",
                    body=(
                        f"First-run backlog scan — {suggestions_created} rejection(s) detected "
                        f"over prior {window} days. Review queue ready."
                    ),
                    cta_url="/board/rejections-review/",
                    kind="rejection_detected",
                )
            else:
                ntfy.send(
                    title="New rejection email(s) detected",
                    body=f"{suggestions_created} new rejection(s) — review queue updated.",
                    cta_url="/board/rejections-review/",
                    kind="rejection_detected",
                )

        log_event(
            "rejection_scan_completed",
            scanned=len(outcome.messages),
            suggestions_created=suggestions_created,
            corroborated=corroborated,
            is_backlog_run=is_backlog_run,
            is_oneshot_rescan=is_oneshot_rescan,
        )
        return 0


def _find_corroborating_handled_job_fallback(conn: sqlite3.Connection, suggestion: Any) -> str | None:
    """Subject/body fallback for §4.8 corroboration when extraction misfired.

    The primary path (``_find_corroborating_handled_job``) uses
    ``suggestion.extracted_company`` as the query key. When the classifier's
    extraction returns None or a wrong token (e.g. a role substring like
    ``"the Program Manager"`` from a Zoox-shape body), the primary lookup
    silently fails and the suggestion lands in the review queue even if
    the underlying rejection is already represented at ``not_selected`` or
    ``rejected`` somewhere in the DB.

    This fallback closes that gap: it scans the email's subject + body
    excerpt for any handled-stage ``jobs.company`` value, using
    word-boundary matching so a 4-char company doesn't false-positive
    against longer words (e.g. ``acme`` in ``acmestaff``). Bounded by the
    number of distinct companies the operator has applied to (typically
    well under 500 rows), so the per-cycle cost is negligible.

    Decoupled from extraction by design — robust to future extraction-
    shape regressions we haven't anticipated. Length floor
    ``_CORROBORATION_MIN_COMPANY_LEN`` accepts false-negative corroboration
    on short-named companies in exchange for zero false-positive
    corroboration that would suppress a real signal.
    """
    haystack = (suggestion.subject + " " + suggestion.body_excerpt).lower()
    rows = conn.execute(
        """
        SELECT id, company FROM jobs
        WHERE stage IN ('not_selected', 'rejected')
          AND synthetic = 0
          AND company IS NOT NULL
          AND company != ''
        """
    ).fetchall()
    for row in rows:
        company = (row["company"] or "").strip().lower()
        if len(company) < _CORROBORATION_MIN_COMPANY_LEN:
            continue
        if re.search(r"\b" + re.escape(company) + r"\b", haystack):
            return row["id"]
    return None


def _find_corroborating_handled_job(conn: sqlite3.Connection, extracted_company: str | None) -> str | None:
    """Find an already-handled (not_selected/rejected) job matching the email's company.

    Coarse company match using rapidfuzz at the same threshold the matcher's
    private ``_company_match`` uses. No alias-config lookup here —
    corroboration is review-queue noise suppression, not the canonical
    match path. False negatives only manifest as one extra ``unmatched``
    suggestion the operator dismisses.
    """
    if not extracted_company:
        return None
    rows = conn.execute(
        """
        SELECT id, company FROM jobs
        WHERE stage IN ('not_selected', 'rejected') AND synthetic = 0
        """
    ).fetchall()
    extracted_lower = extracted_company.lower()
    for row in rows:
        company = row["company"] or ""
        if not company:
            continue
        if fuzz.token_set_ratio(company.lower(), extracted_lower) >= _CORROBORATION_FUZZ_THRESHOLD:
            return row["id"]
    return None


def _persist_suggestion(conn: sqlite3.Connection, suggestion: Any, match: Any) -> bool:
    """Insert one rejection suggestion. Returns True on insert, False on dedup hit.

    ``gmail_message_id`` UNIQUE constraint makes ``INSERT OR IGNORE`` the
    durable dedup gate — a re-run after a crash will not double-suggest.
    """
    cur = conn.execute(
        """
        INSERT OR IGNORE INTO rejection_suggestions (
            gmail_message_id, received_at, sender, subject, body_excerpt,
            extracted_company, extracted_role, matched_job_id, match_status,
            confidence, suggested_reason
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            suggestion.gmail_message_id,
            suggestion.received_at,
            suggestion.sender,
            suggestion.subject,
            suggestion.body_excerpt,
            suggestion.extracted_company,
            suggestion.extracted_role,
            match.job_id,
            match.status,
            suggestion.confidence,
            suggestion.suggested_reason,
        ),
    )
    conn.commit()
    return cur.rowcount > 0


def _parse_argv(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--since-days",
        type=int,
        default=None,
        metavar="N",
        help=(
            "One-shot historical rescan: bypass the UID checkpoint and "
            "date-window the IMAP search to the prior N days. Used after "
            "adding a sender to the allowlist to resurface emails that "
            f"arrived before the addition. Capped at {_MAX_BACKLOG_WINDOW_DAYS}."
        ),
    )
    args = parser.parse_args(argv)
    if args.since_days is not None and args.since_days < 1:
        parser.error("--since-days must be >= 1")
    return args


if __name__ == "__main__":
    args = _parse_argv()
    sys.exit(main(since_days=args.since_days))
