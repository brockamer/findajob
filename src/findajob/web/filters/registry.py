"""Per-tab ColumnSpec lists for the 6 board tabs.

Visibility defaults are tuned to what the operator needs to *decide* on each
tab — see docs/superpowers/specs/2026-04-25-board-filter-framework-design.md
"Per-tab visibility defaults". Hidden columns remain in the spec so the
?cols= URL override (and the future #277 Columns dropdown) can surface them.
"""

from __future__ import annotations

from findajob.config_loader import load_reject_reasons
from findajob.web.filters.spec import ColumnSpec, Kind, validate_specs

# Source / stage / remote_status / reject_reason vocabularies. Single source of
# truth here; if these change, ENUM filters update without touching templates.
_SOURCE_VALUES = (
    "greenhouse_json",
    "ashby_json",
    "ashby",
    "lever_json",
    "jobsapi_linkedin",
    "jobsapi_indeed",
    "gmail_linkedin",
    "gmail_google",
    "jsearch",
    "manual",
)
_STAGE_VALUES = (
    "scored",
    "manual_review",
    "prep_in_progress",
    "materials_drafted",
    "applied",
    "interview",
    "offer",
    "waitlisted",
    "rejected",
    "not_selected",
    "withdrew",
)
_REMOTE_VALUES = ("Remote", "Hybrid", "On-site", "Unknown")


# Reject-reason chip values resolve per-request (lazy callable) so
# /settings/reject-reasons/ saves are reflected on the next page load
# without a container restart (#490). Same source as the board's
# reject-reason dropdown — fixes the silent-filtering drift bug from
# #301 §2.1.
def _reject_reason_values() -> tuple[str, ...]:
    return load_reject_reasons()[0]


# ─── Dashboard ────────────────────────────────────────────────────────────────
DASHBOARD_COLUMNS: tuple[ColumnSpec, ...] = (
    ColumnSpec(name="relevance_score", label="Rel", kind=Kind.SCORE),
    ColumnSpec(name="interview_likelihood", label="Likelihood", kind=Kind.SCORE),
    ColumnSpec(name="fit_score", label="Fit", kind=Kind.SCORE),
    ColumnSpec(name="probability_score", label="Prob", kind=Kind.SCORE),
    ColumnSpec(name="title", label="Title", kind=Kind.TEXT),
    ColumnSpec(name="company", label="Company", kind=Kind.TEXT),
    ColumnSpec(
        name="company_history",
        label="History",
        kind=Kind.COMPUTED,
        sortable=False,
        filterable=False,
    ),
    ColumnSpec(name="location", label="Location", kind=Kind.TEXT),
    ColumnSpec(
        name="remote_status",
        label="Remote",
        kind=Kind.ENUM,
        enum_values=_REMOTE_VALUES,
    ),
    ColumnSpec(name="known_contacts", label="Contacts", kind=Kind.TEXT),
    ColumnSpec(
        name="comp_estimate",
        label="Comp",
        kind=Kind.TEXT,
        default_visible=False,
    ),
    ColumnSpec(name="ai_notes", label="AI notes", kind=Kind.TEXT),
    ColumnSpec(name="user_notes", label="Notes", kind=Kind.TEXT),
    ColumnSpec(name="created_at", label="Date", kind=Kind.DATE),
    # Stage is filterable but not visible by default — score-5/6 triage opt-in.
    ColumnSpec(
        name="stage",
        label="Stage",
        kind=Kind.ENUM,
        enum_values=_STAGE_VALUES,
        default_visible=False,
    ),
)
validate_specs(DASHBOARD_COLUMNS)

# ─── Applied ──────────────────────────────────────────────────────────────────
APPLIED_COLUMNS: tuple[ColumnSpec, ...] = (
    ColumnSpec(name="title", label="Title", kind=Kind.TEXT, db_expr="j.title"),
    ColumnSpec(name="company", label="Company", kind=Kind.TEXT, db_expr="j.company"),
    ColumnSpec(
        name="applied_date",
        label="Applied",
        kind=Kind.DATE,
        db_expr="al.applied_date",
    ),
    ColumnSpec(
        name="days_since_applied",
        label="Days",
        kind=Kind.INTEGER,
        db_expr="CAST((julianday('now') - julianday(al.applied_date)) AS INTEGER)",
    ),
    ColumnSpec(
        name="stage",
        label="Stage",
        kind=Kind.ENUM,
        enum_values=("applied", "interview", "offer"),
        db_expr="j.stage",
    ),
    ColumnSpec(name="user_notes", label="Notes", kind=Kind.TEXT, db_expr="j.user_notes"),
    ColumnSpec(
        name="known_contacts",
        label="Contacts",
        kind=Kind.TEXT,
        db_expr="j.known_contacts",
    ),
    ColumnSpec(name="location", label="Location", kind=Kind.TEXT, db_expr="j.location"),
    ColumnSpec(
        name="remote_status",
        label="Remote",
        kind=Kind.ENUM,
        enum_values=_REMOTE_VALUES,
        db_expr="j.remote_status",
    ),
    ColumnSpec(
        name="cost",
        label="Cost",
        kind=Kind.COMPUTED,
        sortable=True,
        filterable=False,
        db_expr=("(SELECT SUM(cl.cost_usd) FROM cost_log cl WHERE cl.job_id = j.id AND cl.cost_usd IS NOT NULL)"),
    ),
    ColumnSpec(
        name="comp_estimate",
        label="Comp",
        kind=Kind.TEXT,
        db_expr="j.comp_estimate",
        default_visible=False,
    ),
    ColumnSpec(
        name="ai_notes",
        label="AI notes",
        kind=Kind.TEXT,
        db_expr="j.ai_notes",
        default_visible=False,
    ),
)
validate_specs(APPLIED_COLUMNS)

