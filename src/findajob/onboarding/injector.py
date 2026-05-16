"""Onboarding injector (#148).

Turns a parsed emission into seven files on disk, with backup-then-overwrite
and a sentinel file that gates the NUX redirect. The Tier 1 company list
is no longer derived as a separate file (#211 retired
``companies_of_interest.txt``); `findajob.config_loader` reads
`target_companies.md` directly.

All writes are atomic: every tempfile is staged first, then
``os.replace`` commits them in order. Any staging failure rolls back
cleanly — zero mutations to existing files, no partial backup residue.

Pure module: imports ``os``, ``re``, ``shutil``, ``tempfile``,
``datetime``, ``pathlib``. No FastAPI import.
"""

from __future__ import annotations

import os
import re
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import NamedTuple

from findajob.audit import log_event

# Imported lazily inside inject() to avoid a circular import on the
# discoverer side, and to keep this module importable even when the
# discoverer package isn't yet on the path during unit tests of unrelated
# subsystems.
from findajob.onboarding.openrouter_smoke import (
    OnboardingSmokeCheckFailed,
    verify_openrouter_key,
)
from findajob.onboarding.parser import ALLOWED_FILENAMES
from findajob.onboarding.voice_processor import process_voice_samples

# Maps emission filename -> destination relative path (relative to base_root).
# Plain-file destinations: emission body is written verbatim to this file.
# (The three single-line additions — display_name.txt and timezone.txt — also
# live here because they're plain-file writes; ntfy_topic.txt is special-cased
# below because it merges into data/.env rather than overwriting a whole file.)
_ALL_DESTINATIONS: dict[str, str] = {
    "profile.md": "candidate_context/profile.md",
    "master_resume.md": "candidate_context/master_resume.md",
    "target_companies.md": "config/target_companies.md",
    "business_sector_employers_reference.md": "config/business_sector_employers_reference.md",
    "prefilter_rules.yaml": "config/prefilter_rules.yaml",
    "in_domain_patterns.yaml": "config/in_domain_patterns.yaml",
    "reject_reasons.yaml": "config/reject_reasons.yaml",
    "display_name.txt": "candidate_context/display_name.txt",
    "timezone.txt": "data/timezone",
}

# Filenames whose body is parsed and merged into data/.env rather than written
# as a whole file. Body shape per filename is documented inline.
_ENV_MERGE_FILENAMES: tuple[str, ...] = ("ntfy_topic.txt",)
_ENV_FILE_RELPATH = "data/.env"
_ENV_EXAMPLE_RELPATH = "data/.env.example"

# Optional emission filenames -> destination relative path. Processed if
# present in the emission, silently skipped if absent. Backed up the same as
# required destinations.
_OPTIONAL_DESTINATIONS: dict[str, str] = {
    "voice-samples.md": "candidate_context/voice_samples/voice-samples.md",
    "jsearch_queries.txt": "config/jsearch_queries.txt",
    "feed-urls.txt": "config/feed_urls.txt",
    "linkedin-alerts.md": "candidate_context/linkedin-alerts.md",
    "target_locations.txt": "config/target_locations.txt",
}
# Note: rapidapi_feed.txt is consumed by _derive_active_sources (#680) and
# is NOT written to disk on its own — its body contributes one entry to
# the derived config/active_sources.txt. The previous 1:1 map shipped only
# the RapidAPI adapter to active_sources.txt and dropped the company-feed
# and Gmail branches; the derivation fixes that.

_SENTINEL_RELPATH = "data/.onboarding-complete"
_BACKUP_ROOT = ".backups"


class DiscoveryStatus(NamedTuple):
    """Lightweight mirror of findajob.discoverer.RunResult for return.

    Kept module-local so callers don't have to import the discoverer
    package to inspect onboarding results.
    """

    success: bool
    count: int  # type: ignore[assignment]  # NamedTuple field shadows tuple.count method
    error: str | None


