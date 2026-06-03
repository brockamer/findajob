"""Application-materials prep orchestrator.

Extracted from `scripts/prep_application.py` in M3 (#537). Module-load
`load_env()` moved into `main()` so this module is import-safe (no env
file read at import time).

`run_role()` was consolidated to `findajob.llm.role_runner` in M3's
cleanup PR. Notifications use `findajob.notifications.ntfy.send()` for
persistent kind-tagged delivery (#840). `abbrev_title()`
was consolidated to `findajob.prep_naming` in #556.
"""

import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from findajob.actions import reset_prep_to_scored
from findajob.audit import log_event, write_audit
from findajob.background_tasks import writeback_subprocess
from findajob.classification import JD_MAX_CHARS
from findajob.db import connect
from findajob.llm.openrouter import LLMSpendCeilingExceeded
from findajob.llm.role_runner import run_role
from findajob.notifications.ntfy import send as ntfy_send
from findajob.paths import BASE, IMAGE_ROOT, PANDOC, load_env
from findajob.prep.cost_projection import compute_projection
from findajob.prep.docx_postprocess import _add_cover_letter_spacing, _linkify_contact_info
from findajob.prep.docx_render import render_md_to_docx
from findajob.prep.quarantine import quarantine_stale_prep_folders
from findajob.prep_naming import abbrev_title, build_prep_filenames
from findajob.profile import load_voice_samples, read_file_prefix

DB_PATH = f"{BASE}/data/pipeline.db"
PROFILE_PATH = f"{BASE}/candidate_context/profile.md"
MASTER_RESUME_PATH = f"{BASE}/candidate_context/master_resume.md"

_PROBABILITY_HEADING_RE = re.compile(r"##\s*🎯\s*Probability Assessment")
_PERCENT_SCORE_RE = re.compile(r":\s*\d{1,3}%")


def _fit_analysis_is_complete(text: str | None) -> bool:
    """True iff fit_analysis has both required sections AND parseable scores in each.

    Gates the retry loop after the fit_analyst LLM call AND the Step 7 validation
    that blocks shipment of an incomplete briefing. The downstream score-parsing
    regex requires "## 🎯 Probability Assessment" as a section delimiter and
    `:NN%` patterns on each side of it; if any of these are missing, scores
    parse to None and the dashboard renders empty cells.
    """
    if not text:
        return False
    if "Fit Matrix" not in text:
        return False
    parts = _PROBABILITY_HEADING_RE.split(text, maxsplit=1)
    if len(parts) != 2:
        return False
    return bool(_PERCENT_SCORE_RE.search(parts[0]) and _PERCENT_SCORE_RE.search(parts[1]))


def main() -> None:
    import argparse

    # Module-load side effect deferred to here so import is safe.
    load_env()

    parser = argparse.ArgumentParser(description="Prep application materials")
    parser.add_argument("company")
    parser.add_argument("title")
    parser.add_argument("url")
    parser.add_argument("job_id")
    parser.add_argument("--phase", choices=["a", "b", "all"], default="all")
    args = parser.parse_args()

    with writeback_subprocess(DB_PATH):
        if args.phase == "a":
            _run_prep_phase_a(args.company, args.title, args.url, args.job_id)
        elif args.phase == "b":
            _run_prep_phase_b(args.company, args.title, args.url, args.job_id)
        else:
            _run_prep_phase_a(args.company, args.title, args.url, args.job_id)
            _run_prep_phase_b(args.company, args.title, args.url, args.job_id)


def _handle_prep_subprocess_failure(
    conn: sqlite3.Connection,
    job_id: str,
    company: str,
    title: str,
    outdir: str,
    exc: subprocess.CalledProcessError,
) -> None:
    # The 4 must-succeed subprocesses (3× pandoc, 1× find_contacts) raise
    # CalledProcessError when they fail. Without this handler the prep folder
    # would still be partially populated and stage would advance to
    # materials_drafted as if everything succeeded (#495). Sentinel keeps the
    # partial folder for operator inspection rather than rmtree-ing it.
    cmd = exc.cmd[0] if isinstance(exc.cmd, list) and exc.cmd else str(exc.cmd)
    raw_stderr = exc.stderr if exc.stderr else b""
    if isinstance(raw_stderr, bytes):
        stderr_tail = raw_stderr[-2000:].decode("utf-8", errors="replace")
    else:
        stderr_tail = raw_stderr[-2000:]
    sentinel_path = os.path.join(outdir, ".failed_subprocess")
    try:
        with open(sentinel_path, "w") as f:
            f.write(
                f"ts: {datetime.now(UTC).isoformat()}\n"
                f"cmd: {cmd}\n"
                f"returncode: {exc.returncode}\n"
                f"stderr_tail:\n{stderr_tail}\n"
            )
    except OSError:
        pass  # outdir may not exist yet; sentinel is best-effort
    log_event(
        "prep_subprocess_failed",
        company=company,
        title=title,
        job_id=job_id,
        cmd=cmd,
        returncode=exc.returncode,
    )
    reset_prep_to_scored(conn, job_id, reason=f"subprocess_failed:{os.path.basename(cmd)}")
    ntfy_send(
        f"Prep failed: {company} — {title}",
        f"A: subprocess_error\n{cmd} exit {exc.returncode}",
        kind="prep_failure",
    )


