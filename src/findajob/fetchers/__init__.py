"""Job fetching from Greenhouse, RapidAPI (LinkedIn/Indeed), and Gmail."""

import subprocess
import sys
import time

from findajob.audit import log_event
from findajob.classification import JD_MAX_CHARS, strip_jd_boilerplate
from findajob.cleaning import clean_company, clean_title, extract_linkedin_job_id
from findajob.fetchers.adapters._keys import resolve_rapidapi_key
from findajob.paths import IMAGE_ROOT, PANDOC

# Per-call throttle to keep morning triage from bursting past the RapidAPI
# per-minute cap on /v2/linkedin/get. 214-job triage × ~30% LinkedIn ≈ 13s added.
_LINKEDIN_GET_THROTTLE_SEC = 0.2

# Aggregated counters — triage.py resets at run start and emits one
# `linkedin_rate_limited` summary event at run end (issue #223 AC2).
_linkedin_rate_limit_stats: dict[str, int] = {"count": 0, "total_wait": 0}


def reset_linkedin_rate_limit_stats() -> None:
    _linkedin_rate_limit_stats["count"] = 0
    _linkedin_rate_limit_stats["total_wait"] = 0


def get_linkedin_rate_limit_stats() -> dict[str, int]:
    return dict(_linkedin_rate_limit_stats)


# ── JD Fetching ──
def fetch_jd_curl(url):
    """Fetch JD by curling a public URL (Greenhouse/RSS/Lever sources)."""
    try:
        raw = subprocess.run(["curl", "-sL", "--max-time", "10", url], capture_output=True, text=True).stdout
        text = subprocess.run([PANDOC, "-f", "html", "-t", "plain"], input=raw, capture_output=True, text=True).stdout
        return strip_jd_boilerplate(text)[:JD_MAX_CHARS]
    except Exception as e:
        return f"[ERROR fetching JD: {e}]"


def fetch_linkedin_job_data(job_id):
    """
    Fetch full job data via LinkedIn get endpoint.
    Returns {'description': str|None, 'company': str|None, 'title': str|None}.
    LinkedIn job URLs require auth — curling them always returns "Job not found".
    The API get endpoint is the only reliable path.
    """
    import requests as req

    api_key = resolve_rapidapi_key("RAPIDAPI_KEY", "JOBS_API14_KEY")
    if not api_key or not job_id:
        return {"description": None, "company": None, "title": None}
    time.sleep(_LINKEDIN_GET_THROTTLE_SEC)
    url = "https://jobs-api14.p.rapidapi.com/v2/linkedin/get"
    headers = {
        "x-rapidapi-host": "jobs-api14.p.rapidapi.com",
        "x-rapidapi-key": api_key,
    }
    params = {"id": str(job_id)}
    try:
        response = req.get(url, headers=headers, params=params, timeout=15)
        if response.status_code == 429:
            wait = min(int(response.headers.get("Retry-After", "10")), 60)
            _linkedin_rate_limit_stats["count"] += 1
            _linkedin_rate_limit_stats["total_wait"] += wait
            time.sleep(wait)
            response = req.get(url, headers=headers, params=params, timeout=15)
        response.raise_for_status()
        data = response.json()
        if data.get("hasError"):
            log_event("linkedin_get_error", job_id=job_id, errors=data.get("errors"))
            return {"description": None, "company": None, "title": None}
        payload = data.get("data", {})
        description = payload.get("description", "") or ""
        # Company name field varies across API versions — try all known keys
        company = (
            payload.get("companyName")
            or payload.get("company")
            or payload.get("organizationName")
            or (payload.get("hiringOrganization") or {}).get("name")
            or ""
        )
        title = payload.get("title", "") or ""
        return {
            "description": strip_jd_boilerplate(description)[:JD_MAX_CHARS] if description else None,
            "company": clean_company(company) if company else None,
            "title": clean_title(title) if title else None,
        }
    except Exception as e:
        log_event("linkedin_get_error", job_id=job_id, error=str(e))
        return {"description": None, "company": None, "title": None}


