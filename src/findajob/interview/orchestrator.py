"""Interview-prep orchestrator.

Extracted from `scripts/interview_prep.py` in M3 (#537). Module-load
`load_env()` deferred into `main()`. `run_role()` was consolidated to
`findajob.llm.role_runner` and `notify()` to
`findajob.notifications.ntfy.send()` for persistent kind-tagged
delivery (#840).

M6 swap (2026-05-08): the prior `.interview_prep_in_progress` sentinel
file was replaced by the `background_tasks` row contract. Concurrency
control still happens — but at the row level, with the launcher
inserting a `running` row before spawn and the watchdog reaping stuck
rows by per-kind timeout. The `findajob.interview.sentinel` module
was deleted in the same PR.
"""

import os
import re
import sqlite3
import subprocess
import sys
from datetime import datetime

from findajob.audit import log_event
from findajob.background_tasks import writeback_subprocess
from findajob.db import connect
from findajob.interview.flashcards import build_all as build_flashcards
from findajob.llm.role_runner import run_role
from findajob.notifications.ntfy import send as ntfy_send
from findajob.paths import BASE, PANDOC, load_env
from findajob.prep_naming import safe_filename_part
from findajob.profile import read_file_prefix

DB_PATH = f"{BASE}/data/pipeline.db"
PROFILE_PATH = f"{BASE}/candidate_context/profile.md"
MASTER_RESUME_PATH = f"{BASE}/candidate_context/master_resume.md"


def _latest(folder: str, pattern: re.Pattern[str]) -> str | None:
    """Return the absolute path of the most recently modified file in `folder`
    whose basename matches `pattern`, or None if no match."""
    if not folder or not os.path.isdir(folder):
        return None
    matches = [
        os.path.join(folder, name)
        for name in os.listdir(folder)
        if pattern.search(name) and os.path.isfile(os.path.join(folder, name))
    ]
    if not matches:
        return None
    matches.sort(key=os.path.getmtime, reverse=True)
    return matches[0]


def _read_or_empty(path: str | None) -> str:
    if not path:
        return ""
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return ""


def main() -> None:
    # Module-load side effect deferred to here so import is safe.
    load_env()
    with writeback_subprocess(DB_PATH):
        _run_interview_prep()


def _run_interview_prep() -> None:
    if len(sys.argv) < 4:
        print("Usage: interview_prep.py <company> <title> <job_id>", file=sys.stderr)
        sys.exit(2)

    company, title, job_id = sys.argv[1], sys.argv[2], sys.argv[3]

    # ── Look up job + prep folder ──
    conn = connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT prep_folder_path, raw_jd_text, stage FROM jobs WHERE id=?",
        (job_id,),
    ).fetchone()

    if not row:
        conn.close()
        log_event("interview_prep_error", job_id=job_id, reason="job_not_found")
        return

    try:
        prep_folder = row["prep_folder_path"]
        if not prep_folder or not os.path.isdir(prep_folder):
            log_event(
                "interview_prep_error",
                job_id=job_id,
                company=company,
                title=title,
                reason="no_prep_folder",
                folder=prep_folder,
            )
            ntfy_send(
                f"Interview prep failed: {company} — {title}",
                "no_prep_folder\nNo prep folder; apply was likely manual.",
                kind="interview_prep_failed",
            )
            return

        # M6: concurrency control via background_tasks rows, not the
        # prior `.interview_prep_in_progress` sentinel file. Re-clicks
        # are no-ops in the action layer if a `running` row already
        # exists for this (job_id, kind='interview_prep'); see
        # findajob.web.routes.board_actions._launch_interview_prep_subprocess.
        _generate(prep_folder, company, title, job_id, row["raw_jd_text"] or "", conn=conn)
    finally:
        conn.close()


