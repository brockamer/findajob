"""Job fetching from Greenhouse, RapidAPI (LinkedIn/Indeed), and Gmail."""

import html
import os
import re
import subprocess
import sys
import time

from findajob.cleaning import clean_company, clean_title, extract_linkedin_job_id
from findajob.paths import BASE, PANDOC
from findajob.utils import JD_MAX_CHARS, log_event, strip_jd_boilerplate

GMAIL_CREDS = f"{BASE}/config/gmail_oauth_client.json"
GMAIL_TOKEN = f"{BASE}/config/gmail_token.json"
GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

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
    Returns {'description': str|None, 'company': str|None}.
    LinkedIn job URLs require auth — curling them always returns "Job not found".
    The API get endpoint is the only reliable path.
    """
    import requests as req

    api_key = os.environ.get("RAPIDAPI_KEY", "")
    if not api_key or not job_id:
        return {"description": None, "company": None}
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
            return {"description": None, "company": None}
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
        return {
            "description": strip_jd_boilerplate(description)[:JD_MAX_CHARS] if description else None,
            "company": clean_company(company) if company else None,
        }
    except Exception as e:
        log_event("linkedin_get_error", job_id=job_id, error=str(e))
        return {"description": None, "company": None}


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
            # Cache resolved company in job dict so the main loop can use it
            # without a second API call (only relevant for gmail_linkedin blank-company case).
            if source == "gmail_linkedin" and result.get("company"):
                job["_linkedin_company"] = result["company"]
            if result["description"]:
                return result["description"]
        log_event("linkedin_jd_missing", title=job.get("title"), api_id=api_id)
        return "[LinkedIn JD unavailable — no api_id or get request failed]"

    url = job.get("url", "")
    if url:
        return fetch_jd_curl(url)

    return "[No URL available]"


# ── Job Source Fetching ──
def fetch_greenhouse_jobs(feed_urls_path):
    """
    Fetch jobs via Greenhouse public JSON API.
    Replaces fetch_rss_jobs() — Greenhouse deprecated all RSS feeds.
    Parses slugs from existing greenhouse URL entries in feed_urls.txt.
    JD content is included inline; pandoc conversion deferred to fetch_jd()
    so it only runs for jobs that pass dedup (not all jobs fetched).
    """
    import requests as req

    jobs: list[dict[str, str]] = []
    try:
        with open(feed_urls_path) as f:
            urls = [line.strip() for line in f if line.strip() and not line.startswith("#")]
    except FileNotFoundError:
        return jobs

    slug_re = re.compile(r"boards(?:\.eu)?\.greenhouse\.io/([^/]+)/")
    seen_slugs: set[str] = set()
    slugs = []
    for url in urls:
        m = slug_re.search(url)
        if m:
            slug = m.group(1)
            is_eu = ".eu." in url
            if slug not in seen_slugs:
                seen_slugs.add(slug)
                slugs.append((slug, is_eu))

    gh_headers = {"User-Agent": "findajob-pipeline/1.0 (personal job search tool)"}

    for slug, _is_eu in slugs:
        # Greenhouse API host is always boards-api.greenhouse.io regardless of
        # board subdomain (boards.eu.greenhouse.io is the web board only).
        api_url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"
        try:
            resp = req.get(api_url, headers=gh_headers, timeout=15)
            if resp.status_code == 429:
                wait = min(int(resp.headers.get("Retry-After", "10")), 60)
                log_event("greenhouse_rate_limit", slug=slug, wait=wait)
                time.sleep(wait)
                resp = req.get(api_url, headers=gh_headers, timeout=15)
            if resp.status_code != 200:
                log_event("greenhouse_fetch_skip", slug=slug, status=resp.status_code)
                continue
            gh_jobs = resp.json().get("jobs", [])
            for j in gh_jobs:
                jobs.append(
                    {
                        "title": clean_title(j.get("title", "")),
                        "company": clean_company(j.get("company_name", "") or slug),
                        "url": j.get("absolute_url", ""),
                        "location": (j.get("location") or {}).get("name", ""),
                        "source": "greenhouse_json",
                        "description": html.unescape(j.get("content", "") or ""),
                    }
                )
            log_event("greenhouse_fetch", slug=slug, count=len(gh_jobs))
        except Exception as e:
            log_event("greenhouse_fetch_error", slug=slug, error=str(e))
        time.sleep(0.3)

    return jobs


def _parse_feed_slugs(feed_urls_path, slug_regex):
    """Extract (slug, display_name) from feed_urls.txt for a given URL pattern.

    Inline comments like `https://jobs.lever.co/zoox  # Zoox` are recognized
    as display-name overrides. Without a comment, the display name defaults
    to the slug titlecased (best-effort — multi-word slugs still won't split).

    De-duplicates by slug; first occurrence wins.

    Args:
        feed_urls_path: path to feed_urls.txt
        slug_regex: compiled regex with one capture group for the slug
    Returns:
        list of (slug, display_name) tuples
    """
    try:
        with open(feed_urls_path) as f:
            lines = [line.rstrip("\n") for line in f]
    except FileNotFoundError:
        return []

    results = []
    seen: set[str] = set()
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        # Split off any trailing comment — the part BEFORE the # is the URL.
        if "#" in line:
            url_part, _, comment = line.partition("#")
            url_part = url_part.strip()
            display = comment.strip() or None
        else:
            url_part = line
            display = None
        m = slug_regex.search(url_part)
        if not m:
            continue
        slug = m.group(1)
        if slug in seen:
            continue
        seen.add(slug)
        # Default display name: titlecase the slug so "zoox" → "Zoox".
        # User-supplied comment wins when present.
        results.append((slug, display or slug.title()))
    return results


def fetch_ashby_jobs(feed_urls_path):
    """Fetch jobs via Ashby public posting API.

    Parses slugs from ashbyhq.com URLs in feed_urls.txt. Supports inline
    `# Display Name` comments for company-name override.
    API: https://api.ashbyhq.com/posting-api/job-board/{slug}
    """
    import requests as req

    jobs: list[dict[str, str]] = []
    slug_re = re.compile(r"ashbyhq\.com/([A-Za-z0-9_.-]+)")
    feeds = _parse_feed_slugs(feed_urls_path, slug_re)
    if not feeds:
        return jobs

    headers = {"User-Agent": "findajob-pipeline/1.0 (personal job search tool)"}

    for slug, display_name in feeds:
        api_url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}"
        try:
            resp = req.get(api_url, headers=headers, timeout=15)
            if resp.status_code != 200:
                log_event("ashby_fetch_skip", slug=slug, status=resp.status_code)
                continue
            ashby_jobs = resp.json().get("jobs", [])
            for j in ashby_jobs:
                loc = j.get("location") or ""
                if isinstance(loc, dict):
                    loc = loc.get("name", "")
                jobs.append(
                    {
                        "title": clean_title(j.get("title", "")),
                        "company": clean_company(display_name),
                        "url": j.get("jobUrl", ""),
                        "location": loc,
                        "source": "ashby_json",
                        "description": j.get("descriptionHtml", "") or j.get("descriptionPlain", ""),
                    }
                )
            log_event("ashby_fetch", slug=slug, count=len(ashby_jobs))
        except Exception as e:
            log_event("ashby_fetch_error", slug=slug, error=str(e))
        time.sleep(0.3)

    return jobs


def fetch_lever_jobs(feed_urls_path):
    """Fetch jobs via Lever public postings API.

    Parses slugs from lever.co URLs in feed_urls.txt. Supports inline
    `# Display Name` comments for company-name override.
    API: https://api.lever.co/v0/postings/{slug}
    """
    import requests as req

    jobs: list[dict[str, str]] = []
    slug_re = re.compile(r"lever\.co/([A-Za-z0-9_.-]+)")
    feeds = _parse_feed_slugs(feed_urls_path, slug_re)
    if not feeds:
        return jobs

    headers = {"User-Agent": "findajob-pipeline/1.0 (personal job search tool)"}

    for slug, display_name in feeds:
        api_url = f"https://api.lever.co/v0/postings/{slug}"
        try:
            resp = req.get(api_url, headers=headers, timeout=15)
            if resp.status_code != 200:
                log_event("lever_fetch_skip", slug=slug, status=resp.status_code)
                continue
            lever_jobs = resp.json()
            if not isinstance(lever_jobs, list):
                log_event("lever_fetch_skip", slug=slug, status="unexpected_format")
                continue
            for j in lever_jobs:
                cats = j.get("categories", {})
                jobs.append(
                    {
                        "title": clean_title(j.get("text", "")),
                        "company": clean_company(display_name),
                        "url": j.get("hostedUrl", ""),
                        "location": cats.get("location", ""),
                        "source": "lever_json",
                        "description": j.get("descriptionPlain", "") or j.get("description", ""),
                    }
                )
            log_event("lever_fetch", slug=slug, count=len(lever_jobs))
        except Exception as e:
            log_event("lever_fetch_error", slug=slug, error=str(e))
        time.sleep(0.3)

    return jobs


def fetch_jobsapi_jobs(queries_path):
    """
    Fetch jobs via Jobs API (jobs-api14, RapidAPI).
    LinkedIn: stores api_id for /v2/linkedin/get JD fetch.
    Indeed: stores inline description from search response.
    """
    import requests as req

    api_key = os.environ.get("RAPIDAPI_KEY", "")
    if not api_key:
        log_event("jobsapi_error", error="RAPIDAPI_KEY not set in .env")
        return []

    try:
        with open(queries_path) as f:
            queries = [line.strip() for line in f if line.strip() and not line.startswith("#")]
    except FileNotFoundError:
        log_event("jobsapi_error", error=f"queries file not found: {queries_path}")
        return []

    headers = {
        "x-rapidapi-host": "jobs-api14.p.rapidapi.com",
        "x-rapidapi-key": api_key,
        "Content-Type": "application/json",
    }

    sources = [
        {
            "name": "linkedin",
            "url": "https://jobs-api14.p.rapidapi.com/v2/linkedin/search",
            "params": lambda q: {
                "query": q,
                "location": "United States",
                "datePosted": "day",
                "employmentTypes": "fulltime",
                "experienceLevels": "midSenior;director",
            },
            "url_field": "linkedinUrl",
        },
        {
            "name": "indeed",
            "url": "https://jobs-api14.p.rapidapi.com/v2/indeed/search",
            "params": lambda q: {
                "query": q,
                "countryCode": "us",
                "sortType": "date",
            },
            "url_field": "applyUrl",
        },
    ]

    jobs = []
    for query in queries:
        for source in sources:
            try:
                response = req.get(
                    source["url"],
                    headers=headers,
                    params=source["params"](query),  # type: ignore[operator]
                    timeout=30,
                )
                if response.status_code == 429:
                    wait = min(int(response.headers.get("Retry-After", "10")), 60)
                    log_event("rapidapi_rate_limit", source=source["name"], query=query, wait=wait)
                    time.sleep(wait)
                    response = req.get(
                        source["url"],
                        headers=headers,
                        params=source["params"](query),  # type: ignore[operator]
                        timeout=30,
                    )
                response.raise_for_status()
                data = response.json()

                if data.get("hasError"):
                    log_event("jobsapi_error", source=source["name"], query=query, errors=data.get("errors"))
                    continue

                count = 0
                for job in data.get("data", []):
                    raw_title = job.get("title", "")
                    title = clean_title(raw_title)
                    url = job.get(source["url_field"], "") or job.get("linkedinUrl", "")
                    company = clean_company(job.get("companyName", "") or job.get("company", {}).get("name", ""))
                    loc = job.get("location", "")
                    location = loc.get("location", "") if isinstance(loc, dict) else loc

                    if not title or not url:
                        continue

                    job_dict = {
                        "title": title,
                        "company": company,
                        "url": url,
                        "location": location,
                        "source": f"jobsapi_{source['name']}",
                    }

                    if source["name"] == "linkedin":
                        job_dict["api_id"] = str(job.get("id", ""))
                    elif source["name"] == "indeed":
                        job_dict["description"] = job.get("description", "")

                    jobs.append(job_dict)
                    count += 1

                log_event("jobsapi_fetched", source=source["name"], query=query, count=count)
                time.sleep(0.6)

            except Exception as e:
                log_event("jobsapi_error", source=source["name"], query=query, error=str(e))

    return jobs


# ── Gmail Ingestion ──
def get_gmail_service():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    creds = None
    if os.path.exists(GMAIL_TOKEN):
        creds = Credentials.from_authorized_user_file(GMAIL_TOKEN, GMAIL_SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            with open(GMAIL_TOKEN, "w") as f:
                f.write(creds.to_json())
        else:
            if not sys.stdin.isatty():
                log_event("gmail_auth_skipped", reason="No token and no TTY — run triage.py manually once to authorize")
                return None
            flow = InstalledAppFlow.from_client_secrets_file(GMAIL_CREDS, GMAIL_SCOPES)
            creds = flow.run_local_server(port=0)
            with open(GMAIL_TOKEN, "w") as f:
                f.write(creds.to_json())

    return build("gmail", "v1", credentials=creds)


def parse_jobs_from_email(msg):
    import base64

    from bs4 import BeautifulSoup

    html_content = ""

    def extract_parts(part):
        nonlocal html_content
        mime = part.get("mimeType", "")
        if mime == "text/html":
            data = part.get("body", {}).get("data", "")
            if data:
                padded = data + "=" * (4 - len(data) % 4)
                html_content += base64.urlsafe_b64decode(padded).decode("utf-8", errors="ignore")
        for subpart in part.get("parts", []):
            extract_parts(subpart)

    extract_parts(msg.get("payload", {}))
    if not html_content:
        return []

    soup = BeautifulSoup(html_content, "html.parser")
    jobs = []
    seen_urls = set()

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
        href = a["href"]
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
            api_id = extract_linkedin_job_id(str(href))
            if api_id:
                job_dict["api_id"] = api_id
        jobs.append(job_dict)
        seen_urls.add(href)

    return jobs


def fetch_gmail_jobs():
    if not os.path.exists(GMAIL_CREDS):
        log_event("gmail_skipped", reason="gmail_oauth_client.json not found")
        return []

    try:
        service = get_gmail_service()
        if service is None:
            return []
    except Exception as e:
        log_event("gmail_error", stage="auth", error=str(e))
        return []

    query = (
        "(from:jobalerts-noreply@linkedin.com OR from:jobs-noreply@linkedin.com "
        "OR from:indeedjobs@indeed.com OR from:alert@indeed.com "
        "OR from:careers-noreply@google.com OR from:alerts@ziprecruiter.com "
        "OR from:noreply@ziprecruiter.com) newer_than:30d"
    )

    jobs = []
    try:
        results = service.users().messages().list(userId="me", q=query, maxResults=50).execute()
        messages = results.get("messages", [])
        log_event("gmail_messages_found", count=len(messages))

        for msg_ref in messages:
            try:
                msg = service.users().messages().get(userId="me", id=msg_ref["id"], format="full").execute()
                extracted = parse_jobs_from_email(msg)
                jobs.extend(extracted)
            except Exception as e:
                log_event("gmail_parse_error", msg_id=msg_ref["id"], error=str(e))

    except Exception as e:
        log_event("gmail_error", stage="fetch", error=str(e))

    log_event("gmail_fetched", count=len(jobs))
    return jobs