def fetch_jd(job):
    """
    Fetch JD text for a job dict. Strategy by source:
      - jobsapi_indeed:   inline description already in job dict from search response
      - jobsapi_linkedin: call /v2/linkedin/get using stored api_id
      - gmail_linkedin:   call /v2/linkedin/get using api_id extracted from URL
                          (company enrichment handled separately in main)
      - everything else:  curl the URL (Greenhouse, Lever, other Gmail sources)
    """
    source = job.get("source", "")

    if source == "jobsapi_indeed":
        desc = job.get("description", "")
        if desc and len(desc.strip()) > 30:
            return strip_jd_boilerplate(desc)[:JD_MAX_CHARS]
        # No inline description — do NOT curl; Indeed apply URLs are JS-rendered SPAs
        # that always return unusable content. Return sentinel instead.
        return "[No description available]"

    if source == "greenhouse_json":
        desc = job.get("description", "")
        if desc and len(desc.strip()) > 30:
            try:
                plain = subprocess.run(
                    [PANDOC, "-f", "html", "-t", "plain"], input=desc, capture_output=True, text=True, timeout=10
                ).stdout
                plain = strip_jd_boilerplate(plain)[:JD_MAX_CHARS]
                return plain if plain.strip() else "[No description available]"
            except Exception:
                return strip_jd_boilerplate(desc)[:JD_MAX_CHARS]
        return "[No description available]"

    if source in ("ashby_json", "lever_json"):
        desc = job.get("description", "")
        if desc and len(desc.strip()) > 30:
            try:
                plain = subprocess.run(
                    [PANDOC, "-f", "html", "-t", "plain"], input=desc, capture_output=True, text=True, timeout=10
                ).stdout
                plain = strip_jd_boilerplate(plain)[:JD_MAX_CHARS]
                return plain if plain.strip() else "[No description available]"
            except Exception:
                return strip_jd_boilerplate(desc)[:JD_MAX_CHARS]
        return "[No description available]"

    if source in ("jobsapi_linkedin", "gmail_linkedin"):
        api_id = job.get("api_id", "")
        if api_id:
            result = fetch_linkedin_job_data(api_id)
            # Cache resolved company/title in job dict so the main loop can use them
            # without a second API call. Company cache covers the blank-company case;
            # title cache covers the degenerate-title case (#656 — Android share-flow
            # leaves the bare URL as anchor text).
            if source == "gmail_linkedin":
                if result.get("company"):
                    job["_linkedin_company"] = result["company"]
                if result.get("title"):
                    job["_linkedin_title"] = result["title"]
            if result["description"]:
                return result["description"]
        log_event("linkedin_jd_missing", title=job.get("title"), api_id=api_id)
        return "[LinkedIn JD unavailable — no api_id or get request failed]"

    url = job.get("url", "")
    if url:
        return fetch_jd_curl(url)

    return "[No URL available]"


# ── Gmail Ingestion ──


def _normalize_sender_to_source(sender: str, url: str = "") -> str:
    """Map an IMAP sender (and fallback URL) to a findajob source string.

    Sender is the direct ground truth from the IMAP envelope; URL is the
    fallback when the sender domain isn't in the known map. Returns
    "gmail_unknown" if neither resolves.
    """
    sender_lc = (sender or "").lower()
    if "linkedin.com" in sender_lc:
        return "gmail_linkedin"
    if "indeed" in sender_lc:
        return "gmail_indeed"
    if "ziprecruiter" in sender_lc:
        return "gmail_ziprecruiter"
    if "@google.com" in sender_lc or "careers-noreply" in sender_lc:
        return "gmail_google"
    # URL fallback — same patterns the parser uses
    url_lc = (url or "").lower()
    if "linkedin.com" in url_lc or "lnkd.in" in url_lc:
        return "gmail_linkedin"
    if "indeed.com" in url_lc:
        return "gmail_indeed"
    if "ziprecruiter.com" in url_lc:
        return "gmail_ziprecruiter"
    if "google.com" in url_lc:
        return "gmail_google"
    return "gmail_unknown"


def notify_send_raw(text: str, kind: str = "gmail_auth_failure") -> None:
    """Thin wrapper for ntfy notifications. Module-level for monkeypatching in tests.

    Splits `text` into title + body around the first newline so the existing
    notify.py CLI contract is satisfied (it requires title+body positional args).
    """
    title, _, body = text.partition("\n")
    if not body:
        body = title
    subprocess.run(
        [sys.executable, f"{IMAGE_ROOT}/scripts/notify.py", "send-raw", title, body, "--kind", kind],
        check=False,
        timeout=10,
    )