# ─── Review ───────────────────────────────────────────────────────────────────
REVIEW_COLUMNS: tuple[ColumnSpec, ...] = (
    ColumnSpec(name="title", label="Title", kind=Kind.TEXT),
    ColumnSpec(name="company", label="Company", kind=Kind.TEXT),
    ColumnSpec(name="score_flag_reason", label="Flag reason", kind=Kind.TEXT),
    ColumnSpec(
        name="source",
        label="Source",
        kind=Kind.ENUM,
        enum_values=_SOURCE_VALUES,
    ),
    ColumnSpec(name="user_notes", label="Notes", kind=Kind.TEXT),
    ColumnSpec(name="created_at", label="Date", kind=Kind.DATE),
)
validate_specs(REVIEW_COLUMNS)

# ─── Waitlist ─────────────────────────────────────────────────────────────────
WAITLIST_COLUMNS: tuple[ColumnSpec, ...] = (
    ColumnSpec(name="title", label="Title", kind=Kind.TEXT, db_expr="w.title"),
    ColumnSpec(name="company", label="Company", kind=Kind.TEXT, db_expr="w.company"),
    ColumnSpec(
        name="company_history",
        label="History",
        kind=Kind.COMPUTED,
        sortable=False,
        filterable=False,
    ),
    ColumnSpec(
        name="relevance_score",
        label="Rel",
        kind=Kind.SCORE,
        db_expr="w.relevance_score",
    ),
    ColumnSpec(
        name="interview_likelihood",
        label="Likelihood",
        kind=Kind.SCORE,
        db_expr="w.interview_likelihood",
    ),
    ColumnSpec(name="fit_score", label="Fit", kind=Kind.SCORE, db_expr="w.fit_score"),
    ColumnSpec(
        name="probability_score",
        label="Prob",
        kind=Kind.SCORE,
        db_expr="w.probability_score",
    ),
    ColumnSpec(name="location", label="Location", kind=Kind.TEXT, db_expr="w.location"),
    ColumnSpec(
        name="remote_status",
        label="Remote",
        kind=Kind.ENUM,
        enum_values=_REMOTE_VALUES,
        db_expr="w.remote_status",
    ),
    ColumnSpec(
        name="ai_notes",
        label="AI notes",
        kind=Kind.TEXT,
        db_expr="w.ai_notes",
        default_visible=False,
    ),
    ColumnSpec(name="user_notes", label="Notes", kind=Kind.TEXT, db_expr="w.user_notes"),
    ColumnSpec(name="created_at", label="Date", kind=Kind.DATE, db_expr="w.created_at"),
    ColumnSpec(
        name="blocking_app",
        label="Blocking app",
        kind=Kind.COMPUTED,
        sortable=False,
        filterable=False,
    ),
)
validate_specs(WAITLIST_COLUMNS)

# ─── Rejected ─────────────────────────────────────────────────────────────────
REJECTED_COLUMNS: tuple[ColumnSpec, ...] = (
    ColumnSpec(name="title", label="Title", kind=Kind.TEXT, db_expr="j.title"),
    ColumnSpec(name="company", label="Company", kind=Kind.TEXT, db_expr="j.company"),
    ColumnSpec(
        name="reject_reason",
        label="Reason",
        kind=Kind.ENUM,
        enum_values=_reject_reason_values,
        db_expr="j.reject_reason",
    ),
    ColumnSpec(
        name="rejected_date",
        label="Rejected",
        kind=Kind.DATE,
        db_expr="al.rejected_date",
    ),
    ColumnSpec(
        name="rejection_source",
        label="Source",
        kind=Kind.ENUM,
        enum_values=("user", "company"),
        db_expr="CASE j.stage WHEN 'not_selected' THEN 'company' ELSE 'user' END",
    ),
)
validate_specs(REJECTED_COLUMNS)

# ─── Archive ──────────────────────────────────────────────────────────────────
ARCHIVE_COLUMNS: tuple[ColumnSpec, ...] = (
    ColumnSpec(name="relevance_score", label="Rel", kind=Kind.SCORE),
    ColumnSpec(name="title", label="Title", kind=Kind.TEXT),
    ColumnSpec(name="company", label="Company", kind=Kind.TEXT),
    ColumnSpec(
        name="stage",
        label="Stage",
        kind=Kind.ENUM,
        enum_values=_STAGE_VALUES,
    ),
    ColumnSpec(name="location", label="Location", kind=Kind.TEXT),
    ColumnSpec(
        name="remote_status",
        label="Remote",
        kind=Kind.ENUM,
        enum_values=_REMOTE_VALUES,
    ),
    ColumnSpec(name="created_at", label="Date", kind=Kind.DATE),
    ColumnSpec(
        name="source",
        label="Source",
        kind=Kind.ENUM,
        enum_values=_SOURCE_VALUES,
    ),
    ColumnSpec(name="url", label="URL", kind=Kind.TEXT, default_visible=False),
)
validate_specs(ARCHIVE_COLUMNS)


__all__ = [
    "DASHBOARD_COLUMNS",
    "APPLIED_COLUMNS",
    "REVIEW_COLUMNS",
    "WAITLIST_COLUMNS",
    "REJECTED_COLUMNS",
    "ARCHIVE_COLUMNS",
]