@dataclass(frozen=True)
class InjectionDecision:
    """Gate decision produced by :func:`inject`.

    The sentinel is **never** written by :func:`inject` itself — every onboarding
    flow now ends at the connections gate (``/onboarding/connections/{sid}/``,
    #571), which writes the sentinel after the user either uploads a LinkedIn
    Connections.csv or explicitly skips. The Gmail-config gate (#407) sits just
    upstream and preserves the "IMAP connection test before handoff" guarantee
    — its ``/finish`` endpoint still blocks until a successful IMAP test, but
    no longer writes the sentinel itself.

    When ``gate_to_feed_config`` is True the caller redirects the user to
    ``/onboarding/feed-config/{sid}/`` first; that route's ``/finish`` redirects
    onward to the Gmail gate. ``pending_adapter`` is the first adapter name
    whose env var is absent.

    When ``gate_to_feed_config`` is False the caller redirects directly to
    ``/onboarding/gmail-config/{sid}/``.
    """

    gate_to_feed_config: bool
    pending_adapter: str | None  # adapter name to configure, if gating


class InjectResult(NamedTuple):
    backup_dir: Path
    discovery: DiscoveryStatus
    decision: InjectionDecision = InjectionDecision(gate_to_feed_config=False, pending_adapter=None)
    voice_samples_redact_failed: bool = False


def is_complete(base_root: Path) -> bool:
    """True iff the sentinel file exists under ``base_root``."""
    return (base_root / _SENTINEL_RELPATH).is_file()


def mark_complete(base_root: Path) -> None:
    """Write the sentinel file with the current UTC timestamp."""
    sentinel = base_root / _SENTINEL_RELPATH
    sentinel.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    sentinel.write_text(ts + "\n", encoding="utf-8")


def _emission_consistency_warnings(base_root: Path, found: dict[str, str]) -> None:
    """Log non-blocking warnings to pipeline.jsonl for emission inconsistencies.

    Triggers:
      - linkedin-alerts.md emitted but jsearch_queries.txt absent → broken
        cross-reference in the alerts checklist.
      - jsearch_queries.txt emitted but contains zero non-comment, non-blank
        lines → signals prompt-LLM drift (the prompt should not have emitted
        an empty queries file).

    Caller MUST treat any exception from this helper as soft-fail — onboarding
    has already committed at this point and the sentinel is set.
    """
    if "linkedin-alerts.md" in found and "jsearch_queries.txt" not in found:
        log_event(
            "onboarding_emission_anomaly",
            kind="linkedin_alerts_without_jsearch_queries",
            base_root=str(base_root),
        )

    if "jsearch_queries.txt" in found:
        body = found["jsearch_queries.txt"]
        non_comment_lines = [line for line in body.splitlines() if line.strip() and not line.strip().startswith("#")]
        if not non_comment_lines:
            log_event(
                "onboarding_emission_anomaly",
                kind="jsearch_queries_empty",
                base_root=str(base_root),
            )


_DERIVED_ACTIVE_SOURCES_RELPATH = "config/active_sources.txt"


# Onboarding-time mapping from 3g answer-files (interview source-selection) to
# adapter registry names. The 3g question lets the candidate pick a/b/c across
# {RapidAPI, company feeds, Gmail alerts}; each branch emits its own file. The
# interview itself does not write active_sources.txt — this helper derives it
# from what the candidate actually selected. See #680.
def _derive_active_sources(found: dict[str, str]) -> list[str]:
    """Return the ordered adapter list to write to ``config/active_sources.txt``.

    Empty list = no file should be written. Each branch contributes only when
    its emission body has non-blank, non-comment content; an emitted-but-empty
    file is treated as "not selected" (matches the user's lived experience of
    skipping that branch in the interview).
    """
    sources: list[str] = []

    rapidapi_body = found.get("rapidapi_feed.txt", "")
    rapidapi_name = next(
        (line.strip() for line in rapidapi_body.splitlines() if line.strip() and not line.strip().startswith("#")),
        "",
    )
    if rapidapi_name:
        sources.append(rapidapi_name)

    feed_urls_body = found.get("feed-urls.txt", "")
    feed_urls_real_lines = [
        line for line in feed_urls_body.splitlines() if line.strip() and not line.strip().startswith("#")
    ]
    if feed_urls_real_lines:
        sources.extend(["greenhouse", "ashby", "lever"])

    linkedin_alerts_body = found.get("linkedin-alerts.md", "")
    if linkedin_alerts_body.strip():
        # Registry adapter name is "gmail" (see GmailLinkedInAdapter.name); the
        # "gmail_linkedin" string is a per-row source tag, not an adapter key.
        sources.append("gmail")

    return sources


