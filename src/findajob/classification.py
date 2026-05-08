"""Boolean classifiers + JD text quality / boilerplate filters.

Pure-function predicates the pipeline uses to classify rows at ingest
and to filter JD text before scoring. Six functions:

- :func:`is_aggregator_company` / :func:`is_valid_company` —
  recruiter / job-board wrapper detection at ingest time.
- :func:`is_ingest_noise_title` — LinkedIn UI carousel artifacts that
  look like postings but aren't.
- :func:`is_synthetic_job` — speculative cold-outreach flag (#131).
- :func:`jd_is_usable` — JD-text quality gate before LLM scoring.
- :func:`strip_jd_boilerplate` — EEO / legal / benefits trim.

Plus :data:`JD_MAX_CHARS` — the cap applied after stripping.

Extracted from ``utils.py`` in M4.E2.I2 (#550). No logic changes.
"""

from __future__ import annotations

import re
from typing import Any

from findajob.audit import log_event

# ── JD quality check ────────────────────────────────────────────────

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


# ── Ingest-noise filters ───────────────────────────────────────────

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


# ── JD boilerplate stripping ───────────────────────────────────────

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
