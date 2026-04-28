#!/usr/bin/env python3
"""Shared utilities for the JobSearchPipeline."""

import json
import os
import re
import shutil
import sqlite3
from datetime import UTC, datetime
from typing import Any

from findajob.paths import BASE

LOG_PATH: str = f"{BASE}/logs/pipeline.jsonl"

# ── Logging ──────────────────────────────────────────────────────────────────


def log_event(event_type: str, **kwargs: object) -> None:
    entry = {"ts": datetime.now(UTC).isoformat(), "event": event_type, **kwargs}
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    with open(LOG_PATH, "a") as f:
        f.write(json.dumps(entry) + "\n")


# ── Audit log ────────────────────────────────────────────────────────────────


def write_audit(
    conn: sqlite3.Connection,
    job_id: str,
    field_changed: str,
    old_value: object,
    new_value: object,
) -> None:
    conn.execute(
        "INSERT INTO audit_log (job_id, field_changed, old_value, new_value) VALUES (?, ?, ?, ?)",
        (job_id, field_changed, str(old_value) if old_value is not None else None, str(new_value)),
    )
    conn.commit()


def quarantine_stale_prep_folders(
    conn: sqlite3.Connection,
    companies_dir: str,
    folder_prefix: str,
    current_folder_name: str,
) -> list[str]:
    """Move abandoned prep folders matching ``folder_prefix`` into ``companies_dir/.stale/``.

    Each prep run mints a fresh ``{company}_{title}_{date}_{HHMMSS}`` folder but
    only the latest is written to ``jobs.prep_folder_path``. Regenerate clicks,
    concurrent prep races, or failed runs that never promoted to
    ``materials_drafted`` leave older folders orphaned on disk (#174).

    Called at prep start, before the new folder is created. Only folders whose
    name starts with ``folder_prefix`` are considered. A folder is **kept** if:
      * its basename equals ``current_folder_name`` (this run's folder), or
      * its absolute path appears as ``prep_folder_path`` on any jobs row, or
      * its name starts with ``_`` (stage holding directories:
        ``_applied``, ``_rejected``, ``_waitlisted``), or
      * its name equals ``.stale``, or
      * it is a regular file, not a directory.

    Everything else is moved into ``companies_dir/.stale/``. Rather than
    ``rmtree`` — which would destroy data if a concurrent prep is still writing
    — quarantining is reversible. Name collisions inside ``.stale/`` are
    disambiguated with a short random suffix. Returns the list of moved
    basenames (empty if nothing matched).
    """
    try:
        entries = os.listdir(companies_dir)
    except FileNotFoundError:
        return []

    tracked: set[str] = {
        row[0]
        for row in conn.execute(
            "SELECT prep_folder_path FROM jobs WHERE prep_folder_path IS NOT NULL AND prep_folder_path != ''"
        ).fetchall()
    }

    stale_dir = os.path.join(companies_dir, ".stale")
    moved: list[str] = []
    for entry in entries:
        if entry == current_folder_name or entry == ".stale" or entry.startswith("_"):
            continue
        if not entry.startswith(folder_prefix):
            continue
        src = os.path.join(companies_dir, entry)
        if not os.path.isdir(src):
            continue
        if src in tracked:
            continue
        os.makedirs(stale_dir, exist_ok=True)
        dest = os.path.join(stale_dir, entry)
        if os.path.exists(dest):
            dest = os.path.join(stale_dir, f"{entry}_{os.urandom(2).hex()}")
        shutil.move(src, dest)
        moved.append(entry)

    if moved:
        log_event("stale_prep_folders_quarantined", folders=moved, kept=current_folder_name)
    return moved


# ── Environment loading ──────────────────────────────────────────────────────


