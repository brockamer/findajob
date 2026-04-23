"""Job title/company cleaning, normalization, and fingerprinting."""

import hashlib
import re

# ── Normalization & Dedup ──
ABBREVIATIONS: dict[str, str] = {
    r"\bsr\.?\b": "senior",
    r"\bjr\.?\b": "junior",
    r"\bmgr\.?\b": "manager",
    r"\bdir\.?\b": "director",
    r"\beng\.?\b": "engineer",
    r"\bengr\.?\b": "engineer",
    r"\bops\.?\b": "operations",
    r"\binfra\.?\b": "infrastructure",
    r"\bvp\b": "vice president",
    r"\bsvp\b": "senior vice president",
    r"\bhw\b": "hardware",
    r"\bsw\b": "software",
    r"\bdc\b": "data center",
    r"\bmfg\b": "manufacturing",
    r"\bpgm\b": "program",
    r"\btpm\b": "technical program manager",
}


def normalize(text: str) -> str:
    text = text.lower().strip()
    for pattern, replacement in ABBREVIATIONS.items():
        text = re.sub(pattern, replacement, text)
    text = re.sub(r"[^a-z0-9 ]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


# Parenthetical work-mode tags LinkedIn appends to location: "(On-site)",
# "(Remote)", "(Hybrid)". Strip before normalizing so re-ingests with/without
# the tag produce stable fingerprints (#182 Bug B).
_LOC_WORK_MODE_RE: re.Pattern[str] = re.compile(r"\s*\((?:on[- ]?site|remote|hybrid)\)\s*", re.IGNORECASE)

# Trailing country suffixes: ", United States" / ", US" / ", Canada" / ", UK".
# LinkedIn appends these inconsistently — strip so fingerprints stay stable.
_LOC_TRAILING_COUNTRY_RE: re.Pattern[str] = re.compile(
    r",\s*(?:united\s+states|usa?|canada|uk|united\s+kingdom)\s*$", re.IGNORECASE
)

# Tokens that by themselves indicate a coarse (country-level or unknown)
# location — used by is_coarse_location().
#
# Known trade-off: "Remote, US" and "Remote, Canada" both normalize to
# "remote" and produce identical fingerprints, so cross-border remote
# postings of the same (company, title) collapse into one row. Acceptable
# for a US-based candidate; cross-border work-authorization cases would
# need a richer location model to preserve distinctness.
_COARSE_LOCATION_TOKENS: frozenset[str] = frozenset(
    {"", "us", "usa", "united states", "canada", "uk", "united kingdom", "eu", "europe", "remote"}
)


def normalize_location(location: str) -> str:
    """Canonicalize a location string for fingerprinting.

    Strips LinkedIn work-mode parentheticals and trailing country suffixes
    that vary across ingest runs. Returns the lowercase normalized form.
    """
    if not location:
        return ""
    s = _LOC_WORK_MODE_RE.sub(" ", location)
    s = _LOC_TRAILING_COUNTRY_RE.sub("", s)
    return normalize(s)


def is_coarse_location(location: str) -> bool:
    """True if location is country-level or empty — triggers Tier 2 loose dedup.

    Specific city-level locations ("Barstow, TX", "Menlo Park, CA") are NOT
    coarse, so distinct-location reqs (e.g., site managers in different cities)
    still produce distinct fingerprints.
    """
    return normalize_location(location) in _COARSE_LOCATION_TOKENS


def fingerprint(title: str, company: str, location: str = "") -> str:
    """Tier 1 strict fingerprint — exact location match required."""
    key = normalize(title) + "|" + normalize(company) + "|" + normalize_location(location)
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def loose_fingerprint(title: str, company: str) -> str:
    """Tier 2 loose fingerprint — (title, company) only, for cross-source
    syndication where one side has a coarse location (#182 Bug C)."""
    key = normalize(title) + "|" + normalize(company)
    return hashlib.sha256(key.encode()).hexdigest()[:16]


# ── Title Cleaning ──
# Job boards (especially Indeed via Jobs API) append metadata directly to the title field:
# board name, location, salary, time-ago, badges. Strip everything after these markers.
_TITLE_SPLIT_PATTERNS: re.Pattern[str] = re.compile(
    r"(?:"
    r"Jobs via \w[\w ]*·"  # "Jobs via Dice ·"
    r"|\bvia \w[\w ]*·"  # "via LinkedIn ·"
    r"|\s·\s"  # generic " · " separator
    r"|\s[-–]\s(?:Remote|Hybrid|On-?site|Contract|Full.?time|Part.?time)"
    r"|\$[\d,]+[Kk]?\s*[-–]"  # salary range start "$140K -"
    r"|\d+\s*(?:hour|day|week|month)s?\s+ago"  # "2 days ago"
    r"|(?:Easy|Quick)\s+Apply"
    r"|Actively\s+recruiting"
    r"|Fast\s+growing"
    r")",
    re.IGNORECASE,
)


def clean_title(raw_title: str) -> str:
    """Strip job board metadata appended to title field by Indeed/Jobs API.

    Also collapses all whitespace runs (including NBSP U+00A0) to single
    spaces and strips leading/trailing whitespace — str.strip() with no
    args misses NBSP (#182 Bug A).
    """
    m = _TITLE_SPLIT_PATTERNS.search(raw_title)
    if m:
        raw_title = raw_title[: m.start()]
    # Collapse any-whitespace runs (incl. NBSP) to a single space, then strip
    # both whitespace and the board-separator chars that may remain at edges.
    collapsed = re.sub(r"\s+", " ", raw_title, flags=re.UNICODE)
    return collapsed.strip(" ·-–\t\xa0")


# Company field from LinkedIn API often has location/metadata appended:
# "Google – Multiple Sites4 days ago", "Google · Sunnyvale, CA, US 12 connections"
_COMPANY_SPLIT_PATTERNS: re.Pattern[str] = re.compile(
    r"(?:"
    r"\s[·–—-]\s"  # " · " or " – " separator before location
    r"|\d+\s+connections?"  # "12 connections"
    r"|\d+\s*(?:hour|day|week|month)s?\s+ago"  # "3 days ago"
    r"|(?:Easy|Quick)\s+Apply"
    r"|Actively\s+recruiting"
    r"|,\s*[A-Z][a-z]+,\s*(?:United States|US|Canada|UK)"  # ", Sunnyvale, United States"
    r")",
    re.IGNORECASE,
)


def clean_company(raw_company: str) -> str:
    """Strip location/metadata appended to company field by LinkedIn/Indeed API."""
    if not raw_company:
        return ""
    m = _COMPANY_SPLIT_PATTERNS.search(raw_company)
    if m:
        raw_company = raw_company[: m.start()]
    return raw_company.strip(" ·-–,")


# Regex to extract numeric LinkedIn job ID from job URLs
# Matches: linkedin.com/jobs/view/1234567890 and linkedin.com/comm/jobs/view/1234567890
_LINKEDIN_JOB_ID_RE: re.Pattern[str] = re.compile(r"linkedin\.com/(?:comm/)?jobs/view/(\d+)", re.IGNORECASE)


def extract_linkedin_job_id(url: str | None) -> str | None:
    """Extract numeric job ID from a LinkedIn job URL. Returns str or None."""
    m = _LINKEDIN_JOB_ID_RE.search(url or "")
    return m.group(1) if m else None