def _utc_stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _backup_relpaths() -> list[str]:
    paths = list(_ALL_DESTINATIONS.values())
    paths.extend(_OPTIONAL_DESTINATIONS.values())
    paths.append(_DERIVED_ACTIVE_SOURCES_RELPATH)  # derived from rapidapi_feed/feed-urls/linkedin-alerts (#680)
    paths.append(_SENTINEL_RELPATH)
    paths.append(_ENV_FILE_RELPATH)  # data/.env is mutated via merge — must back up
    return paths


def merge_env_content(existing: str, example: str, updates: dict[str, str]) -> str:
    """Compute the new ``data/.env`` content after merging ``updates``.

    Strategy:
      - If ``existing`` is empty, start from ``example`` (the .env.example
        template baked into the repo). Otherwise start from ``existing``.
      - Walk lines preserving blank lines, comments, and unrelated keys.
      - For each line matching ``KEY=...`` whose KEY is in ``updates``, replace
        with the new value. Keys that didn't appear get appended at the end.

    Pure function — does no I/O. Tests can verify exact line-level output.
    """
    base = existing if existing else example
    new_lines: list[str] = []
    handled: set[str] = set()
    for line in base.splitlines(keepends=True):
        stripped = line.lstrip()
        if not stripped.strip() or stripped.startswith("#"):
            new_lines.append(line)
            continue
        m = re.match(r"^([A-Z_][A-Z0-9_]*)\s*=", stripped)
        if m and m.group(1) in updates:
            key = m.group(1)
            new_lines.append(f"{key}={updates[key]}\n")
            handled.add(key)
        else:
            new_lines.append(line)
    # Append any keys that didn't already appear in the file
    for key, value in updates.items():
        if key not in handled:
            if new_lines and not new_lines[-1].endswith("\n"):
                new_lines.append("\n")
            new_lines.append(f"{key}={value}\n")
    return "".join(new_lines)


def _parse_ntfy_topic_body(body: str) -> str:
    """Parse the ``ntfy_topic.txt`` emission body into the bare topic string.

    Tolerates either of:
      - ``NTFY_TOPIC=judy-jobsearch-2026`` (key=value form)
      - ``judy-jobsearch-2026`` (bare value form)
    Returns the trimmed value. Empty result raises ValueError so onboarding
    fails loudly rather than writing an empty topic that silently misroutes.
    """
    text = body.strip()
    if not text:
        raise ValueError("ntfy_topic.txt is empty")
    # Strip a leading `NTFY_TOPIC=` if present
    m = re.match(r"^NTFY_TOPIC\s*=\s*(.+)$", text, re.IGNORECASE)
    if m:
        text = m.group(1).strip()
    # Strip optional surrounding quotes
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        text = text[1:-1]
    if not text:
        raise ValueError("ntfy_topic.txt yielded empty value after stripping prefix/quotes")
    return text


def backup_existing(base_root: Path, stamp: str) -> Path:
    """Copy any existing destinations to ``{base_root}/.backups/{stamp}/``.

    Returns the backup directory path. If no extant sources are present
    (first onboarding run on a fresh stack), no directory is created and
    the returned path will not exist on disk. Preserves the relative path
    structure of every copied file.

    Skipping the mkdir on first runs is defense-in-depth against #365: if
    the operator's host is missing the ``./state/.backups:/app/.backups``
    bind mount, an unconditional mkdir on a fresh stack would fail with
    EPERM (parent ``/app`` is root-owned in the image). Short-circuiting
    when there is nothing to back up means a fresh first run never
    touches that path — the bind mount only matters on re-runs, which is
    when there's something to back up anyway.
    """
    sources = [base_root / r for r in _backup_relpaths()]
    extant = [s for s in sources if s.is_file()]
    dest_root = base_root / _BACKUP_ROOT / stamp
    if not extant:
        return dest_root
    dest_root.mkdir(parents=True, exist_ok=True)
    for src in extant:
        relpath = src.relative_to(base_root)
        target = dest_root / relpath
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, target)
    return dest_root