def _handle_prep_runtime_failure(
    conn: sqlite3.Connection,
    job_id: str,
    company: str,
    title: str,
    outdir: str,
    reason: str,
) -> None:
    """Roll Phase A back to ``scored`` after a non-subprocess runtime failure.

    Covers the spend-ceiling breach (``LLMSpendCeilingExceeded``) and the
    fail-closed DB-error path from the ceiling gate (#956) — failure classes
    the original handler (``CalledProcessError`` only) let fall through
    uncaught, stranding the job in ``prep_in_progress`` until the 60-min
    watchdog with no notification.

    The reset is guarded on stage='prep_in_progress' (``reset_prep_to_scored``).
    If it's a no-op because the job already advanced — e.g. an OperationalError
    surfacing from a write AFTER stage was committed to ``briefing_ready`` —
    this is a post-success hiccup, not a prep failure: skip the sentinel/event/
    ntfy so a succeeded prep isn't mislabeled as failed. If the reset call
    itself errors (genuinely unavailable DB), still alert — the watchdog
    remains the backstop.
    """
    try:
        did_reset = reset_prep_to_scored(conn, job_id, reason=reason)
    except Exception as e:  # noqa: BLE001 — recovery is best-effort; watchdog backstops
        log_event("prep_reset_failed", job_id=job_id, reason=reason, error=f"{type(e).__name__}: {e}")
        did_reset = True  # the reset itself errored mid-failure — alert the operator
    if not did_reset:
        return  # stage already advanced — a post-success hiccup, not a prep failure
    sentinel_path = os.path.join(outdir, ".failed_prep")
    try:
        with open(sentinel_path, "w") as f:
            f.write(f"ts: {datetime.now(UTC).isoformat()}\nreason: {reason}\n")
    except OSError:
        pass  # outdir may not exist yet; sentinel is best-effort
    log_event("prep_runtime_failed", company=company, title=title, job_id=job_id, reason=reason)
    ntfy_send(
        f"Prep failed: {company} — {title}",
        f"A: {reason}\nStage reset to scored.",
        kind="prep_failure",
    )


def _run_prep() -> None:
    """Legacy wrapper: run Phase A then Phase B in sequence (--phase=all default)."""
    company, title, url, job_id = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
    _run_prep_phase_a(company, title, url, job_id)
    _run_prep_phase_b(company, title, url, job_id)