def _generate(
    prep_folder: str,
    company: str,
    title: str,
    job_id: str,
    jd_text: str,
    *,
    conn: sqlite3.Connection | None = None,
) -> None:
    log_event(
        "interview_prep_started",
        job_id=job_id,
        company=company,
        title=title,
        folder=prep_folder,
    )

    # ── Discover existing artifacts in the prep folder ──
    # `Resume Changes` must NOT match the resume regex — it's a different doc.
    briefing_path = _latest(prep_folder, re.compile(r"Briefing.*\.md$"))
    resume_path = _latest(prep_folder, re.compile(r"(?<!Changes )Resume(?! Changes).*\.md$"))
    cover_path = _latest(prep_folder, re.compile(r"Cover.*\.md$"))
    critique_path = _latest(prep_folder, re.compile(r"Critique.*\.md$"))

    briefing = _read_or_empty(briefing_path)
    resume = _read_or_empty(resume_path)
    cover = _read_or_empty(cover_path)
    critique = _read_or_empty(critique_path)

    if not briefing:
        log_event(
            "interview_prep_error",
            job_id=job_id,
            company=company,
            title=title,
            reason="no_briefing_in_prep_folder",
            folder=prep_folder,
        )
        ntfy_send(
            f"Interview prep failed: {company} — {title}",
            "no_briefing\nNo briefing found in prep folder; cannot expand.",
            kind="interview_prep_failed",
        )
        return

    # ── Load profile + master resume — injected directly, never via RAG ──
    profile = _read_or_empty(PROFILE_PATH)
    master = _read_or_empty(MASTER_RESUME_PATH)

    if not profile or not master:
        log_event(
            "interview_prep_error",
            job_id=job_id,
            company=company,
            title=title,
            reason="missing_candidate_files",
            profile=bool(profile),
            master=bool(master),
        )
        ntfy_send(
            f"Interview prep failed: {company} — {title}",
            "missing_files\nMissing profile.md or master_resume.md.",
            kind="interview_prep_failed",
        )
        return

    # ── Build prompt ──
    # cached_prefix: profile + master_resume — stable across all jobs in a day;
    # enables same-role cache hits. Per-job content (JD, briefing, company) goes in prompt.
    cached_prefix = f"CANDIDATE PROFILE:\n{profile}\n\nMASTER RESUME:\n{master}"

    cover_section = f"\nCOVER LETTER (the version submitted):\n{cover}\n" if cover else ""
    critique_section = f"\nRECRUITER CRITIQUE:\n{critique}\n" if critique else ""
    briefing_header = (
        "COMPANY BRIEFING (canonical — your STAR section MUST expand its questions+stories, not re-derive):"
    )
    prompt = (
        f"Company: {company}\nTitle: {title}\n\n"
        f"JOB DESCRIPTION:\n{jd_text}\n\n"
        f"{briefing_header}\n{briefing}\n\n"
        f"TAILORED RESUME (the version actually submitted):\n{resume}\n"
        f"{cover_section}"
        f"{critique_section}"
    )

    # ── Generate ──
    output_md = run_role(
        "interview_prep",
        prompt,
        cached_prefix=cached_prefix,
        pin_provider="anthropic",
        conn=conn,
        job_id=job_id,
    )

    if not output_md or len(output_md) < 500:
        log_event(
            "interview_prep_error",
            job_id=job_id,
            company=company,
            title=title,
            reason="empty_or_short_output",
            chars=len(output_md) if output_md else 0,
        )
        ntfy_send(
            f"Interview prep failed: {company} — {title}",
            "empty_or_short_output\nLLM returned empty/short output.",
            kind="interview_prep_failed",
        )
        return

    # ── Write artifact ──
    file_prefix = read_file_prefix()
    co = safe_filename_part(company, 40)
    t = safe_filename_part(title, 60)
    timestamp_fn = datetime.now().strftime("%Y%m%d-%H%M%S")
    base = f"{file_prefix} Interview Prep - {co} - {t} - {timestamp_fn}"
    md_path = os.path.join(prep_folder, f"{base}.md")
    docx_path = os.path.join(prep_folder, f"{base}.docx")

    with open(md_path, "w") as f:
        f.write(output_md)

    subprocess.run(
        [
            PANDOC,
            md_path,
            "--lua-filter",
            f"{BASE}/config/strip-bookmarks.lua",
            "--reference-doc",
            f"{BASE}/config/reference.docx",
            "-o",
            docx_path,
        ],
        check=False,
    )

    log_event(
        "interview_prep_complete",
        job_id=job_id,
        company=company,
        title=title,
        folder=prep_folder,
        md=os.path.basename(md_path),
        chars=len(output_md),
    )

    # ── Study materials: study guide + flashcard deck ──
    _generate_study_materials(
        prep_folder=prep_folder,
        company=company,
        title=title,
        job_id=job_id,
        jd_text=jd_text,
        briefing=briefing,
        resume=resume,
        cover=cover,
        critique=critique,
        interview_prep=output_md,
        cached_prefix=cached_prefix,
        file_prefix=file_prefix,
        co=co,
        t=t,
        timestamp_fn=timestamp_fn,
        conn=conn,
    )

    # ── Podcast: auto-generate Deep Dive format ──
    _generate_podcast_if_configured(
        prep_folder=prep_folder,
        company=company,
        title=title,
        job_id=job_id,
        jd_text=jd_text,
        briefing=briefing,
        resume=resume,
        cover=cover,
        critique=critique,
        interview_prep=output_md,
        cached_prefix=cached_prefix,
        conn=conn,
    )

    ntfy_send(
        f"Interview prep ready: {company} — {title}",
        md_path,
        kind="interview_prep_ready",
    )
    print(f"INTERVIEW_PREP_COMPLETE:{md_path}")