def inject(
    base_root: Path,
    found: dict[str, str],
    *,
    openrouter_api_key: str = "",
    rapidapi_key: str = "",
    redact_voice_samples: bool = True,
    skip_smoke_check: bool = False,
    conn: sqlite3.Connection | None = None,
) -> InjectResult:
    """Backup, stage, commit, smoke-check, then run the discovery hook.

    ``found`` must contain every filename in :data:`ALLOWED_FILENAMES`;
    otherwise raises :class:`ValueError` without touching disk.

    ``openrouter_api_key`` is the user-supplied key collected from a separate
    form field on ``/onboarding/`` (kept out of the LLM-driven emission so it
    never enters the user's chat-LLM logs). It's merged into ``data/.env`` as
    ``OPENROUTER_API_KEY=...`` and verified with a 1-token completion against
    OpenRouter BEFORE the staged tempfiles are committed (#631 — transactional
    emit). An invalid or unreachable key raises :class:`OnboardingSmokeCheckFailed`
    with ``status_code`` reflecting the underlying HTTP failure (402 when the
    account is out of credit, 400 otherwise), and ALL staged tempfiles + the
    backup dir created this run are deleted before propagating. The user
    re-pastes / tops up credit and re-clicks Finalize — the second attempt
    runs from a clean state. Pass an empty string only in contexts where
    ``skip_smoke_check=True`` (tests; legacy callers).

    ``rapidapi_key`` is the optional RapidAPI key for LinkedIn/Indeed job
    search (``RAPIDAPI_KEY`` in ``data/.env``). When empty or whitespace, the
    key is not written to ``data/.env`` — the ``.env.example`` placeholder
    line is left in place. When provided, it is merged without any live smoke
    check (``findajob.fetchers`` performs its own truthiness-based skip logic).

    Optional filenames (currently ``voice-samples.md``) are processed if
    present and silently skipped if absent. When voice-samples.md is present,
    its body is run through ``process_voice_samples`` (clean + LLM-redact)
    before staging; ``redact_voice_samples=False`` skips the LLM step and
    writes only the structurally-cleaned text.

    ``skip_smoke_check=True`` skips the OpenRouter verification step. Tests
    use this to avoid network calls; production callers must NOT set this.

    ``conn`` is forwarded to ``process_voice_samples`` so a cost_log row is
    written when voice samples are LLM-redacted (#481). None disables
    cost-logging.

    On any staging or commit error, all tempfiles and the backup dir
    created this run are removed, and the exception propagates.
    """
    missing = [n for n in ALLOWED_FILENAMES if n not in found]
    if missing:
        raise ValueError(f"inject(): parsed emission is missing: {missing}")

    # Compute the merged data/.env content from collected updates.
    env_updates: dict[str, str] = {}
    if openrouter_api_key.strip():
        env_updates["OPENROUTER_API_KEY"] = openrouter_api_key.strip()
    if rapidapi_key.strip():
        env_updates["RAPIDAPI_KEY"] = rapidapi_key.strip()
    if "ntfy_topic.txt" in found:
        env_updates["NTFY_TOPIC"] = _parse_ntfy_topic_body(found["ntfy_topic.txt"])

    env_path = base_root / _ENV_FILE_RELPATH
    env_example_path = base_root / _ENV_EXAMPLE_RELPATH
    existing_env = env_path.read_text(encoding="utf-8") if env_path.is_file() else ""
    example_env = env_example_path.read_text(encoding="utf-8") if env_example_path.is_file() else ""
    new_env_content = merge_env_content(existing_env, example_env, env_updates) if env_updates else None

    # Derive active_sources.txt content from the source-selection emission
    # files (#680). Empty list = user picked 'none' or skipped every source
    # branch; no file should be written.
    derived_active_sources = _derive_active_sources(found)

    # Ensure target directories exist (required + any optional that was provided)
    parent_relpaths: list[str] = list(_ALL_DESTINATIONS.values())
    for opt_name, opt_relpath in _OPTIONAL_DESTINATIONS.items():
        if opt_name in found:
            parent_relpaths.append(opt_relpath)
    if derived_active_sources:
        parent_relpaths.append(_DERIVED_ACTIVE_SOURCES_RELPATH)
    if new_env_content is not None:
        parent_relpaths.append(_ENV_FILE_RELPATH)
    for relpath in parent_relpaths:
        (base_root / relpath).parent.mkdir(parents=True, exist_ok=True)
    (base_root / _SENTINEL_RELPATH).parent.mkdir(parents=True, exist_ok=True)

    stamp = _utc_stamp()
    backup_dir = backup_existing(base_root, stamp)

    decision: InjectionDecision = InjectionDecision(gate_to_feed_config=False, pending_adapter=None)
    voice_samples_redact_failed = False
    tempfiles: list[tuple[str, Path]] = []  # (tmp_name, final_dest)
    env_tmp_name: str | None = None
    try:
        # Stage every required parsed file (whole-file destinations only —
        # env-merge filenames are handled separately below).
        for name in ALLOWED_FILENAMES:
            if name in _ENV_MERGE_FILENAMES:
                continue
            dest = base_root / _ALL_DESTINATIONS[name]
            fd, tmp_name = tempfile.mkstemp(prefix=dest.name + ".", suffix=".tmp", dir=str(dest.parent))
            tempfiles.append((tmp_name, dest))  # register immediately so rollback sees it
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
                fh.write(found[name])

        # Stage optional files. voice-samples.md goes through process_voice_samples
        # (clean + LLM-redact); the others (jsearch_queries.txt, feed-urls.txt,
        # linkedin-alerts.md) are plain-write.
        for opt_name, opt_relpath in _OPTIONAL_DESTINATIONS.items():
            if opt_name not in found:
                continue
            body = found[opt_name]
            if opt_name == "voice-samples.md":
                processed, redaction_ok = process_voice_samples(body, redact=redact_voice_samples, conn=conn)
                if not redaction_ok:
                    # LLM redaction failed (#634): the structurally-cleaned body still
                    # contains every PII string the user pasted. Skip the write entirely
                    # so unredacted content never lands on disk; surface the flag so the
                    # route can warn the user to retry after the LLM outage clears.
                    log_event("onboarding_voice_samples_dropped", base_root=str(base_root))
                    voice_samples_redact_failed = True
                    continue
                if not processed:
                    continue  # voice-samples processing returned empty → skip write
                body = processed
            dest = base_root / opt_relpath
            fd, tmp_name = tempfile.mkstemp(prefix=dest.name + ".", suffix=".tmp", dir=str(dest.parent))
            tempfiles.append((tmp_name, dest))  # register immediately so rollback sees it
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
                fh.write(body)

        # Stage the derived config/active_sources.txt (#680). One line per
        # adapter name, trailing newline. Skipped entirely when the user
        # picked 'none' / skipped every source branch — absence is the
        # signal that /settings/active-sources/ uses to show its banner.
        if derived_active_sources:
            active_dest = base_root / _DERIVED_ACTIVE_SOURCES_RELPATH
            fd, active_tmp_name = tempfile.mkstemp(
                prefix=active_dest.name + ".", suffix=".tmp", dir=str(active_dest.parent)
            )
            tempfiles.append((active_tmp_name, active_dest))
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
                fh.write("\n".join(derived_active_sources) + "\n")

        # Stage the merged data/.env if there are any env updates
        if new_env_content is not None:
            fd, env_tmp_name = tempfile.mkstemp(prefix=env_path.name + ".", suffix=".tmp", dir=str(env_path.parent))
            tempfiles.append((env_tmp_name, env_path))  # register immediately so rollback sees it
            os.chmod(env_tmp_name, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
                fh.write(new_env_content)

        # Smoke-check the OpenRouter key BEFORE committing any tempfile (#631).
        # All emission tempfiles are staged at this point; an os.replace loop
        # below commits them atomically. If the smoke check fails — most
        # commonly 402 PaymentRequired when the user runs out of credit
        # mid-onboarding — the except handler tears down every tempfile + the
        # backup dir so a fresh re-click writes from a clean state.
        if not skip_smoke_check and openrouter_api_key.strip():
            ok, err = verify_openrouter_key(openrouter_api_key)
            if not ok:
                # Detect 402 PaymentRequired from the verify message so the
                # route can surface HTTP 402 to the user (vs the catch-all 400
                # we reserve for 401/429/network).
                status = 402 if err and "402 Payment Required" in err else 400
                raise OnboardingSmokeCheckFailed(
                    err or "OpenRouter verification failed.",
                    status_code=status,
                )

        # Commit: os.replace every staged tempfile into place
        for tmp_name, dest in tempfiles:
            os.replace(tmp_name, dest)
        tempfiles = []  # all committed

        # Decide whether to gate to feed-config first. The sentinel is always
        # deferred to the Gmail-config gate's /finish endpoint (#407) — inject()
        # never writes it directly.  Delete any pre-existing sentinel so re-runs
        # are enforcing, not advisory; without this a re-run user could navigate
        # directly to /board/ and bypass the gates.
        sentinel_path = base_root / _SENTINEL_RELPATH
        if sentinel_path.exists():
            sentinel_path.unlink()

        active_path = base_root / "config" / "active_sources.txt"
        if not active_path.exists():
            # No picker emission → straight to Gmail-config gate.
            decision = InjectionDecision(gate_to_feed_config=False, pending_adapter=None)
        else:
            from findajob.fetchers.adapters.registry import REGISTERED_ADAPTERS  # noqa: PLC0415

            active_names = [
                n.strip() for n in active_path.read_text().splitlines() if n.strip() and not n.startswith("#")
            ]
            classes_by_name = {cls.name: cls for cls in REGISTERED_ADAPTERS}
            needs_gate = False
            pending: str | None = None
            for name in active_names:
                if name not in classes_by_name:
                    continue
                instance = classes_by_name[name]()
                # Only env-var-bearing adapters (RapidAPI-flavored) belong in
                # the feed-config gate — feed-config exists to onboard API
                # keys. Env-less adapters (greenhouse/ashby/lever public APIs,
                # gmail_linkedin via the downstream Gmail-config gate) check
                # their config differently and would otherwise mis-route here
                # after #680's multi-adapter derivation.
                if not instance.required_env_vars:
                    continue
                if not instance.is_configured():
                    needs_gate = True
                    pending = name
                    break

            if needs_gate:
                decision = InjectionDecision(gate_to_feed_config=True, pending_adapter=pending)
            else:
                decision = InjectionDecision(gate_to_feed_config=False, pending_adapter=None)
    except Exception:
        # Roll back: delete any remaining tempfiles + the backup dir created
        # this run. After #631 this branch also handles OnboardingSmokeCheckFailed
        # — the smoke check runs before commit, so the staged tempfiles must
        # be torn down on failure (transactional emit: AC#2 of #631).
        for tmp_name, _dest in tempfiles:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
        shutil.rmtree(backup_dir, ignore_errors=True)
        raise

    # Non-blocking emission-consistency warnings (#283). Soft-fail: any failure
    # here does NOT roll back the seven-file commit (sentinel is already written).
    # Placed before the discovery hook so warnings fire even if discoverer bombs.
    try:
        _emission_consistency_warnings(base_root, found)
    except Exception:  # noqa: BLE001 — warnings must never fail onboarding
        pass

    # Post-commit discovery hook. Soft-fail: any failure here does NOT
    # roll back the seven-file commit (sentinel is already written).
    try:
        from findajob.discoverer import run as run_discovery  # noqa: PLC0415

        discovery_result = run_discovery(base_root, ntfy_enabled=False)
        discovery = DiscoveryStatus(
            success=discovery_result.success,
            count=discovery_result.count,
            error=discovery_result.error,
        )
    except Exception as e:  # noqa: BLE001 — discovery must never crash onboarding
        discovery = DiscoveryStatus(success=False, count=0, error=str(e))
    return InjectResult(
        backup_dir=backup_dir,
        discovery=discovery,
        decision=decision,
        voice_samples_redact_failed=voice_samples_redact_failed,
    )


def decide_post_interview_redirect(base_root: Path) -> InjectionDecision:
    """Re-derive the feed-config vs gmail-config gate decision from disk state.

    Called by ``/onboarding/spend-ceiling/{session_id}/finish`` (#671) so that
    the same branching logic isn't duplicated in two route handlers.  Reads
    ``config/active_sources.txt`` from ``base_root`` — the same file that
    ``inject()`` gates on — and applies the same env-var-bearing-adapter check.
    Returns an :class:`InjectionDecision` with ``gate_to_feed_config`` True
    iff any active RapidAPI adapter is unconfigured.
    """
    active_path = base_root / "config" / "active_sources.txt"
    if not active_path.exists():
        return InjectionDecision(gate_to_feed_config=False, pending_adapter=None)

    from findajob.fetchers.adapters.registry import REGISTERED_ADAPTERS  # noqa: PLC0415

    active_names = [n.strip() for n in active_path.read_text().splitlines() if n.strip() and not n.startswith("#")]
    classes_by_name = {cls.name: cls for cls in REGISTERED_ADAPTERS}
    for name in active_names:
        if name not in classes_by_name:
            continue
        instance = classes_by_name[name]()
        if not instance.required_env_vars:
            continue
        if not instance.is_configured():
            return InjectionDecision(gate_to_feed_config=True, pending_adapter=name)

    return InjectionDecision(gate_to_feed_config=False, pending_adapter=None)