def _run_prep_phase_a(company: str, title: str, url: str, job_id: str) -> None:
    """Phase A: company_researcher → briefing_writer → fit_analyst.

    Writes briefing.md + briefing.docx to the prep folder, stores fit_score
    and probability_score in the DB, and transitions stage to ``briefing_ready``.
    On any failure, resets stage to ``scored`` (same as the pre-split path).
    """
    rec_re = re.compile(r"^##[^\n]*Overall Recommendation\s*:", re.MULTILINE)

    # Guard: skip if prep already completed for this job
    conn_check = connect(DB_PATH, timeout=30)
    conn_check.row_factory = sqlite3.Row
    existing = conn_check.execute("SELECT prep_folder_path, stage FROM jobs WHERE id=?", (job_id,)).fetchone()
    conn_check.close()
    if existing and existing["prep_folder_path"] and existing["stage"] == "materials_drafted":
        log_event("prep_skipped_duplicate", company=company, title=title, job_id=job_id)
        print(f"PREP_SKIPPED: materials already drafted for {job_id}")
        return

    # Sanitize company for filesystem safety (title already goes through abbrev_title)
    safe_company = re.sub(r"[^\w\s\-&.,]", "_", company).strip()
    date = datetime.now().strftime("%Y-%m-%d")
    time_str = datetime.now().strftime("%H%M%S")
    companies_dir = f"{BASE}/companies"
    folder_prefix = f"{safe_company}_{abbrev_title(title)}_"
    outdir = f"{companies_dir}/{folder_prefix}{date}_{time_str}"

    # Quarantine any prior prep folders for this {company, title} that aren't
    # tracked by the DB — Regenerate and prep races otherwise leave orphans (#174).
    cleanup_conn = connect(DB_PATH, timeout=30)
    try:
        quarantine_stale_prep_folders(cleanup_conn, companies_dir, folder_prefix, os.path.basename(outdir))
    finally:
        cleanup_conn.close()

    os.makedirs(outdir, exist_ok=True)

    # Build per-file output paths using the candidate's file prefix (from profile.md).
    # Pattern: {Prefix} Resume - {Company} - {Title} - {YYYYMMDD-HHMMSS}.{ext}
    file_prefix = read_file_prefix()
    timestamp_fn = f"{date.replace('-', '')}-{time_str}"
    fn = build_prep_filenames(company, title, timestamp_fn, file_prefix)
    out = {k: os.path.join(outdir, v) for k, v in fn.items()}

    log_event("prep_started", company=company, title=title, job_id=job_id, file_prefix=file_prefix)

    # ── Step 1: Load JD from DB (already fetched during triage) ──
    # Do NOT re-curl — LinkedIn and many other URLs require auth and will return garbage.
    conn = connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT raw_jd_text, stage, synthetic, speculative_briefing_folder FROM jobs WHERE id=?",
            (job_id,),
        ).fetchone()
        jd_text = (row["raw_jd_text"] or "").strip() if row else ""
        is_synthetic = bool(row["synthetic"]) if row and "synthetic" in row.keys() else False
        speculative_briefing_folder = (
            row["speculative_briefing_folder"] if row and "speculative_briefing_folder" in row.keys() else None
        )

        if not jd_text or len(jd_text) < 50:
            # Fallback: try curling for Greenhouse/Lever/public URLs only
            try:
                raw = subprocess.run(["curl", "-sL", "--max-time", "15", url], capture_output=True, text=True).stdout
                jd_text = subprocess.run(
                    [PANDOC, "-f", "html", "-t", "plain"], input=raw, capture_output=True, text=True
                ).stdout[:JD_MAX_CHARS]
            except Exception:
                jd_text = "[ERROR: Could not fetch JD]"

        with open(out["jd_txt"], "w") as f:
            f.write(jd_text)

        # ── Load profile and master resume — injected directly, never via RAG ──
        missing_files = []
        try:
            with open(PROFILE_PATH) as f:
                profile_text = f.read()
        except FileNotFoundError:
            missing_files.append(f"profile.md ({PROFILE_PATH})")
            profile_text = ""

        try:
            with open(MASTER_RESUME_PATH) as f:
                master_text = f.read()
        except FileNotFoundError:
            missing_files.append(f"master_resume.md ({MASTER_RESUME_PATH})")
            master_text = ""

        if missing_files:
            log_event("prep_missing_candidate_files", job_id=job_id, company=company, missing="; ".join(missing_files))
            shutil.rmtree(outdir, ignore_errors=True)
            reset_prep_to_scored(conn, job_id, reason="missing_candidate_files")
            ntfy_send(
                f"Prep failed: {company} — {title}",
                f"A: missing_files\n{'; '.join(missing_files)}",
                kind="prep_failure",
            )
            return

        # ── Per-prep cost projection (#713) ──
        # Whole-prep projection at Phase A start. Best-effort: a projection
        # failure never blocks the prep — the operator wanted early warning,
        # not a gate. ``prep_cost_projection_high`` fires only when both
        # values are populated and projection exceeds 1.5x recent median.
        try:
            projection = compute_projection(conn)
            log_event(
                "prep_cost_projection",
                job_id=job_id,
                company=company,
                title=title,
                projected_cost_usd=projection.projected_usd,
                n_roles=projection.n_roles,
                n_roles_with_history=projection.n_roles_with_history,
                expensive_role=projection.expensive_role,
                recent_median_usd=projection.recent_median_usd,
                n_history_preps=projection.n_history_preps,
                ceiling_usd=projection.ceiling_usd,
            )
            if (
                projection.projected_usd is not None
                and projection.ceiling_usd is not None
                and projection.projected_usd > projection.ceiling_usd
            ):
                log_event(
                    "prep_cost_projection_high",
                    job_id=job_id,
                    company=company,
                    title=title,
                    projected_cost_usd=projection.projected_usd,
                    ceiling_usd=projection.ceiling_usd,
                    expensive_role=projection.expensive_role,
                )
        except Exception as e:  # noqa: BLE001 — projection is best-effort
            log_event("prep_cost_projection_failed", job_id=job_id, error=f"{type(e).__name__}: {e}")

        # ── Build shared cached_prefix strings for Opus stages ──
        # Stable content shared across briefing_writer, resume_tailor, cover_letter_writer,
        # and recruiter_critic. Placed as cache_control-marked blocks so Anthropic can
        # serve a prompt-cache hit on the second+ call within a batch prep session.
        # Cross-role caching within a single prep run is deferred to #478.
        voice_samples = load_voice_samples()
        log_event("voice_samples_loaded", caller="prep_shared_prefix", chars=len(voice_samples))
        shared_candidate_jd = (
            f"CANDIDATE PROFILE:\n{profile_text}\n\n"
            f"MASTER RESUME:\n{master_text}\n\n"
            f"Company: {company}\nTitle: {title}\n\n"
            f"JD:\n{jd_text}\n\n"
            f"---\n\n"
        )

        # ── Step 2: Company briefing FIRST — gives all downstream steps rich context ──
        # For synthetic rows (#131 speculative), the deep-research briefing was
        # already generated at submission time and approved by the operator on the
        # review page. Reuse it instead of regenerating via briefing_writer (#320 —
        # spec drift fix). Falls back to the regular briefing_writer flow if the
        # column is unset, the folder is missing, or briefing.md is empty/absent.
        briefing = ""
        if is_synthetic and speculative_briefing_folder:
            spec_briefing_path = os.path.join(BASE, "companies", speculative_briefing_folder, "briefing.md")
            try:
                with open(spec_briefing_path) as f:
                    briefing = f.read().strip()
                if briefing:
                    log_event(
                        "speculative_briefing_reused",
                        job_id=job_id,
                        company=company,
                        folder=speculative_briefing_folder,
                        chars=len(briefing),
                    )
                    # Copy into the prep folder so the materials view surfaces the
                    # raw deep-research briefing as a distinct artifact alongside
                    # the prep-time merged briefing+fit_analysis. Bare filename
                    # `briefing.md` is classified as "Briefing (speculative)" by
                    # findajob.web.routes.materials._classify_file (#485).
                    try:
                        shutil.copy2(spec_briefing_path, os.path.join(outdir, "briefing.md"))
                    except OSError as e:
                        # Copy failure is non-fatal — the merged briefing still gets
                        # written and the spec briefing remains in its origin folder.
                        log_event(
                            "speculative_briefing_copy_failed",
                            job_id=job_id,
                            company=company,
                            error=f"{type(e).__name__}: {e}",
                        )
            except FileNotFoundError:
                log_event(
                    "speculative_briefing_missing",
                    job_id=job_id,
                    company=company,
                    folder=speculative_briefing_folder,
                    expected_path=spec_briefing_path,
                )
                briefing = ""

        if not briefing:
            # Real-row flow OR synthetic-fallback when the speculative briefing is missing.
            brief_prompt = f"Research {company} thoroughly.\nJob title: {title}\nJD:\n{jd_text}"
            # Stage 1 — company_researcher (Perplexity, no caching)
            raw_briefing = run_role("company_researcher", brief_prompt, conn=conn, job_id=job_id)

            # Pass raw research through briefing_writer with candidate context for stories.
            # Stage 2 — briefing_writer (Opus, cached_prefix=shared_candidate_jd)
            briefing_tail = (
                f"Format the following company research into a structured briefing 1-pager "
                f"for {company}. Job: {title}.\n\n"
                f"RAW RESEARCH:\n{raw_briefing}"
            )
            briefing = run_role(
                "briefing_writer",
                briefing_tail,
                cached_prefix=shared_candidate_jd,
                pin_provider="anthropic",
                conn=conn,
                job_id=job_id,
            )

            # ── Validate: briefing must end with an Overall Recommendation section ──
            # The role prompt requires this verdict heading; model sometimes drops it.
            # Retry once; if still missing, let downstream validator fail prep cleanly.
            if not briefing or not rec_re.search(briefing):
                log_event("briefing_missing_recommendation", job_id=job_id, company=company, retry=1)
                briefing = run_role(
                    "briefing_writer",
                    briefing_tail,
                    cached_prefix=shared_candidate_jd,
                    pin_provider="anthropic",
                    conn=conn,
                    job_id=job_id,
                )

        # Fit analysis: multi-dimensional assessment appended to briefing
        # Stage 3 — fit_analyst (Perplexity, no caching)
        fit_prompt = (
            f"Analyze the fit between this candidate and this role.\n\n"
            f"CANDIDATE PROFILE:\n{profile_text}\n\n"
            f"MASTER RESUME:\n{master_text}\n\n"
            f"Company: {company}\nTitle: {title}\n\n"
            f"JD:\n{jd_text}\n\n"
            f"COMPANY BRIEFING:\n{briefing}"
        )
        fit_analysis = run_role("fit_analyst", fit_prompt, conn=conn, job_id=job_id)

        # Retry once when fit_analysis is empty or structurally incomplete.
        if not _fit_analysis_is_complete(fit_analysis):
            log_event("fit_analyst_retry", job_id=job_id, company=company, title=title, retry=1)
            fit_analysis = run_role("fit_analyst", fit_prompt, conn=conn, job_id=job_id)

        # Combine briefing and fit analysis into one document.
        # The briefing ends with an Overall Recommendation verdict; fit analysis
        # contains the Matrix/Probability/Strengths/Gaps detail that should sit
        # BEFORE the verdict so the doc reads detail → synthesis → recommendation.
        fit_score_avg = None
        prob_score_avg = None
        full_briefing = briefing
        if fit_analysis:
            rec_match = rec_re.search(briefing)
            if rec_match:
                briefing_pre = briefing[: rec_match.start()].rstrip()
                briefing_rec = briefing[rec_match.start() :]
                full_briefing = f"{briefing_pre}\n\n---\n\n# Fit Analysis\n\n{fit_analysis}\n\n---\n\n{briefing_rec}"
            else:
                full_briefing = f"{briefing}\n\n---\n\n# Fit Analysis\n\n{fit_analysis}"
            # Parse scores from fit analysis for DB storage
            # All scores are 0-100%. Fit Matrix section has 6 dimensions, Probability has 3.
            try:
                # Split on Probability Assessment heading to separate the two sections
                parts = re.split(r"##\s*🎯\s*Probability Assessment", fit_analysis, maxsplit=1)
                fit_section = parts[0] if parts else fit_analysis
                prob_section = parts[1] if len(parts) > 1 else ""
                fit_scores = [int(m.group(1)) for m in re.finditer(r":\s*(\d{1,3})%", fit_section)]
                prob_scores = [int(m.group(1)) for m in re.finditer(r":\s*(\d{1,3})%", prob_section)]
                if fit_scores:
                    fit_score_avg = round(sum(fit_scores) / len(fit_scores), 1)
                if prob_scores:
                    prob_score_avg = round(sum(prob_scores) / len(prob_scores), 1)
                log_event(
                    "fit_analysis",
                    company=company,
                    title=title,
                    fit_score=fit_score_avg,
                    probability_score=prob_score_avg,
                    fit_scores=fit_scores,
                    prob_scores=prob_scores,
                )
            except Exception:
                pass

        with open(out["briefing_md"], "w") as f:
            f.write(full_briefing)
        render_md_to_docx(out["briefing_md"], out["briefing_docx"], has_yaml_frontmatter=True)

        # ── Phase A validation ──
        # Briefing must end with an Overall Recommendation verdict.
        try:
            with open(out["briefing_md"]) as f:
                briefing_check = f.read()
        except OSError:
            briefing_check = ""
        validation_failures = []
        if not rec_re.search(briefing_check):
            validation_failures.append("briefing: missing Overall Recommendation")
        if fit_score_avg is None or prob_score_avg is None:
            validation_failures.append(
                f"briefing: missing fit analysis (fit_score={fit_score_avg}, prob_score={prob_score_avg})"
            )
        if validation_failures:
            log_event(
                "prep_validation_failed",
                company=company,
                title=title,
                failures="; ".join(validation_failures),
            )
            shutil.rmtree(outdir, ignore_errors=True)
            reset_prep_to_scored(conn, job_id, reason="validation_failed")
            ntfy_send(
                f"Prep failed: {company} — {title}",
                f"A: validation_fail\n{'; '.join(validation_failures)}",
                kind="prep_failure",
            )
            return

        # ── Phase A DB write: transition to briefing_ready with scores ──
        now = datetime.now(UTC).isoformat()
        old_stage = row["stage"] if row else "unknown"
        conn.execute(
            """
            UPDATE jobs SET stage='briefing_ready', stage_updated=?, prep_folder_path=?,
                   fit_score=?, probability_score=?, updated_at=?
            WHERE id=?
            """,
            (now, outdir, fit_score_avg, prob_score_avg, now, job_id),
        )
        conn.commit()
        write_audit(conn, job_id, "stage", old_stage, "briefing_ready")

        log_event("prep_phase_a_complete", company=company, title=title, folder=outdir)
        ntfy_send(
            f"Briefing ready: {company} — {title}",
            outdir,
            kind="prep_briefing_ready",
        )

        conn.close()
        print(f"PREP_PHASE_A_COMPLETE:{outdir}")

    except LLMSpendCeilingExceeded as exc:
        # #956: a spend-ceiling breach mid-prep is routine (operator-configured
        # affordability gate), not exotic. run_role re-raises it (it is
        # deliberately NOT an OpenRouterError). Reset + notify immediately
        # instead of stranding the job in prep_in_progress for the watchdog.
        _handle_prep_runtime_failure(conn, job_id, company, title, outdir, reason="spend_ceiling")
        raise SystemExit(1) from exc
    except sqlite3.OperationalError as exc:
        # #956: the ceiling gate is fail-closed — a missing/half-initialized
        # pipeline.db propagates a raw OperationalError through complete().
        # Recover the same way rather than crash uncaught.
        _handle_prep_runtime_failure(conn, job_id, company, title, outdir, reason="db_error")
        raise SystemExit(1) from exc
    except subprocess.CalledProcessError as exc:
        _handle_prep_subprocess_failure(conn, job_id, company, title, outdir, exc)
        raise SystemExit(1) from exc