def _generate_study_materials(
    *,
    prep_folder: str,
    company: str,
    title: str,
    job_id: str,
    jd_text: str,
    briefing: str,
    resume: str,
    cover: str,
    critique: str,
    interview_prep: str,
    cached_prefix: str,
    file_prefix: str,
    co: str,
    t: str,
    timestamp_fn: str,
    conn: sqlite3.Connection | None,
) -> None:
    """Generate study guide + flashcard deck after interview prep completes.

    Non-fatal: failures here log + notify but don't fail the overall
    interview prep run (the primary artifact already shipped).
    """
    cover_section = f"\nCOVER LETTER:\n{cover}\n" if cover else ""
    critique_section = f"\nRECRUITER CRITIQUE:\n{critique}\n" if critique else ""

    study_prompt = (
        f"Company: {company}\nTitle: {title}\n\n"
        f"JOB DESCRIPTION:\n{jd_text}\n\n"
        f"COMPANY BRIEFING:\n{briefing}\n\n"
        f"TAILORED RESUME:\n{resume}\n"
        f"{cover_section}"
        f"{critique_section}"
        f"\nINTERVIEW PREP:\n{interview_prep}\n"
    )

    # ── Study Guide ──
    study_guide_md = run_role(
        "study_guide_generator",
        study_prompt,
        cached_prefix=cached_prefix,
        conn=conn,
        job_id=job_id,
    )

    if not study_guide_md or len(study_guide_md) < 200:
        log_event(
            "study_guide_error",
            job_id=job_id,
            company=company,
            title=title,
            reason="empty_or_short_output",
            chars=len(study_guide_md) if study_guide_md else 0,
        )
        ntfy_send(
            f"Study guide failed: {company} — {title}",
            "empty_or_short_output",
            kind="study_guide_failed",
        )
        return

    sg_base = f"{file_prefix} Study Guide - {co} - {t} - {timestamp_fn}"
    sg_path = os.path.join(prep_folder, f"{sg_base}.md")
    with open(sg_path, "w") as f:
        f.write(study_guide_md)

    log_event(
        "study_guide_complete",
        job_id=job_id,
        company=company,
        title=title,
        chars=len(study_guide_md),
    )

    # ── Flashcard Deck ──
    flashcard_prompt = (
        f"Company: {company}\nTitle: {title}\n\n"
        f"JOB DESCRIPTION:\n{jd_text}\n\n"
        f"COMPANY BRIEFING:\n{briefing}\n\n"
        f"TAILORED RESUME:\n{resume}\n"
        f"\nINTERVIEW PREP:\n{interview_prep}\n"
        f"\nSTUDY GUIDE:\n{study_guide_md}\n"
    )

    flashcard_json_raw = run_role(
        "flashcard_generator",
        flashcard_prompt,
        cached_prefix=cached_prefix,
        conn=conn,
        job_id=job_id,
    )

    if not flashcard_json_raw:
        log_event(
            "flashcard_error",
            job_id=job_id,
            company=company,
            title=title,
            reason="empty_output",
        )
        ntfy_send(
            f"Flashcards failed: {company} — {title}",
            "empty_output",
            kind="flashcard_failed",
        )
        return

    fc_base = f"{file_prefix} Flashcards - {co} - {t} - {timestamp_fn}"
    try:
        paths = build_flashcards(
            raw_json=flashcard_json_raw,
            company=company,
            title=title,
            output_dir=prep_folder,
            base_name=fc_base,
        )
        log_event(
            "flashcard_complete",
            job_id=job_id,
            company=company,
            title=title,
            apkg=os.path.basename(paths["apkg"]),
            cards=len(paths),
        )
    except Exception as exc:
        log_event(
            "flashcard_error",
            job_id=job_id,
            company=company,
            title=title,
            reason=f"{type(exc).__name__}: {exc}",
        )
        ntfy_send(
            f"Flashcards failed: {company} — {title}",
            f"{type(exc).__name__}: {exc}",
            kind="flashcard_failed",
        )


# ── Podcast generation ──

