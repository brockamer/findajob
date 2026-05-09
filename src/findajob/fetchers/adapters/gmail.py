"""GmailLinkedInAdapter — migrates fetch_gmail_jobs to the JobSourceAdapter framework (#410.4).

The most idiosyncratic of the four #410 migrations: IMAP-backed (no env var,
no slug pattern), the only adapter where per-row `source` is set dynamically
(`gmail_linkedin` / `gmail_indeed` / `gmail_ziprecruiter` / `gmail_google` /
`gmail_unknown`) by `_normalize_sender_to_source`, and the only one with a
streak-counter side effect (3-strike auth-failure ntfy escalation).

The class-attr `source_label = "gmail"` is the *adapter-level* identifier
(used for admin UX and registry-membership invariants); per-row `source` is
set inside `fetch()`. This asymmetry is intentional and unique to Gmail —
do NOT generalize the Protocol to handle dynamic source labels until at
least one other adapter needs the same shape (#248 Workday and #249 Gem
both use static source labels).
"""

from __future__ import annotations

from typing import ClassVar

from findajob import gmail_imap
from findajob.audit import log_event

from .base import LiveTestResult, QueryResult


class GmailLinkedInAdapter:
    """IMAP-backed adapter for LinkedIn / Indeed / ZipRecruiter / Google
    job-alert digest emails.

    Off state: returns `[]` from `fetch()` and `auth` bucket from
    `live_test()` if `config/gmail.json` is missing or unparseable. The
    dispatcher pattern in `_normalize_sender_to_source` handles the
    per-row source label.
    """

    name: ClassVar[str] = "gmail"
    display_name: ClassVar[str] = "Gmail (job-alert digests via IMAP)"
    source_label: ClassVar[str] = "gmail"  # adapter-level; per-row source set in fetch()
    required_env_vars: ClassVar[tuple[str, ...]] = ()  # IMAP config in config/gmail.json, not env

    def __init__(self, since_days: int | None = None) -> None:
        """`since_days` triggers a SINCE-N-days IMAP search instead of the
        normal incremental UID fetch — for diagnostic/backfill runs only.
        Exposed via constructor (not the Protocol's `fetch(queries)` signature)
        to keep the Protocol uniform across adapters; #410.5 orchestrator
        will pass it through."""
        self._since_days = since_days

    def is_configured(self) -> bool:
        return gmail_imap.load_config() is not None

    def fetch(self, queries: list[str]) -> list[dict]:
        del queries  # Gmail is push-driven via IMAP, not query-driven
        # Lazy import to avoid loading the rest of fetchers/__init__.py at
        # adapter-module import time — and so monkeypatch on
        # `findajob.fetchers.notify_send_raw` resolves at call time.
        import email as email_lib
        from dataclasses import replace
        from datetime import UTC, datetime

        from findajob import fetchers as _fmod

        config = gmail_imap.load_config()
        if config is None:
            log_event("gmail_skipped", reason="not_configured")
            return []

        state = gmail_imap.load_state()
        outcome = gmail_imap.fetch_new_messages(config, state, since_days=self._since_days)

        if outcome.result == gmail_imap.TestResult.AUTH_FAILED:
            new_streak = state.auth_failure_streak + 1
            gmail_imap.save_state(replace(state, auth_failure_streak=new_streak, last_error="auth_failed"))
            log_event("gmail_auth_failed", streak=new_streak)
            # Exact-equality intent: notify only on the 2→3 transition. Test
            # `test_fetch_does_not_refire_ntfy_after_streak_passes_three` locks
            # this in — do not change `==` to `>=` without re-thinking the
            # operator-experience contract.
            if new_streak == 3:
                try:
                    _fmod.notify_send_raw("🔐 Gmail login failed — refresh app password at /config/gmail/")
                except Exception as e:
                    log_event("gmail_ntfy_send_failed", error=str(e))
            return []

        if outcome.result == gmail_imap.TestResult.CONNECTION_ERROR:
            log_event("gmail_connection_error")
            return []

        # SUCCESS — fetch_new_messages always populates new_uid/new_uidvalidity
        # on success (gmail_imap.py:306-310); narrow for mypy.
        assert outcome.new_uid is not None and outcome.new_uidvalidity is not None
        now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        gmail_imap.save_state(
            replace(
                state,
                last_uid=outcome.new_uid,
                last_uidvalidity=outcome.new_uidvalidity,
                auth_failure_streak=0,
                last_fetched_at=now,
                last_login_at=now,
                last_error=None,
            )
        )

        sender_counts: dict[str, int] = {}
        for sender, _ in outcome.messages:
            sender_counts[sender] = sender_counts.get(sender, 0) + 1
        log_event("gmail_messages_found", count=len(outcome.messages), by_sender=sender_counts)

        jobs: list[dict] = []
        for sender, raw_bytes in outcome.messages:
            try:
                msg = email_lib.message_from_bytes(raw_bytes)
                for job in _fmod.parse_jobs_from_email_imap(msg):
                    job["source"] = _fmod._normalize_sender_to_source(sender, job.get("url", ""))
                    jobs.append(job)
            except Exception as e:
                log_event("gmail_parse_error", error=str(e))
        return jobs

    def live_test(self, queries: list[str]) -> LiveTestResult:
        del queries
        config = gmail_imap.load_config()
        if config is None:
            return LiveTestResult(
                ok=False,
                bucket="auth",
                per_query=[],
                auth_error="Gmail not configured. Visit /config/gmail/ to add your address and app password.",
            )
        result = gmail_imap.test_login(config)
        per_query = [QueryResult(query="gmail_imap_login", count=0)]
        if result == gmail_imap.TestResult.SUCCESS:
            return LiveTestResult(ok=True, bucket="success", per_query=per_query, auth_error=None)
        if result == gmail_imap.TestResult.CONNECTION_ERROR:
            return LiveTestResult(
                ok=False,
                bucket="network",
                per_query=per_query,
                auth_error="IMAP connection failed — check network or imap.gmail.com reachability.",
            )
        # AUTH_FAILED + INVALID_CONFIG both route to `auth` — same UX class
        # (user must fix something in /config/gmail/). Mapping INVALID_CONFIG
        # explicitly so a future TestResult value doesn't silently fall
        # through to a wrong bucket.
        return LiveTestResult(
            ok=False,
            bucket="auth",
            per_query=per_query,
            auth_error=f"Gmail login failed ({result.value}). Refresh app password at /config/gmail/.",
        )