def _extract_jobs_from_html(html_content: str) -> list[dict]:
    """Shared HTML→jobs extractor. Used by parse_jobs_from_email_imap.

    Handles BeautifulSoup parsing, anchor extraction, SKIP_LABELS filtering,
    JOB_URL_PATTERNS source tagging, title/company heuristics, and URL
    deduplication. The Gmail-API variant of this function was deleted in #330
    — IMAP is now the only source of email-derived jobs.
    """
    from bs4 import BeautifulSoup

    if not html_content:
        return []

    soup = BeautifulSoup(html_content, "html.parser")
    jobs: list[dict] = []
    seen_urls: set[str] = set()

    SKIP_LABELS = {
        "view job",
        "apply",
        "apply now",
        "see job",
        "learn more",
        "view",
        "click here",
        "unsubscribe",
        "manage alerts",
        "view all jobs",
        "see all jobs",
        "update preferences",
        "privacy policy",
        "terms",
        "help",
        "contact us",
        "settings",
        "opt out",
        "manage email",
        "see more jobs",
        "view more jobs",
        "all jobs",
    }

    JOB_URL_PATTERNS = [
        ("linkedin.com/jobs", "gmail_linkedin"),
        ("linkedin.com/comm/jobs", "gmail_linkedin"),
        ("lnkd.in/", "gmail_linkedin"),
        ("indeed.com/viewjob", "gmail_indeed"),
        ("indeed.com/rc/clk", "gmail_indeed"),
        ("indeed.com/pagead", "gmail_indeed"),
        ("r.indeed.com", "gmail_indeed"),
        ("ziprecruiter.com/jobs", "gmail_ziprecruiter"),
        ("ziprecruiter.com/c/", "gmail_ziprecruiter"),
        ("careers.google.com", "gmail_google"),
        ("google.com/about/careers", "gmail_google"),
    ]

    for a in soup.find_all("a", href=True):
        href = str(a["href"])
        # LinkedIn/Indeed emails often pack "Title\nCompany" in one <a> tag.
        # Split on newline first so company doesn't get concatenated into title.
        raw_text = a.get_text(separator="\n", strip=True)
        text_lines = [part.strip() for part in raw_text.split("\n") if part.strip()]
        title = clean_title(text_lines[0]) if text_lines else ""
        anchor_company = text_lines[1] if len(text_lines) > 1 else ""  # may be overridden below

        if not title or len(title) < 6 or title.lower() in SKIP_LABELS:
            continue
        # Skip LinkedIn digest subject lines misread as job titles
        title_lower = title.lower()
        if (
            title_lower.startswith("jobs similar to")
            or title_lower.startswith("jobs at ")
            or title_lower.startswith("jobs in ")
        ):
            continue
        if href in seen_urls:
            continue
        if len(title) > 140:
            continue

        source = None
        for pattern, src in JOB_URL_PATTERNS:
            if pattern in href:
                source = src
                break

        if not source:
            continue

        company = ""
        parent = a.find_parent()
        if parent:
            for sib in parent.find_next_siblings(limit=4):
                txt = sib.get_text(strip=True)
                if txt and 6 < len(txt) < 120 and txt.lower() not in SKIP_LABELS:
                    company = txt
                    break
            if not company:
                full_text = parent.get_text(separator=" ", strip=True)
                parts = full_text.split(title, 1)
                if len(parts) > 1:
                    candidate = parts[1].strip().split("\n")[0][:100].strip()
                    if candidate and candidate.lower() not in SKIP_LABELS:
                        company = candidate
        # Last resort: use the second line of anchor text (stripped of skip labels)
        if not company and anchor_company and anchor_company.lower() not in SKIP_LABELS:
            company = anchor_company

        company = clean_company(company)
        job_dict = {"title": title, "company": company, "url": href, "location": "", "source": source}
        # For LinkedIn URLs, extract job ID so fetch_jd can use the API path
        if source == "gmail_linkedin":
            api_id = extract_linkedin_job_id(href)
            if api_id:
                job_dict["api_id"] = api_id
        jobs.append(job_dict)
        seen_urls.add(href)

    return jobs


def parse_jobs_from_email_imap(message) -> list[dict]:
    """Walk an :class:`email.message.Message` and extract job rows.

    Iterates the MIME tree for ``text/html`` parts, decodes them, and hands
    the concatenated HTML to :func:`_extract_jobs_from_html`. Plain-text-only
    messages return an empty list.
    """
    html_parts: list[str] = []
    if message.is_multipart():
        for part in message.walk():
            if part.get_content_type() == "text/html":
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    try:
                        html_parts.append(payload.decode(charset, errors="ignore"))
                    except (LookupError, UnicodeDecodeError):
                        html_parts.append(payload.decode("utf-8", errors="ignore"))
    else:
        if message.get_content_type() == "text/html":
            payload = message.get_payload(decode=True)
            if payload:
                charset = message.get_content_charset() or "utf-8"
                try:
                    html_parts.append(payload.decode(charset, errors="ignore"))
                except (LookupError, UnicodeDecodeError):
                    html_parts.append(payload.decode("utf-8", errors="ignore"))

    return _extract_jobs_from_html("".join(html_parts))
