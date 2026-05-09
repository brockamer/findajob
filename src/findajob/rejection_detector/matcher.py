"""Match a classified rejection to a candidate jobs.id.

Multi-step:
  1. Resolve company-name aliases (config/company_aliases.yaml) — §4.2.1
  2. Filter candidate jobs to active stages (matches _POST_APPLICATION_STAGES)
  3. Token-set overlap >= 0.8 against company name (rapidfuzz)
  4. If multiple match: narrow by role title (token-set >= 0.6)
  5. Apply seniority-token gate when narrowing — §4.2.2

Spec: docs/superpowers/specs/2026-05-01-362-rejection-detection-design.md §4.2 matcher.py + §4.2.{1,2}
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from rapidfuzz import fuzz

from findajob.paths import BASE
from findajob.rejection_detector.patterns import SENIORITY_TOKENS

_COMPANY_ALIASES_PATH = Path(BASE) / "config" / "company_aliases.yaml"

_TOKEN_SPLIT_RE = re.compile(r"[\s,\-/.]+")


@dataclass(frozen=True)
class MatchResult:
    job_id: str | None
    status: str


def match_job(
    conn: sqlite3.Connection,
    extracted_company: str | None,
    extracted_role: str | None,
    received_at: str,
) -> MatchResult:
    if not extracted_company:
        return MatchResult(job_id=None, status="unmatched")

    aliases = _load_aliases()
    candidate_names = resolve_aliases(extracted_company, aliases)

    rows = conn.execute(
        """
        SELECT id, company, title, stage
        FROM jobs
        WHERE stage IN ('applied', 'interview', 'offer')
          AND synthetic = 0
        """
    ).fetchall()

    by_company = [row for row in rows if any(_company_match(row["company"], cand) for cand in candidate_names)]

    if len(by_company) == 0:
        return MatchResult(job_id=None, status="unmatched")
    if len(by_company) == 1:
        return MatchResult(job_id=by_company[0]["id"], status="matched")

    if extracted_role:
        narrowed = [row for row in by_company if _role_match(row["title"], extracted_role)]
        seniority_filtered = _filter_by_seniority(narrowed, extracted_role)
        if len(seniority_filtered) == 1:
            return MatchResult(job_id=seniority_filtered[0]["id"], status="matched")

    return MatchResult(job_id=None, status="ambiguous")


def _load_aliases() -> dict[str, str]:
    if not _COMPANY_ALIASES_PATH.exists():
        return {}
    try:
        data = yaml.safe_load(_COMPANY_ALIASES_PATH.read_text(encoding="utf-8"))
    except yaml.YAMLError:
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): str(v) for k, v in data.items()}


def resolve_aliases(extracted_company: str, aliases: dict[str, str]) -> set[str]:
    """Return candidate canonical names for a sender-extracted company.

    Word-boundary tokenization avoids "cobot in cobotics inc" false positives
    that pure substring would hit.
    """
    extracted_tokens = _tokenize(extracted_company)
    candidates = {extracted_company.lower()}
    for alias, canonical in aliases.items():
        alias_tokens = _tokenize(alias)
        canonical_tokens = _tokenize(canonical)
        if alias_tokens and alias_tokens.issubset(extracted_tokens):
            candidates.add(canonical.lower())
        if canonical_tokens and canonical_tokens.issubset(extracted_tokens):
            candidates.add(alias.lower())
    return candidates


def _tokenize(s: str) -> set[str]:
    return {tok for tok in _TOKEN_SPLIT_RE.split(s.lower()) if tok}


def _company_match(jobs_company: str, candidate: str) -> bool:
    if not jobs_company or not candidate:
        return False
    return fuzz.token_set_ratio(jobs_company.lower(), candidate.lower()) >= 80


def _role_match(jobs_title: str, extracted_role: str) -> bool:
    if not jobs_title or not extracted_role:
        return False
    return fuzz.token_set_ratio(jobs_title.lower(), extracted_role.lower()) >= 60


def _filter_by_seniority(rows: list[Any], extracted_role: str) -> list[Any]:
    """If the extracted role contains a seniority token, candidates must too."""
    extracted_tokens = _tokenize(extracted_role)
    extracted_seniority = extracted_tokens & SENIORITY_TOKENS
    if not extracted_seniority:
        return rows
    return [row for row in rows if _tokenize(row["title"]) & SENIORITY_TOKENS == extracted_seniority]