def load_env(path: str | None = None) -> dict[str, str]:
    """Load key=value pairs from a .env file into os.environ. Returns dict."""
    if path is None:
        path = f"{BASE}/data/.env"
    env = {}
    try:
        with open(os.path.expanduser(path)) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, val = line.partition("=")
                    key = key.strip()
                    val = val.strip().strip("'\"")
                    os.environ[key] = val
                    env[key] = val
    except FileNotFoundError:
        pass
    return env


# ── LLM JSON validation ─────────────────────────────────────────────────────


def extract_json_payload(raw_output: str) -> str:
    """Pull the JSON payload out of an LLM response that may wrap it in
    markdown fences, prose, or both.

    Handles, in order:
    1. Whole response is a fenced block (```...```, possibly with a
       language tag like ```json). Strip the fences.
    2. Response contains a fenced ```json…``` block somewhere inside
       prose. Return the contents of the first such block.
    3. Response has prose before the JSON. Find the first '{' or '[',
       return the substring from there onward (the parser will reject
       trailing prose only if it's also non-JSON, which is fine — we
       optimize for prose-before-JSON, the observed failure mode).
    4. Otherwise return the input unchanged.

    This is best-effort recovery for scorer responses that drift from
    "JSON only" in the prompt despite explicit instructions; the parser
    that consumes the output still gates on `json.loads` + schema
    validation.
    """
    text = raw_output.strip()

    # Case 1: whole-response fenced block
    if text.startswith("```"):
        # Drop the opening fence line (which may carry a language tag
        # like "```json"), then strip a trailing fence if present.
        first_newline = text.find("\n")
        if first_newline != -1:
            inner = text[first_newline + 1 :]
            if inner.rstrip().endswith("```"):
                inner = inner.rstrip()[: -len("```")]
            return inner.strip()

    # Case 2: fenced block embedded in prose. Match the first ```json
    # or ``` (with optional lang tag) ... ``` block.
    fence_match = _FENCED_JSON_RE.search(text)
    if fence_match:
        return fence_match.group(1).strip()

    # Case 3: bare JSON preceded by prose. Anchor at the first { or [
    # that opens a JSON value.
    for opener in ("{", "["):
        idx = text.find(opener)
        if idx > 0:  # strictly prose-before; idx == 0 already parses
            return text[idx:].strip()

    return text


_FENCED_JSON_RE = re.compile(r"```(?:json|JSON)?\s*\n(.*?)\n```", re.DOTALL)


def validate_llm_json(raw_output: str, schema_path: str) -> tuple[dict | None, str | None]:
    import jsonschema

    text = extract_json_payload(raw_output)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as e:
        return None, f"JSON parse: {e}"
    try:
        with open(schema_path) as f:
            schema = json.load(f)
        jsonschema.validate(parsed, schema)
    except jsonschema.ValidationError as e:
        return None, f"Schema: {e.message}"
    return parsed, None


# ── JD quality check ─────────────────────────────────────────────────────────