def _run_prep_phase_b(company: str, title: str, url: str, job_id: str) -> None:
    """Phase B: resume_tailor → resume_change_reviewer → cover_letter_writer →
    recruiter_critic → find_contacts.

    Re-reads briefing from disk and JD/scores from DB on entry (no in-memory
    handoff). On subprocess failure, resets stage to ``briefing_ready`` (NOT
    ``scored``) so the operator can retry without re-paying Phase A.
    """
    # ── Re-read state from DB ──
    conn = connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT raw_jd_text, stage, synthetic, prep_folder_path, fit_score, probability_score, "
            "speculative_briefing_folder FROM jobs WHERE id=?",
            (job_id,),
        ).fetchone()
        jd_text = (row["raw_jd_text"] or "").strip() if row else ""
        is_synthetic = bool(row["synthetic"]) if row and "synthetic" in row.keys() else False
        mode_marker = "<<SPECULATIVE_MODE>>\n\n" if is_synthetic else ""
        outdir = row["prep_folder_path"] if row and row["prep_folder_path"] else ""

        if not outdir or not os.path.isdir(outdir):
            log_event(
                "prep_phase_b_missing_folder",
                job_id=job_id,
                company=company,
                title=title,
                outdir=outdir or "(empty)",
            )
            ntfy_send(
                f"Prep failed: {company} — {title}",
                f"B: aborted_no_folder\n{outdir or '(empty)'}",
                kind="prep_failure",
            )
            raise SystemExit(1)

        if not jd_text or len(jd_text) < 50:
            # Fallback curl (same as Phase A)
            try:
                raw = subprocess.run(["curl", "-sL", "--max-time", "15", url], capture_output=True, text=True).stdout
                jd_text = subprocess.run(
                    [PANDOC, "-f", "html", "-t", "plain"], input=raw, capture_output=True, text=True
                ).stdout[:JD_MAX_CHARS]
            except Exception:
                jd_text = "[ERROR: Could not fetch JD]"

        # ── Re-read profile and master resume ──
        try:
            with open(PROFILE_PATH) as f:
                profile_text = f.read()
        except FileNotFoundError:
            profile_text = ""

        try:
            with open(MASTER_RESUME_PATH) as f:
                master_text = f.read()
        except FileNotFoundError:
            master_text = ""

        # ── Re-load voice samples ──
        voice_samples = load_voice_samples()
        log_event("voice_samples_loaded", caller="prep_phase_b", chars=len(voice_samples))
        voice_section = f"VOICE SAMPLES:\n{voice_samples}\n\n" if voice_samples else ""
        shared_candidate_jd = (
            f"CANDIDATE PROFILE:\n{profile_text}\n\n"
            f"MASTER RESUME:\n{master_text}\n\n"
            f"Company: {company}\nTitle: {title}\n\n"
            f"JD:\n{jd_text}\n\n"
            f"---\n\n"
        )
        shared_with_voice = f"{shared_candidate_jd}{voice_section}---\n\n"

        # ── Re-read briefing from disk ──
        # Handles both {Prefix} Briefing - ... .md (regular) and bare briefing.md (speculative).
        briefing_files = list(Path(outdir).glob("*Briefing*.md")) + list(Path(outdir).glob("briefing.md"))
        if briefing_files:
            with open(briefing_files[0]) as f:
                full_briefing = f.read()
        else:
            full_briefing = ""
            log_event("prep_phase_b_no_briefing", job_id=job_id, company=company, title=title, outdir=outdir)

        briefing_context = full_briefing if full_briefing else ""

        # ── Build output paths using a fresh timestamp ──
        file_prefix = read_file_prefix()
        date = datetime.now().strftime("%Y-%m-%d")
        time_str = datetime.now().strftime("%H%M%S")
        timestamp_fn = f"{date.replace('-', '')}-{time_str}"
        fn = build_prep_filenames(company, title, timestamp_fn, file_prefix)
        out = {k: os.path.join(outdir, v) for k, v in fn.items()}

        # ── Stage 4: resume_tailor ──
        resume_md = run_role(
            "resume_tailor",
            f"COMPANY BRIEFING AND FIT ANALYSIS:\n{briefing_context}",
            cached_prefix=shared_candidate_jd,
            pin_provider="anthropic",
            conn=conn,
            job_id=job_id,
        )
        # Strip [VERIFY: ...] lines that appear before the first # header
        rlines = resume_md.split("\n")
        first_hdr = next((i for i, line in enumerate(rlines) if line.startswith("#")), 0)
        rlines = [line for i, line in enumerate(rlines) if not (i < first_hdr and line.startswith("[VERIFY:"))]
        resume_md = "\n".join(rlines).strip()
        resume_md = _linkify_contact_info(resume_md)
        with open(out["resume_md"], "w") as f:
            f.write(resume_md)

        # Quality check — informational only, never block prep
        try:
            qc = subprocess.run(
                [sys.executable, f"{IMAGE_ROOT}/scripts/diag/validate_resume.py", "--json", out["resume_md"]],
                capture_output=True,
                text=True,
                timeout=15,
            )
            if qc.stdout:
                viols_by_file = json.loads(qc.stdout)
                viols = list(viols_by_file.values())[0] if viols_by_file else []
                log_event(
                    "resume_quality_check",
                    company=company,
                    title=title,
                    violations=len(viols),
                    high=sum(1 for v in viols if v["severity"] == "HIGH"),
                    med=sum(1 for v in viols if v["severity"] == "MED"),
                )
        except Exception:
            pass

        render_md_to_docx(out["resume_md"], out["resume_docx"])

        # ── Stage 5: resume_change_reviewer ──
        changes_prompt = (
            f"ORIGINAL MASTER RESUME:\n{master_text}\n\nTAILORED RESUME:\n{resume_md}\n\nTARGET JD:\n{jd_text}"
        )
        changes_md = run_role("resume_change_reviewer", changes_prompt, conn=conn, job_id=job_id)
        with open(out["changes_md"], "w") as f:
            f.write(changes_md)

        # ── Stage 6: cover_letter_writer ──
        today_str = datetime.now().strftime("%B %d, %Y")
        cover_prompt = (
            f"{mode_marker}Date: {today_str}\n\n"
            f"COMPANY BRIEFING AND FIT ANALYSIS:\n{briefing_context}\n\n"
            f"TAILORED RESUME:\n{resume_md}"
        )
        cover_md_text = run_role(
            "cover_letter_writer",
            cover_prompt,
            cached_prefix=shared_with_voice,
            pin_provider="anthropic",
            conn=conn,
            job_id=job_id,
        )
        # Strip horizontal rules — LLM inserts "---" between header and body.
        cover_md_text = re.sub(r"\n---\n", "\n\n", cover_md_text)
        with open(out["cover_md"], "w") as f:
            f.write(cover_md_text)
        render_md_to_docx(out["cover_md"], out["cover_docx"])
        _add_cover_letter_spacing(out["cover_docx"])

        # ── Stage 7: recruiter_critic ──
        critique_md = run_role(
            "recruiter_critic",
            f"Company: {company}\nTitle: {title}\n\nTAILORED RESUME:\n{resume_md}\n\nCOVER LETTER:\n{cover_md_text}",
            cached_prefix=f"JD:\n{jd_text}\n\n---\n\n",
            pin_provider="anthropic",
            conn=conn,
            job_id=job_id,
        )
        if critique_md:
            with open(out["critique_md"], "w") as f:
                f.write(critique_md)

        # ── Step 5: Network outreach ──
        subprocess.run(
            [
                sys.executable,
                f"{IMAGE_ROOT}/scripts/find_contacts.py",
                company,
                jd_text,
                outdir,
                file_prefix,
                timestamp_fn,
                "1" if is_synthetic else "0",
                job_id,
            ],
            check=True,
            capture_output=True,
        )

        # ── Step 6: Review checklist ──
        with open(out["checklist_md"], "w") as f:
            f.write(f"""# Review Checklist — {company} / {title}
    Generated: {date}

    ## Before sending, complete these steps:
    - [ ] Open `{fn["changes_md"]}` — review every flagged reorder/keyword add
    - [ ] Open `{fn["resume_docx"]}` — fill any [MISSING: ...] placeholders
    - [ ] Open `{fn["cover_docx"]}` — fill any [MISSING: ...] placeholders (expect 1-2 max)
    - [ ] Read cover letter aloud — does it sound like you?
    - [ ] Verify every factual claim in the cover letter (metrics, company names, titles)
    - [ ] Check `{fn["briefing_docx"]}` — any red flags or new intel to weave in?
    - [ ] Review outreach drafts if you plan to reach out before applying

    ## Files in this folder:
    - `{fn["resume_docx"]}`    ← start here
    - `{fn["changes_md"]}`    ← what the AI changed and why
    - `{fn["cover_docx"]}`       ← fill placeholders before sending
    - `{fn["briefing_docx"]}`
    - `{fn["jd_txt"]}`    ← original JD for reference
    - `{file_prefix} Outreach to *.txt`    ← network outreach drafts
    """)

        # ── Step 7: Validate output ──
        MIN_BYTES = 500
        validation_failures = []
        for label, path in [("resume", out["resume_md"]), ("cover_letter", out["cover_md"])]:
            try:
                sz = os.path.getsize(path)
            except OSError:
                sz = 0
            if sz < MIN_BYTES:
                validation_failures.append(f"{label}: {sz}B (min {MIN_BYTES})")
        if validation_failures:
            log_event(
                "prep_validation_failed",
                company=company,
                title=title,
                failures="; ".join(validation_failures),
            )
            # Phase B validation failure: reset to briefing_ready (NOT scored).
            # Briefing + folder stay intact so operator can retry Phase B.
            _handle_phase_b_failure(conn, job_id, company, title, "validation_failed")
            return

        # ── Step 8: Update SQLite (stage = materials_drafted) ──
        now = datetime.now(UTC).isoformat()
        old_stage = row["stage"] if row else "unknown"
        conn.execute(
            """
            UPDATE jobs SET stage='materials_drafted', stage_updated=?, updated_at=?
            WHERE id=?
            """,
            (now, now, job_id),
        )
        conn.commit()
        write_audit(conn, job_id, "stage", old_stage, "materials_drafted")

        log_event("prep_complete", company=company, title=title, folder=outdir)
        ntfy_send(
            f"Drafts ready: {company} — {title}",
            outdir,
            kind="prep_drafts_ready",
        )

        conn.close()
        print(f"PREP_COMPLETE:{outdir}")

    except LLMSpendCeilingExceeded as exc:
        # #956: ceiling breach mid-Phase-B — reset to briefing_ready (NOT
        # scored), preserving the briefing folder so the operator can retry
        # Phase B without re-paying Phase A. Same recovery as a subprocess crash.
        _handle_phase_b_failure(conn, job_id, company, title, "spend_ceiling")
        raise SystemExit(1) from exc
    except sqlite3.OperationalError as exc:
        # #956: fail-closed ceiling-gate DB error mid-Phase-B — recover, don't crash.
        _handle_phase_b_failure(conn, job_id, company, title, "db_error")
        raise SystemExit(1) from exc
    except subprocess.CalledProcessError as exc:
        # Phase B subprocess failure: reset to briefing_ready (NOT scored).
        # Preserves the briefing folder so operator can retry without re-paying Phase A.
        cmd_name = exc.cmd[0] if isinstance(exc.cmd, list) and exc.cmd else str(exc.cmd)
        _handle_phase_b_failure(conn, job_id, company, title, f"subprocess_failed:{os.path.basename(cmd_name)}")
        raise SystemExit(1) from exc


