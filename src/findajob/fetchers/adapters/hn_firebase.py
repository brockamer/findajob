"""HNFirebaseAdapter — Hacker News monthly hiring threads (#853 Phase 3).

Scrapes the monthly "Ask HN: Who is Hiring?" and "Ask HN: Freelancer?
Seeking freelancer?" threads via the official HN Firebase API
(https://hacker-news.firebaseio.com/v0/). No auth required — the API is
public and rate-limit-free for reasonable use.

Architecture: deterministic comment parser (per issue #853's decision log).
Each top-level comment in a hiring thread is treated as one job posting.
Title/company extraction uses a heuristic parser targeting the established
HN "Who is Hiring" comment format: first line is typically
"Company Name | Role Title | Location | Remote/Onsite | ..." separated
by pipes. This is not AI-parsed — deterministic regex + split logic.
"""

from __future__ import annotations

import re
import time
from typing import ClassVar

import requests

from findajob.audit import log_event
from findajob.cleaning import clean_company, clean_title

from .base import LiveTestResult, QueryResult

_HN_USER = "whoishiring"
_THREAD_PATTERNS = [
    re.compile(r"Ask HN: Who is hiring\?", re.IGNORECASE),
    re.compile(r"Ask HN: Freelancer\? Seeking freelancer\?", re.IGNORECASE),
    re.compile(r"Ask HN: Who wants to be hired\?", re.IGNORECASE),
]
_HIRING_PATTERN = re.compile(r"Ask HN: Who is hiring\?", re.IGNORECASE)
_FREELANCER_PATTERN = re.compile(r"Ask HN: Freelancer\? Seeking freelancer\?", re.IGNORECASE)


class HNFirebaseAdapter:
    """Hacker News hiring thread ingestion via Firebase API.

    Discovers the most recent "Who is Hiring" and "Freelancer?" threads
    by scanning submissions from the `whoishiring` user, then parses
    top-level comments as job postings using deterministic pipe-delimited
    format extraction.
    """

    name: ClassVar[str] = "hn-firebase"
    display_name: ClassVar[str] = "Hacker News (Who is Hiring)"
    source_label: ClassVar[str] = "hn_whoishiring"
    required_env_vars: ClassVar[tuple[str, ...]] = ()

    _API_BASE: ClassVar[str] = "https://hacker-news.firebaseio.com/v0"
    _UA: ClassVar[str] = "findajob-pipeline/1.0 (personal job search tool)"
    _MAX_COMMENTS: ClassVar[int] = 800
    _COMMENT_BATCH_DELAY: ClassVar[float] = 0.05

    def is_configured(self) -> bool:
        return True

    def fetch(self, queries: list[str]) -> list[dict]:
        del queries
        thread_ids = self._discover_threads()
        if not thread_ids:
            log_event("hn_firebase_no_threads")
            return []
        jobs: list[dict] = []
        for thread_id in thread_ids:
            thread_jobs = self._parse_thread(thread_id)
            jobs.extend(thread_jobs)
        log_event("hn_firebase_fetch", count=len(jobs), threads=len(thread_ids))
        return jobs

    def _discover_threads(self) -> list[int]:
        """Find recent hiring thread IDs from the whoishiring user's submissions."""
        url = f"{self._API_BASE}/user/{_HN_USER}.json"
        try:
            resp = requests.get(url, headers={"User-Agent": self._UA}, timeout=15)
        except Exception as e:
            log_event("hn_firebase_user_error", error=str(e))
            return []
        if resp.status_code != 200:
            log_event("hn_firebase_user_skip", status=resp.status_code)
            return []
        try:
            user_data = resp.json()
        except ValueError:
            return []
        if not isinstance(user_data, dict):
            return []
        submitted = user_data.get("submitted", [])
        if not isinstance(submitted, list):
            return []
        # Check most recent submissions (they're in reverse-chronological order)
        thread_ids: list[int] = []
        for item_id in submitted[:30]:
            item = self._get_item(item_id)
            if not item or item.get("type") != "story":
                continue
            title = item.get("title", "")
            if _HIRING_PATTERN.search(title) or _FREELANCER_PATTERN.search(title):
                thread_ids.append(item_id)
                if len(thread_ids) >= 2:
                    break
        return thread_ids

    def _get_item(self, item_id: int) -> dict | None:
        url = f"{self._API_BASE}/item/{item_id}.json"
        try:
            resp = requests.get(url, headers={"User-Agent": self._UA}, timeout=10)
        except Exception:
            return None
        if resp.status_code != 200:
            return None
        try:
            return resp.json()
        except ValueError:
            return None

    def _parse_thread(self, thread_id: int) -> list[dict]:
        """Fetch and parse top-level comments from a hiring thread."""
        thread = self._get_item(thread_id)
        if not thread:
            return []
        kids = thread.get("kids", [])
        if not isinstance(kids, list):
            return []
        thread_url = f"https://news.ycombinator.com/item?id={thread_id}"
        jobs: list[dict] = []
        for comment_id in kids[: self._MAX_COMMENTS]:
            comment = self._get_item(comment_id)
            if not comment or comment.get("dead") or comment.get("deleted"):
                continue
            text = comment.get("text", "")
            if not text:
                continue
            parsed = _parse_comment(text, thread_url, comment_id)
            if parsed:
                jobs.append(parsed)
            time.sleep(self._COMMENT_BATCH_DELAY)
        return jobs

    def live_test(self, queries: list[str]) -> LiveTestResult:
        del queries
        url = f"{self._API_BASE}/user/{_HN_USER}.json"
        try:
            resp = requests.get(url, headers={"User-Agent": self._UA}, timeout=15)
        except requests.RequestException as e:
            return LiveTestResult(ok=False, bucket="network", per_query=[], auth_error=str(e))
        if resp.status_code == 429:
            return LiveTestResult(ok=False, bucket="rate_limit", per_query=[], auth_error="Rate limited.")
        if 500 <= resp.status_code < 600:
            return LiveTestResult(
                ok=False, bucket="server", per_query=[], auth_error=f"HTTP {resp.status_code}: server error."
            )
        if resp.status_code != 200:
            return LiveTestResult(
                ok=False, bucket="server", per_query=[], auth_error=f"HTTP {resp.status_code}: unexpected."
            )
        try:
            data = resp.json()
        except ValueError:
            return LiveTestResult(ok=False, bucket="server", per_query=[], auth_error="Invalid JSON.")
        if not isinstance(data, dict) or "submitted" not in data:
            return LiveTestResult(ok=False, bucket="server", per_query=[], auth_error="Unexpected user shape.")
        per_query = [QueryResult(query="whoishiring", count=len(data.get("submitted", [])))]
        return LiveTestResult(ok=True, bucket="success", per_query=per_query, auth_error=None)