_JD_WALL_SIGNALS: list[str] = [
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


def jd_is_usable(jd_text: str | None) -> bool:
    if not jd_text or len(jd_text.strip()) < 30:
        return False
    lower = jd_text.lower()
    return not any(s in lower for s in _JD_WALL_SIGNALS)


# ── Candidate name / file prefix helpers ─────────────────────────────────────

_PROFILE_NAME_RE: re.Pattern[str] = re.compile(
    r"^\s*\*{0,2}\s*Name:\s*\*{0,2}\s*(.+?)\s*\*{0,2}\s*$",
    re.IGNORECASE,
)
_PROFILE_FILE_PREFIX_RE: re.Pattern[str] = re.compile(
    r"^\s*\*{0,2}\s*File\s*Prefix:\s*\*{0,2}\s*(.+?)\s*\*{0,2}\s*$",
    re.IGNORECASE,
)


def _clean_profile_field(raw: str | None) -> str:
    """Strip surrounding whitespace, asterisks, and backticks from a profile field value."""
    return (raw or "").strip().strip("*").strip("`").strip()


def read_candidate_name(profile_path: str | None = None) -> str:
    """Read the candidate's full name from profile.md.

    Prefers an explicit `Name: Xxx Yyy` line (from the Identity section).
    Tolerates `**Name:** Xxx Yyy` (bold markdown) and similar variants.
    Returns 'Candidate' if nothing matches.
    """
    if profile_path is None:
        profile_path = f"{BASE}/candidate_context/profile.md"
    try:
        with open(profile_path) as f:
            for line in f:
                m = _PROFILE_NAME_RE.match(line)
                if m:
                    value = _clean_profile_field(m.group(1))
                    if value:
                        return value
    except (FileNotFoundError, OSError):
        pass
    return "Candidate"


def read_file_prefix(profile_path: str | None = None) -> str:
    """Read the prefix used in generated filenames.

    Prefers an explicit `File Prefix: Xxx` line (from profile.md). Falls back
    to the last word of the candidate's name (from `Name:`). Returns 'Candidate'
    if neither is available.
    """
    if profile_path is None:
        profile_path = f"{BASE}/candidate_context/profile.md"
    try:
        with open(profile_path) as f:
            for line in f:
                m = _PROFILE_FILE_PREFIX_RE.match(line)
                if m:
                    value = _clean_profile_field(m.group(1))
                    if value:
                        return value
    except (FileNotFoundError, OSError):
        pass

    name = read_candidate_name(profile_path)
    parts = name.strip().split()
    return parts[-1] if parts else "Candidate"


_UNSAFE_FNAME_CHARS: re.Pattern[str] = re.compile(r"[^\w\s\-&.,]")


def safe_filename_part(s: str | None, max_len: int = 80) -> str:
    """Sanitize a string for use as a filename component.

    Keeps word characters, spaces, hyphens, ampersands, periods, and commas.
    Collapses whitespace. Truncates to max_len. Strips trailing punctuation
    that would look odd at a word boundary.
    """
    s = _UNSAFE_FNAME_CHARS.sub("", s or "")
    s = re.sub(r"\s+", " ", s).strip()
    if len(s) > max_len:
        s = s[:max_len].rstrip()
    return s.rstrip(" .-,")


def build_prep_filenames(company: str, title: str, timestamp_fn: str, file_prefix: str) -> dict[str, str]:
    """Return a dict of {logical_name: filename} for a prep folder.

    Naming convention:
      {Prefix} Resume - {Company} - {Title} - {YYYYMMDD-HHMMSS}.{md,docx}
      {Prefix} Cover - {Company} - {Title} - {YYYYMMDD-HHMMSS}.{md,docx}
      {Prefix} Briefing - {Company} - {Title} - {YYYYMMDD-HHMMSS}.{md,docx}
      {Prefix} Resume Changes - {Company} - {Title} - {YYYYMMDD-HHMMSS}.md
      {Prefix} Critique - {Company} - {Title} - {YYYYMMDD-HHMMSS}.md
      JD - {Company} - {Title}.txt
      Review Checklist - {Company} - {Title}.md

    Outreach filenames are generated separately by find_contacts.py.
    """
    co = safe_filename_part(company, 40)
    t = safe_filename_part(title, 60)
    # Core user-facing docs: full pattern with timestamp
    resume_base = f"{file_prefix} Resume - {co} - {t} - {timestamp_fn}"
    cover_base = f"{file_prefix} Cover - {co} - {t} - {timestamp_fn}"
    briefing_base = f"{file_prefix} Briefing - {co} - {t} - {timestamp_fn}"
    changes_base = f"{file_prefix} Resume Changes - {co} - {t} - {timestamp_fn}"
    critique_base = f"{file_prefix} Critique - {co} - {t} - {timestamp_fn}"
    # Internal reference docs: short form, no prefix or timestamp
    jd_base = f"JD - {co} - {t}"
    checklist_base = f"Review Checklist - {co} - {t}"
    return {
        "resume_md": f"{resume_base}.md",
        "resume_docx": f"{resume_base}.docx",
        "cover_md": f"{cover_base}.md",
        "cover_docx": f"{cover_base}.docx",
        "briefing_md": f"{briefing_base}.md",
        "briefing_docx": f"{briefing_base}.docx",
        "changes_md": f"{changes_base}.md",
        "critique_md": f"{critique_base}.md",
        "jd_txt": f"{jd_base}.txt",
        "checklist_md": f"{checklist_base}.md",
    }


# ── Ingest noise filters ─────────────────────────────────────────────────────

# Job-board aggregators / recruiting firms whose "company" field is the board
# or the recruiter, not the actual employer. Jobs from these sources are
# effectively useless without knowing the real hiring company — the candidate
# cannot research culture, target specific contacts, or tailor outreach.
# Filtered at both ingest time (triage.py) and prep-trigger time (board_actions.py).
AGGREGATOR_PREFIXES: tuple[str, ...] = (
    "jobs via ",
    "job via ",
    "posted via ",
    "staffmark",
    "adecco",
    "manpower",
    "randstad",
    "insight global",
    "robert half",
    "kforce",
    "dice",
)


def is_aggregator_company(company: str | None) -> bool:
    """Return True if the company field looks like an aggregator / recruiter wrapper."""
    if not company:
        return False
    c = company.strip().lower()
    return any(c.startswith(prefix) for prefix in AGGREGATOR_PREFIXES)


def is_valid_company(company: str | None) -> bool:
    """Return False if company is blank OR a known aggregator / job-board wrapper."""
    if not company or not company.strip():
        return False
    return not is_aggregator_company(company)


def is_ingest_noise_title(title: str | None) -> bool:
    """Return True if the title looks like a LinkedIn UI element, not an actual job posting.

    The LinkedIn API occasionally returns recommendations-carousel items
    ("Jobs similar to X") as if they were real postings. These have mangled
    field semantics — the 'title' is the UI label, and the 'company' is
    typically the actual job title with "at Company Name" appended.
    """
    if not title:
        return False
    t = title.strip().lower()
    if t.startswith("jobs similar"):
        return True
    if t == "job similar to":
        return True
    return False


def build_outreach_filename(contact_name: str, company: str, timestamp_fn: str, file_prefix: str) -> str:
    """Return filename for an outreach draft.

    Pattern: {Prefix} Outreach to {Contact Name} - {Company} - {YYYYMMDD-HHMMSS}.txt
    """
    co = safe_filename_part(company, 40)
    ct = safe_filename_part(contact_name, 40)
    return f"{file_prefix} Outreach to {ct} - {co} - {timestamp_fn}.txt"


# ── Voice samples ─────────────────────────────────────────────────────────


def load_voice_samples(samples_dir: str | None = None, max_chars: int = 32000) -> str:
    """Concatenate candidate voice samples for style calibration.

    Reads .md and .txt files from candidate_context/voice_samples/ (excluding
    README*), joins with double-newline separators, caps at max_chars. Returns
    empty string when the directory is missing, contains no samples, or only
    contains README files.
    """
    if samples_dir is None:
        samples_dir = f"{BASE}/candidate_context/voice_samples"
    if not os.path.isdir(samples_dir):
        return ""

    parts: list[str] = []
    for name in sorted(os.listdir(samples_dir)):
        if name.lower().startswith("readme"):
            continue
        if not (name.endswith(".md") or name.endswith(".txt")):
            continue
        path = os.path.join(samples_dir, name)
        try:
            with open(path) as f:
                content = f.read().strip()
        except OSError:
            continue
        if content:
            parts.append(content)

    if not parts:
        return ""

    text = "\n\n".join(parts)
    if len(text) > max_chars:
        text = text[:max_chars]
    return text


# ── JD boilerplate stripping ───────────────────────────────────────────────

JD_MAX_CHARS: int = 16000

_BOILERPLATE_PATTERNS: list[str] = [
    # EEO
    r"equal\s+opportunity\s+employer",
    r"equal\s+employment\s+opportunity",
    r"we\s+do\s+not\s+discriminate",
    r"without\s+regard\s+to\s+race",
    r"affirmative\s+action",
    r"all\s+qualified\s+applicants\s+will\s+receive\s+consideration",
    # Legal / compliance
    r"reasonable\s+accommodation",
    r"e-verify",
    r"employment\s+eligibility\s+verification",
    r"right\s+to\s+work",
    r"protected\s+veteran",
    r"drug[- ]free\s+workplace",
    # Disclaimers
    r"this\s+(?:job\s+)?posting\s+is\s+not",
    r"salary\s+ranges?\s+may\s+vary",
    r"the\s+above\s+is\s+intended\s+to\s+describe",
    r"nothing\s+in\s+this\s+job\s+(?:posting|description)",
    r"this\s+(?:job\s+)?description\s+(?:is\s+not|does\s+not)",
    # Application boilerplate
    r"how\s+to\s+apply",
    r"to\s+apply,?\s+please",
    r"apply\s+now\s+at",
    # Benefits headers (start-of-paragraph)
    r"^benefits\s*:",
    r"^what\s+we\s+offer\s*:",
    r"^our\s+benefits\s+include",
    r"^perks\s+(?:&|and)\s+benefits",
    r"^total\s+rewards",
    r"^compensation\s+(?:&|and)\s+benefits",
]

_BOILERPLATE_RE: re.Pattern[str] = re.compile("|".join(_BOILERPLATE_PATTERNS), re.IGNORECASE | re.MULTILINE)


def is_synthetic_job(job: Any) -> bool:
    """Return True when this row represents a speculative (cold-outreach) job.

    Driven by the ``jobs.synthetic`` column, which is set to 1 by the speculative
    approver and 0 (default) for all real postings. Treat any truthy value
    (1, "1") as synthetic; absence or 0 means real.
    """
    if not job:
        return False
    val = job.get("synthetic") if hasattr(job, "get") else None
    if val is None:
        # sqlite3.Row supports __getitem__ but not .get(); fall back.
        try:
            val = job["synthetic"]
        except (KeyError, IndexError, TypeError):
            return False
    return bool(int(val)) if val is not None else False


def strip_jd_boilerplate(text: str | None) -> str:
    """Remove trailing EEO/legal/benefits boilerplate from JD text.

    Works backwards from the end, paragraph by paragraph. Stops trimming
    when a paragraph doesn't match any boilerplate pattern. Never removes
    more than 40% of the text or drops below 200 chars retained.
    """
    if not text or len(text) < 200:
        return text or ""

    # Split into paragraphs on double-newline or blank lines
    paragraphs = re.split(r"\n\s*\n", text)
    if len(paragraphs) <= 1:
        return text  # single block — don't risk stripping it

    original_len = len(text)
    min_retain = max(200, int(original_len * 0.6))  # never strip more than 40%

    # Walk backwards, marking trailing boilerplate paragraphs for removal
    trim_from = len(paragraphs)  # index to trim from (exclusive of kept content)
    for i in range(len(paragraphs) - 1, 0, -1):  # never trim paragraph 0
        para = paragraphs[i].strip()
        if not para:
            continue  # skip empty paragraphs
        if _BOILERPLATE_RE.search(para):
            trim_from = i
        else:
            break  # hit real content — stop trimming

    if trim_from >= len(paragraphs):
        return text  # nothing to trim

    kept = "\n\n".join(paragraphs[:trim_from]).rstrip()

    if len(kept) < min_retain:
        return text  # safety: would remove too much

    chars_removed = original_len - len(kept)
    if chars_removed > 0 and chars_removed / original_len > 0.30:
        log_event(
            "jd_boilerplate_warning",
            removed_pct=round(chars_removed / original_len * 100, 1),
            original_len=original_len,
            kept_len=len(kept),
        )

    return kept