def _handle_phase_b_failure(
    conn: sqlite3.Connection,
    job_id: str,
    company: str,
    title: str,
    reason: str,
) -> None:
    """Roll Phase B back to ``briefing_ready``.

    Does NOT call ``_handle_prep_subprocess_failure`` (which resets to
    ``scored`` and rmtrees the folder). Phase B preserves the briefing
    folder so the operator can retry without re-paying Phase A.

    The ``old_value`` in the audit row is read from the current row
    rather than hard-coded — Phase B can be entered from either
    ``briefing_ready`` (legacy ``--phase=all`` wrapper, Phase A just
    finished) or ``prep_in_progress`` (the ``/continue-prep`` route,
    which transitions stage before spawning the subprocess).

    Guards on those two pre-materials stages: if the job already advanced to
    ``materials_drafted`` — e.g. an OperationalError surfacing from the
    post-commit ``write_audit`` AFTER the success stage was committed (Phase B
    commits ``materials_drafted`` before its audit write) — this is a
    post-success hiccup, not a Phase B failure. Reverting it would clobber a
    completed application, so skip the reset AND the misleading prep_failure
    ntfy. (#956)
    """
    try:
        existing = conn.execute("SELECT stage FROM jobs WHERE id=?", (job_id,)).fetchone()
        old_stage = existing[0] if existing else "unknown"
    except Exception:  # noqa: BLE001 — best-effort; can't safely act on an unreadable stage
        old_stage = "unknown"
    if old_stage not in ("prep_in_progress", "briefing_ready"):
        return  # already advanced (or unreadable) — don't revert a completed prep or false-notify
    now = datetime.now(UTC).isoformat()
    try:
        conn.execute(
            "UPDATE jobs SET stage='briefing_ready', stage_updated=?, updated_at=? WHERE id=?",
            (now, now, job_id),
        )
        conn.commit()
        write_audit(conn, job_id, "stage", old_stage, "briefing_ready")
    except Exception:
        pass  # best-effort; the original error is already propagating
    log_event("prep_phase_b_failed", company=company, title=title, job_id=job_id, reason=reason)
    ntfy_send(
        f"Prep failed: {company} — {title}",
        f"B: {reason}\nBriefing intact; retry available.",
        kind="prep_failure",
    )