FORMAT_INSTRUCTIONS: dict[str, str] = {
    "deep_dive": (
        "FORMAT: Deep Dive (~8 minutes, ~2000 words)\n\n"
        "Speaker A is the lead host who drives the narrative. Speaker B is a curious, "
        "well-prepared co-host who asks probing follow-up questions and pushes back.\n\n"
        "Structure: Open with what the company does and what this role actually is. Spend "
        "the first half on the company context (what they're building, where they are in "
        "their growth arc, what's hard about this domain) and the role specifics (day-to-day, "
        "team structure, what success looks like). Then cover how the candidate's background "
        "maps to the requirements; be specific but matter-of-fact, not flattering. Surface "
        "the trickiest interview angles and genuine gaps. Close with 2-3 concrete things to "
        "remember walking in.\n\n"
        "Tone: Two sharp analysts breaking down an opportunity. Curious and substantive, "
        "not cheerleading. More '60 Minutes' than 'morning show.'"
    ),
    "deep_dive_long": (
        "FORMAT: Deep Dive Extended (~15 minutes, ~4000 words)\n\n"
        "Same structure as the standard Deep Dive but with significantly more depth on each "
        "section. Spend more time on company context (industry landscape, competitors, recent "
        "news, funding/growth stage). Go deeper on role specifics (likely team dynamics, "
        "reporting structure, first-90-days expectations). Cover more interview angles (6-8 "
        "likely questions with suggested approaches). Include a section on questions the "
        "candidate should ask the interviewer.\n\n"
        "Speaker A is the lead host. Speaker B asks probing follow-ups and plays devil's "
        "advocate at least twice.\n\n"
        "Tone: Two sharp analysts doing a thorough breakdown. Substantive and detailed. "
        "Be honest about gaps and unknowns rather than papering over them."
    ),
    "brief": (
        "FORMAT: The Brief (~3 minutes, ~700 words)\n\n"
        "Speaker A delivers a tight, single-speaker-dominant briefing. Speaker B interjects "
        "only 2-3 times with quick clarifying questions.\n\n"
        "Structure: What the company does (one sentence). What the role is (two sentences). "
        "Three things the candidate needs to know walking in: the strongest angle from their "
        "background, the biggest gap to address proactively, and the one question they should "
        "be most prepared for.\n\n"
        "Tone: Crisp and direct, like a well-prepared friend giving you the quick version "
        "in the parking lot before you walk in. No filler, no flattery."
    ),
    "qa_drill": (
        "FORMAT: Q&A Drill (~8 minutes, ~2000 words)\n\n"
        "Speaker A plays a realistic interviewer: asks questions drawn from the JD, "
        "the company's known focus areas, and common objections for this candidate profile. "
        "Speaker B plays a coach who, after each question, walks through how to approach "
        "it; which experience to lead with, what metrics to cite, what to avoid saying.\n\n"
        "Structure: 5-7 questions, progressing from introductory (tell me about yourself / "
        "why this company) through technical/domain (specific to the JD requirements) to "
        "behavioral (leadership, conflict, failure stories). Include at least one question "
        "that probes a genuine gap or weakness. Each Q&A pair should feel like a mini "
        "coaching session with specific, actionable guidance.\n\n"
        "Tone: The interviewer is realistic and occasionally tough. The coach is specific "
        "and practical; never vague encouragement like 'just be yourself.'"
    ),
    "critical_analysis": (
        "FORMAT: Critical Analysis (~8 minutes, ~2000 words)\n\n"
        "Speaker A takes a skeptical hiring manager's perspective: identifies real gaps "
        "between the candidate's background and the JD, surfaces objections that would "
        "come up in a debrief, and stress-tests the resume narrative. Speaker B plays "
        "strategic advisor: for each concern, proposes a concrete reframe with specific "
        "evidence.\n\n"
        "Structure: Open by identifying the 3-4 biggest potential objections (skill gaps, "
        "industry transitions, tenure patterns, missing keywords, overqualification). For "
        "each, Speaker A articulates the concern as a hiring manager would voice it in a "
        "debrief ('my worry is...'), then Speaker B provides a counter-strategy with "
        "specific evidence from the prep materials. Close with an honest assessment of "
        "overall positioning; strong and weak.\n\n"
        "Tone: Constructively adversarial. Speaker A is tough but fair. Speaker B is "
        "strategic and evidence-based; never dismissive of a legitimate concern."
    ),
}