def _parse_comment(html_text: str, thread_url: str, comment_id: int) -> dict | None:
    """Parse a single top-level HN hiring comment into a job row.

    HN "Who is Hiring" comments follow a loose but recognizable convention:
    the first line is pipe-delimited metadata:
        Company Name | Role Title | Location | Remote/Onsite | Salary

    Not all fields are always present. The parser extracts what it can
    and falls back gracefully — a comment with no parseable first line
    is still emitted with the raw text as description if it looks like a
    job posting (contains hiring-related keywords).
    """
    plain = _strip_html(html_text)
    lines = plain.strip().split("\n")
    if not lines:
        return None
    first_line = lines[0].strip()
    if "|" in first_line:
        parts = [p.strip() for p in first_line.split("|")]
        company = parts[0] if len(parts) > 0 else ""
        title = parts[1] if len(parts) > 1 else ""
        location = parts[2] if len(parts) > 2 else ""
    else:
        # Fallback: treat first line as company/title combined
        if not _looks_like_job(plain):
            return None
        company = first_line[:80]
        title = ""
        location = ""

    if not company and not title:
        return None

    comment_url = f"https://news.ycombinator.com/item?id={comment_id}"
    description = "\n".join(lines[1:]).strip() if len(lines) > 1 else ""

    return {
        "title": clean_title(title) if title else clean_title(company),
        "company": clean_company(company) if company else "Unknown (HN)",
        "url": comment_url,
        "location": location or "See posting",
        "source": "hn_whoishiring",
        "description": description,
    }


def _strip_html(text: str) -> str:
    """Remove HTML tags and decode common entities from HN comment text."""
    import html

    text = html.unescape(text)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<p>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    return text


def _looks_like_job(text: str) -> bool:
    """Heuristic: does this comment look like a job posting?"""
    lower = text.lower()
    signals = ["hiring", "looking for", "remote", "onsite", "salary", "apply", "role", "position", "engineer"]
    return sum(1 for s in signals if s in lower) >= 2
