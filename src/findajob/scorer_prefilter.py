#!/usr/bin/env python3
"""
Deterministic pre-filter for job scoring.
Runs BEFORE any LLM call. Two stages, with the location gate between them:

  Stage 1 — Hard reject by title regex OR excluded company → score 1,
            scored, no LLM. Title check runs first; company check second.
            (context_suppressors in prefilter_rules.yaml can override
            the title branch.)
  Location — Location names a US state, country or non-US region outside
            config/target_locations.txt → score 2, no LLM (#1217; rules in
            findajob.location_gate).
  Stage 2 — In-domain title + no usable JD → score 5, no LLM

If no stage fires, returns (None, None) and caller should invoke the LLM.

Rules are loaded from config/prefilter_rules.yaml, config/in_domain_patterns.yaml,
config/excluded_employers.yaml and config/target_locations.txt (all gitignored).
See src/findajob/config_loader.py and src/findajob/location_gate.py. If any file
is missing, that branch becomes a no-op — install config files per
docs/operations/config-reference.md.

Usage:
    from findajob.scorer_prefilter import prefilter_score
    result, reason = prefilter_score(title, company, jd_is_usable, location=location)
    if result is not None:
        return result, 0   # latency=0, no subprocess
    # ... LLM path
"""

from __future__ import annotations

from findajob.config_loader import (
    load_excluded_employers,
    load_hard_reject_rules,
    load_in_domain_rules,
)
from findajob.location_gate import load_targets, location_mismatch
from findajob.tiers import resolve_tier


def _hard_reject_match(title: str) -> str | None:
    """Return the matched pattern string, or None."""
    reject_re, suppressor_re = load_hard_reject_rules()
    m = reject_re.search(title)
    if not m:
        return None
    # If a context suppressor also matches, don't reject.
    if suppressor_re is not None and suppressor_re.search(title):
        return None
    return m.group(0).strip()


def _excluded_employer_match(company: str) -> str | None:
    """Return the matched company string, or None. Case-insensitive."""
    if not company:
        return None
    c = company.strip()
    if not c:
        return None
    exact_set, regex_re = load_excluded_employers()
    if c.lower() in exact_set:
        return c
    if regex_re is not None and regex_re.search(c):
        return c
    return None


def _in_domain_match(title: str) -> bool:
    in_domain_re, poison_re = load_in_domain_rules()
    if poison_re is not None and poison_re.search(title):
        return False
    return bool(in_domain_re.search(title))


def prefilter_score(
    title: str, company: str, jd_usable: bool, *, location: str = ""
) -> tuple[dict[str, object] | None, str | None]:
    """
    Returns (result_dict, reason_str) if a deterministic decision can be made,
    or (None, None) if the LLM should be invoked.

    `company` drives the excluded-employer branch and the company tier.
    `location` drives the location gate; the default "" never trips it.
    """
    t = (title or "").strip()

    # ── Stage 1: Hard reject ──────────────────────────────────────────────────
    match = _hard_reject_match(t)
    if match:
        reason = f'Pre-filter hard reject: title matched "{match}"'
        return {
            "score_status": "scored",
            "relevance_score": 1,
            "interview_likelihood": 1,
            "strengths_alignment": "Hard reject — title is outside candidate domain.",
            "industry_sector": None,
            "comp_estimate": None,
            "ai_notes": reason,
            "score_flag_reason": reason,
            "remote_status": "Unknown",
            "scored_by": "prefilter_stage1",
            "company_tier": resolve_tier(company),
        }, reason

    excluded = _excluded_employer_match(company)
    if excluded:
        reason = f'Pre-filter excluded employer: "{excluded}"'
        return {
            "score_status": "scored",
            "relevance_score": 1,
            "interview_likelihood": 1,
            "strengths_alignment": "Excluded employer — user opted out of this company.",
            "industry_sector": None,
            "comp_estimate": None,
            "ai_notes": reason,
            "score_flag_reason": "excluded_employer",
            "remote_status": "Unknown",
            "scored_by": "prefilter_stage1",
            "company_tier": resolve_tier(company),
        }, reason

    # ── Location gate: place outside target_locations.txt → score 2 ───────────
    targets = load_targets() if location else None
    if targets is not None and (outside := location_mismatch(location, targets)) is not None:
        reason = (
            f'Pre-filter location mismatch: "{location}" names {outside}, '
            f"outside the target locations ({'; '.join(targets.lines)})"
        )
        return {
            "score_status": "scored",
            "relevance_score": 2,
            "interview_likelihood": 1,
            "strengths_alignment": "Location mismatch — the posting is outside the candidate's target locations.",
            "industry_sector": None,
            "comp_estimate": None,
            "ai_notes": reason,
            "score_flag_reason": "location_mismatch",
            "remote_status": "Unknown",
            "scored_by": "prefilter_location",
            "company_tier": resolve_tier(company),
        }, reason

    # ── Stage 2: In-domain title, JD absent → score 5 ─────────────────────────
    if not jd_usable and _in_domain_match(t):
        reason = "Pre-filter in-domain/no-JD: scored 5"
        return {
            "score_status": "scored",
            "relevance_score": 5,
            "interview_likelihood": 4,
            "strengths_alignment": "Title is directionally in-domain. JD unavailable — scored 5 per policy.",
            "industry_sector": None,
            "comp_estimate": None,
            "ai_notes": reason,
            "score_flag_reason": None,
            "remote_status": "Unknown",
            "scored_by": "prefilter_stage2",
            "company_tier": resolve_tier(company),
        }, reason

    return None, None