def _generate_podcast_if_configured(
    *,
    prep_folder: str,
    company: str,
    title: str,
    job_id: str,
    jd_text: str,
    briefing: str,
    resume: str,
    cover: str,
    critique: str,
    interview_prep: str,
    cached_prefix: str,
    conn: sqlite3.Connection | None,
) -> None:
    """Auto-generate a Deep Dive podcast if GEMINI_API_KEY is configured.

    Non-fatal: failures log + notify but don't fail the overall run.
    Skips silently when the API key is absent (opt-in via env var).
    """
    if not os.environ.get("GEMINI_API_KEY", "").strip():
        log_event("podcast_skipped", job_id=job_id, reason="no_gemini_api_key")
        return

    try:
        generate_podcast_for_job(
            prep_folder=prep_folder,
            company=company,
            title=title,
            job_id=job_id,
            jd_text=jd_text,
            briefing=briefing,
            resume=resume,
            cover=cover,
            critique=critique,
            interview_prep=interview_prep,
            cached_prefix=cached_prefix,
            podcast_format="deep_dive",
            conn=conn,
        )
    except Exception as exc:  # noqa: BLE001
        log_event(
            "podcast_error",
            job_id=job_id,
            company=company,
            title=title,
            reason=f"{type(exc).__name__}: {exc}",
        )
        ntfy_send(
            f"Podcast failed: {company} — {title}",
            f"{type(exc).__name__}: {exc}",
            kind="podcast_failed",
        )


def generate_podcast_for_job(
    *,
    prep_folder: str,
    company: str,
    title: str,
    job_id: str,
    jd_text: str,
    briefing: str,
    resume: str,
    cover: str,
    critique: str,
    interview_prep: str,
    cached_prefix: str,
    podcast_format: str = "deep_dive",
    focus: str = "",
    conn: sqlite3.Connection | None = None,
) -> str:
    """Generate a podcast for a specific format. Returns the MP3 path.

    Called by the auto-generate path (Deep Dive during interview prep) and
    by the on-demand route handler (any format from the materials page).
    """
    from findajob.llm.tts import generate_podcast  # noqa: PLC0415

    format_instructions = FORMAT_INSTRUCTIONS.get(podcast_format)
    if not format_instructions:
        raise ValueError(f"Unknown podcast format: {podcast_format}")

    study_guide = _read_or_empty(_latest(prep_folder, re.compile(r"Study Guide.*\.md$")))

    cover_section = f"\nCOVER LETTER:\n{cover}\n" if cover else ""
    critique_section = f"\nRECRUITER CRITIQUE:\n{critique}\n" if critique else ""
    study_section = f"\nSTUDY GUIDE:\n{study_guide}\n" if study_guide else ""
    focus_section = f"\nFOCUS:\n{focus}\n" if focus else ""

    script_prompt = (
        f"Company: {company}\nTitle: {title}\n\n"
        f"JOB DESCRIPTION:\n{jd_text}\n\n"
        f"COMPANY BRIEFING:\n{briefing}\n\n"
        f"TAILORED RESUME:\n{resume}\n"
        f"{cover_section}"
        f"{critique_section}"
        f"\nINTERVIEW PREP:\n{interview_prep}\n"
        f"{study_section}"
        f"\nFORMAT INSTRUCTIONS:\n{format_instructions}\n"
        f"{focus_section}"
    )

    log_event(
        "podcast_script_started",
        job_id=job_id,
        company=company,
        title=title,
        format=podcast_format,
    )

    script = run_role(
        "podcast_scriptwriter",
        script_prompt,
        cached_prefix=cached_prefix,
        conn=conn,
        job_id=job_id,
    )

    if not script or len(script) < 200:
        log_event(
            "podcast_script_error",
            job_id=job_id,
            company=company,
            title=title,
            reason="empty_or_short_script",
            chars=len(script) if script else 0,
        )
        ntfy_send(
            f"Podcast script failed: {company} — {title}",
            f"empty_or_short_script ({podcast_format})",
            kind="podcast_failed",
        )
        return ""

    mp3_path = os.path.join(prep_folder, f"interview_prep_podcast_{podcast_format}.mp3")

    generate_podcast(
        script,
        mp3_path,
        speaker_a="Speaker A",
        speaker_b="Speaker B",
        conn=conn,
        job_id=job_id,
        operation=f"podcast_tts_{podcast_format}",
    )

    log_event(
        "podcast_complete",
        job_id=job_id,
        company=company,
        title=title,
        format=podcast_format,
        mp3=os.path.basename(mp3_path),
    )

    ntfy_send(
        f"Podcast ready: {company} — {title} ({podcast_format})",
        mp3_path,
        kind="podcast_ready",
    )

    return mp3_path
