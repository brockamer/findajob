"""Board tabs: /board/dashboard, /applied, /review, /waitlist, /rejected, /not-selected, /archive."""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from findajob.config_loader import load_spend_ceiling
from findajob.fetchers.adapters import registry as _adapter_registry
from findajob.triage.schedule import next_triage_time
from findajob.web import view_prefs
from findajob.web.company_history import build_history_by_fp, fetch_company_history
from findajob.web.discoveries import load_discoveries_summary
from findajob.web.filters import ColumnSpec, ParsedFilters, build_filter_clauses, parse_filter_params
from findajob.web.filters import registry as filter_registry
from findajob.web.routes.materials import get_db

router = APIRouter()


_VALID_DENSITIES = {"compact", "expanded"}
_DEFAULT_DENSITY = "compact"


def _normalize_density(raw: str) -> str:
    return raw if raw in _VALID_DENSITIES else _DEFAULT_DENSITY


def _resolve_visible(specs: tuple[ColumnSpec, ...], parsed: ParsedFilters) -> set[str]:
    """URL is the authority by the time this runs.

    Cascade (full picture, #277): URL ?cols= wins → if absent, the page
    handler has already 303-redirected from view_prefs.load() so the
    persisted state is now in the URL → if neither, fall back to
    ColumnSpec.default_visible here.
    """
    if parsed.cols:
        return set(parsed.cols)
    return {s.name for s in specs if s.default_visible}


def _maybe_redirect_to_persisted(
    request: Request,
    tab: str,
    parsed: ParsedFilters,
    db: sqlite3.Connection,
) -> RedirectResponse | None:
    """If the URL carries no filter state and view_prefs has a row for
    this tab, return a 303 to the same path with the persisted query
    string. Caller short-circuits the handler on a non-None return.

    Unrelated query params (?density=, ?dismiss_*=) are deliberately
    dropped here — they're not part of the filter framework's URL
    contract, and persisting them is out of scope for #277. The redirect
    target rebuilds the URL with only the persisted querystring.
    """
    if view_prefs.has_filter_state(parsed):
        return None
    persisted = view_prefs.load(db, tab)
    if not persisted:
        return None
    return RedirectResponse(url=f"{request.url.path}?{persisted}", status_code=303)


def _persist_view(db: sqlite3.Connection, tab: str, parsed: ParsedFilters) -> None:
    """Auto-save the current parsed filter state, allowlisted.

    Inlined into page + /rows GETs so every filter mutation updates the
    per-tab pref. Empty parsed state is a no-op — use the reset
    endpoint to explicitly clear persistence.

    ``cols`` matching the tab's ``ColumnSpec.default_visible`` set is
    dropped from the persisted string (#844). Persisting a no-op cols
    clause causes the cold-load redirect to render the cols pill on
    the operator's perceived default view.
    """
    default_cols = _default_cols_for_storage_tab(tab)
    view_prefs.save(db, tab, view_prefs.serialize(parsed, default_cols=default_cols))


def _default_cols_for_storage_tab(tab: str) -> tuple[str, ...]:
    """Return the spec-defined default-visible column names for a tab."""
    specs = _STORAGE_TAB_SPECS.get(tab)
    if specs is None:
        return ()
    return tuple(s.name for s in specs if s.default_visible)


_STORAGE_TAB_SPECS: dict[str, tuple[ColumnSpec, ...]] = {
    "dashboard": filter_registry.DASHBOARD_COLUMNS,
    "applied": filter_registry.APPLIED_COLUMNS,
    "review": filter_registry.REVIEW_COLUMNS,
    "waitlist": filter_registry.WAITLIST_COLUMNS,
    "rejected": filter_registry.REJECTED_COLUMNS,
    "not_selected": filter_registry.NOT_SELECTED_COLUMNS,
    "archive": filter_registry.ARCHIVE_COLUMNS,
}


_DASHBOARD_DEFAULT_SORT = "relevance_score"
_DASHBOARD_DEFAULT_SCORE_MIN = 7

# Stage gate + dedup-sibling exclusion. The score floor is no longer baked in —
# it's applied as a ROUTE-LEVEL DEFAULT (see _dashboard_query) so that
# ?relevance_score_min=5 actually surfaces score-5/6 buried gems instead of
# being clobbered by the base WHERE. The default keeps cold-load behavior
# unchanged at score >= 7.
_DASHBOARD_BASE_WHERE = (
    "stage IN ('scored','manual_review','prep_in_progress','briefing_ready','materials_drafted')"
    " AND NOT EXISTS ("
    "  SELECT 1 FROM jobs sib"
    "  WHERE sib.id != jobs.id"
    "    AND LOWER(TRIM(sib.company)) = LOWER(TRIM(jobs.company))"
    "    AND LOWER(TRIM(sib.title)) = LOWER(TRIM(jobs.title))"
    "    AND sib.stage IN ('applied','interview','offer','not_selected')"
    " )"
)


def _apply_dashboard_default_score(parsed: ParsedFilters) -> ParsedFilters:
    """If the user didn't pass ?relevance_score_min/_max, apply a default score
    floor of 7 so the cold-load surface stays at 7+. Any explicit user value
    wins (including 0, e.g., ?relevance_score_min=0 to see everything)."""
    if "relevance_score" in parsed.numeric_range:
        return parsed
    from dataclasses import replace

    return replace(
        parsed,
        numeric_range={
            **parsed.numeric_range,
            "relevance_score": (_DASHBOARD_DEFAULT_SCORE_MIN, None),
        },
    )


def _dashboard_query(parsed: ParsedFilters) -> tuple[str, list[object]]:
    specs = filter_registry.DASHBOARD_COLUMNS
    parsed = _apply_dashboard_default_score(parsed)
    clauses, params = build_filter_clauses(specs, parsed)
    sort = parsed.sort or _DASHBOARD_DEFAULT_SORT
    order = "DESC" if parsed.desc else "ASC"
    sql = (
        "SELECT fingerprint, title, company, location, remote_status, known_contacts, "
        "comp_estimate, ai_notes, user_notes, relevance_score, fit_score, probability_score, "
        "interview_likelihood, stage, created_at, stage_updated, url, prep_folder_path "
        f"FROM jobs WHERE ({_DASHBOARD_BASE_WHERE}){clauses} ORDER BY {sort} {order}"
    )
    return sql, params


@router.get("/board/dashboard", response_class=HTMLResponse)
def dashboard(
    request: Request,
    density: str = Query(default=_DEFAULT_DENSITY),
    dismiss_active_sources_banner: int = Query(default=0),
    dismiss_spend_ceiling_banner: int = Query(default=0),
    dismiss_first_triage_banner: int = Query(default=0),
    triage_launched: int = Query(default=0),
    db: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    # #603: ?dismiss_active_sources_banner=1 sets a 1-year cookie and
    # redirects back to the bare dashboard URL so subsequent loads use
    # the cookie path.
    if dismiss_active_sources_banner:
        redirect = RedirectResponse(url="/board/dashboard", status_code=303)
        redirect.set_cookie(
            "active_sources_banner_dismissed",
            "1",
            max_age=31536000,  # 1 year
            httponly=False,
            samesite="lax",
        )
        return redirect  # type: ignore[return-value]

    # #671: ?dismiss_spend_ceiling_banner=1 sets a 1-year cookie so the
    # operator can silence the banner without setting a ceiling.
    if dismiss_spend_ceiling_banner:
        redirect = RedirectResponse(url="/board/dashboard", status_code=303)
        redirect.set_cookie(
            "spend_ceiling_banner_dismissed",
            "1",
            max_age=31536000,  # 1 year
            httponly=False,
            samesite="lax",
        )
        return redirect  # type: ignore[return-value]

    # #752: ?dismiss_first_triage_banner=1 silences the first-visit banner
    # via a 1-year cookie. Also self-suppresses once jobs exist or sentinel
    # ages past _FIRST_TRIAGE_WINDOW_HOURS — cookie just covers the
    # legitimately-first-visit window for users who want to skip the nudge.
    if dismiss_first_triage_banner:
        redirect = RedirectResponse(url="/board/dashboard", status_code=303)
        redirect.set_cookie(
            "first_triage_banner_dismissed",
            "1",
            max_age=31536000,  # 1 year
            httponly=False,
            samesite="lax",
        )
        return redirect  # type: ignore[return-value]

    specs = filter_registry.DASHBOARD_COLUMNS
    parsed = parse_filter_params(specs, request.query_params)
    view_prefs_redirect = _maybe_redirect_to_persisted(request, "dashboard", parsed, db)
    if view_prefs_redirect is not None:
        return view_prefs_redirect  # type: ignore[return-value]
    _persist_view(db, "dashboard", parsed)
    sql, params = _dashboard_query(parsed)
    rows = db.execute(sql, params).fetchall()
    history_by_fp = build_history_by_fp(rows, fetch_company_history(db))
    materials_base_url = os.environ.get("FINDAJOB_MATERIALS_BASE_URL", "")
    visible = _resolve_visible(specs, parsed)
    discoveries = load_discoveries_summary(request.app.state.base_root)
    rejections_pending = _rejections_pending_count(db)
    show_banner, default_count = _active_sources_banner_state(request)
    show_ceiling_banner = _spend_ceiling_banner_state(request)
    show_first_triage_banner, next_triage_fire = _first_triage_banner_state(request, db)
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="board/dashboard.html",
        context={
            "specs": specs,
            "visible": visible,
            "parsed": parsed,
            "rows": rows,
            "history_by_fp": history_by_fp,
            "density": _normalize_density(density),
            "tab": "dashboard",
            "materials_base_url": materials_base_url,
            "discoveries": discoveries,
            "rejections_pending": rejections_pending,
            "active_sources_banner": show_banner,
            "active_sources_default_count": default_count,
            "spend_ceiling_banner": show_ceiling_banner,
            "first_triage_banner": show_first_triage_banner,
            "next_triage_fire": next_triage_fire,
            "triage_launched": bool(triage_launched),
        },
    )


def _active_sources_banner_state(request: Request) -> tuple[bool, int]:
    """Decide whether to show the #603 banner + how many adapters would
    fetch under the default. Returns ``(show_banner, default_count)``.

    Banner shows iff `config/active_sources.txt` is absent AND the
    `active_sources_banner_dismissed` cookie is unset. `default_count`
    is included in the banner copy to give operators a concrete
    "X adapters fetchable" signal even when they choose not to customize.
    """
    if request.cookies.get("active_sources_banner_dismissed") == "1":
        return False, 0
    if _adapter_registry._active_sources_path().exists():
        return False, 0
    # Count adapters that would actually fetch under the default — i.e.,
    # in _DEFAULT_ACTIVE_SOURCES AND is_configured() True.
    default_set = set(_adapter_registry._DEFAULT_ACTIVE_SOURCES)
    count = 0
    for cls in _adapter_registry.REGISTERED_ADAPTERS:
        if cls.name not in default_set:
            continue
        try:
            if cls().is_configured():
                count += 1
        except Exception:
            pass
    return True, count


def _spend_ceiling_banner_state(request: Request) -> bool:
    """Show the spend-ceiling nudge banner iff no ceiling is configured (#671).

    Banner is suppressed when:
    - ``load_spend_ceiling()`` returns a value (ceiling is set), or
    - The ``spend_ceiling_banner_dismissed`` cookie is "1".
    """
    if request.cookies.get("spend_ceiling_banner_dismissed") == "1":
        return False
    return load_spend_ceiling() is None


_FIRST_TRIAGE_WINDOW_HOURS = 48
"""How long after onboarding completion the first-triage banner remains
eligible to show. 48h floor — triage runs daily, so a shorter window risks
expiring the banner before the first cycle has fired for a late-night
onboarder. Cookie-dismiss is the user's escape hatch within the window."""


def _first_triage_banner_state(
    request: Request,
    db: sqlite3.Connection,
) -> tuple[bool, datetime | None]:
    """Show the empty-dashboard "your first triage hasn't run yet" banner (#752).

    Returns ``(show_banner, next_triage_fire)``. The banner is suppressed when:
    - The ``first_triage_banner_dismissed`` cookie is "1".
    - The ``jobs`` table has any rows (first triage produced output already).
    - The onboarding sentinel is missing, or older than
      ``_FIRST_TRIAGE_WINDOW_HOURS`` (we only nudge during the legitimately-
      first-visit window; long-idle users get the standard empty state).
    """
    if request.cookies.get("first_triage_banner_dismissed") == "1":
        return False, None
    job_count = db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    if job_count > 0:
        return False, None
    base_root: Path = request.app.state.base_root
    sentinel = base_root / "data" / ".onboarding-complete"
    if not sentinel.is_file():
        return False, None
    age_seconds = datetime.now().timestamp() - sentinel.stat().st_mtime
    if age_seconds > _FIRST_TRIAGE_WINDOW_HOURS * 3600:
        return False, None
    return True, next_triage_time()


def _rejections_pending_count(db: sqlite3.Connection) -> int:
    """Pending rejection-review queue count for the dashboard widget (#362).

    Tolerates pre-#362 stacks where ``rejection_suggestions`` doesn't exist
    yet by returning 0 — same shape as the analogous notifications guard.
    """
    try:
        return int(db.execute("SELECT COUNT(*) FROM rejection_suggestions WHERE user_action = 'pending'").fetchone()[0])
    except sqlite3.OperationalError:
        return 0


_APPLIED_DEFAULT_SORT = "applied_date"
_APPLIED_BASE_WHERE = "j.stage IN ('applied','interview','offer')"


def _applied_source() -> str:
    """FROM/JOIN clause for Applied — LEFT JOIN audit_log for applied_date."""
    return (
        "FROM jobs j "
        "LEFT JOIN ("
        "  SELECT job_id, MIN(changed_at) AS applied_date "
        "  FROM audit_log "
        "  WHERE field_changed = 'stage' AND new_value IN ('applied','interview','offer') "
        "  GROUP BY job_id"
        ") al ON al.job_id = j.id"
    )


def _applied_query(parsed: ParsedFilters) -> tuple[str, list[object]]:
    specs = filter_registry.APPLIED_COLUMNS
    clauses, params = build_filter_clauses(specs, parsed)
    sort = parsed.sort or _APPLIED_DEFAULT_SORT
    # sort by spec name → use db_expr if defined; else the bare name.
    sort_spec = next((s for s in specs if s.name == sort), None)
    sort_ref = sort_spec.sql_ref if sort_spec else _APPLIED_DEFAULT_SORT
    order = "DESC" if parsed.desc else "ASC"
    sql = (
        "SELECT j.fingerprint, j.title, j.company, j.stage, j.location, j.remote_status, "
        "       j.known_contacts, j.comp_estimate, j.ai_notes, j.user_notes, j.created_at, "
        "       j.url, "
        "       al.applied_date, "
        "       CAST((julianday('now') - julianday(al.applied_date)) AS INTEGER) AS days_since_applied, "
        "       (SELECT SUM(cl.cost_usd) FROM cost_log cl "
        "        WHERE cl.job_id = j.id AND cl.cost_usd IS NOT NULL) AS cost "
        f"{_applied_source()} "
        f"WHERE ({_APPLIED_BASE_WHERE}){clauses} "
        f"ORDER BY {sort_ref} {order}"
    )
    return sql, params


@router.get("/board/applied", response_class=HTMLResponse)
def applied(
    request: Request,
    density: str = Query(default=_DEFAULT_DENSITY),
    db: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    specs = filter_registry.APPLIED_COLUMNS
    parsed = parse_filter_params(specs, request.query_params)
    view_prefs_redirect = _maybe_redirect_to_persisted(request, "applied", parsed, db)
    if view_prefs_redirect is not None:
        return view_prefs_redirect  # type: ignore[return-value]
    _persist_view(db, "applied", parsed)
    sql, params = _applied_query(parsed)
    rows = db.execute(sql, params).fetchall()
    materials_base_url = os.environ.get("FINDAJOB_MATERIALS_BASE_URL", "")
    visible = _resolve_visible(specs, parsed)
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="board/applied.html",
        context={
            "specs": specs,
            "visible": visible,
            "parsed": parsed,
            "rows": rows,
            "density": _normalize_density(density),
            "tab": "applied",
            "materials_base_url": materials_base_url,
        },
    )


_REVIEW_DEFAULT_SORT = "created_at"
_REVIEW_BASE_WHERE = "stage = 'manual_review'"


def _review_query(parsed: ParsedFilters) -> tuple[str, list[object]]:
    specs = filter_registry.REVIEW_COLUMNS
    clauses, params = build_filter_clauses(specs, parsed)
    sort = parsed.sort or _REVIEW_DEFAULT_SORT
    order = "DESC" if parsed.desc else "ASC"
    sql = (
        "SELECT fingerprint, title, company, score_flag_reason, source, user_notes, created_at, stage, url "
        f"FROM jobs WHERE ({_REVIEW_BASE_WHERE}){clauses} "
        f"ORDER BY {sort} {order}"
    )
    return sql, params


@router.get("/board/review", response_class=HTMLResponse)
def review(
    request: Request,
    density: str = Query(default=_DEFAULT_DENSITY),
    db: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    specs = filter_registry.REVIEW_COLUMNS
    parsed = parse_filter_params(specs, request.query_params)
    view_prefs_redirect = _maybe_redirect_to_persisted(request, "review", parsed, db)
    if view_prefs_redirect is not None:
        return view_prefs_redirect  # type: ignore[return-value]
    _persist_view(db, "review", parsed)
    sql, params = _review_query(parsed)
    rows = db.execute(sql, params).fetchall()
    materials_base_url = os.environ.get("FINDAJOB_MATERIALS_BASE_URL", "")
    visible = _resolve_visible(specs, parsed)
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="board/review.html",
        context={
            "specs": specs,
            "visible": visible,
            "parsed": parsed,
            "rows": rows,
            "density": _normalize_density(density),
            "tab": "review",
            "materials_base_url": materials_base_url,
        },
    )


_WAITLIST_DEFAULT_SORT = "w.created_at"
_WAITLIST_BASE_WHERE = "w.stage = 'waitlisted'"


def _waitlist_query(parsed: ParsedFilters) -> tuple[str, list[object]]:
    specs = filter_registry.WAITLIST_COLUMNS
    clauses, params = build_filter_clauses(specs, parsed)
    sort = parsed.sort or "created_at"
    sort_spec = next((s for s in specs if s.name == sort), None)
    sort_ref = sort_spec.sql_ref if sort_spec else _WAITLIST_DEFAULT_SORT
    order = "DESC" if parsed.desc else "ASC"
    sql = f"""
    SELECT w.fingerprint, w.title, w.company, w.relevance_score,
           w.fit_score, w.probability_score, w.interview_likelihood,
           w.location, w.remote_status,
           w.ai_notes, w.user_notes, w.created_at, w.stage, w.url,
           (SELECT j2.title || ' (' || j2.stage || ')'
              FROM jobs j2
             WHERE j2.company = w.company
               AND j2.fingerprint != w.fingerprint
               AND j2.stage IN ('applied','interview','offer','materials_drafted','prep_in_progress')
             ORDER BY j2.stage_updated DESC
             LIMIT 1) AS blocking_app
    FROM jobs w
    WHERE ({_WAITLIST_BASE_WHERE}){clauses}
    ORDER BY {sort_ref} {order}
    """
    return sql, params


@router.get("/board/waitlist", response_class=HTMLResponse)
def waitlist(
    request: Request,
    density: str = Query(default=_DEFAULT_DENSITY),
    db: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    specs = filter_registry.WAITLIST_COLUMNS
    parsed = parse_filter_params(specs, request.query_params)
    view_prefs_redirect = _maybe_redirect_to_persisted(request, "waitlist", parsed, db)
    if view_prefs_redirect is not None:
        return view_prefs_redirect  # type: ignore[return-value]
    _persist_view(db, "waitlist", parsed)
    sql, params = _waitlist_query(parsed)
    rows = db.execute(sql, params).fetchall()
    history_by_fp = build_history_by_fp(rows, fetch_company_history(db))
    materials_base_url = os.environ.get("FINDAJOB_MATERIALS_BASE_URL", "")
    visible = _resolve_visible(specs, parsed)
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="board/waitlist.html",
        context={
            "specs": specs,
            "visible": visible,
            "parsed": parsed,
            "rows": rows,
            "history_by_fp": history_by_fp,
            "density": _normalize_density(density),
            "tab": "waitlist",
            "materials_base_url": materials_base_url,
        },
    )


_REJECTED_DEFAULT_SORT = "rejected_date"
_REJECTED_BASE_WHERE = "j.stage IN ('rejected','not_selected')"


def _rejected_source() -> str:
    # Latest stage-transition into rejected/not_selected per job. audit_log.job_id
    # stores jobs.id (UUID) — match jobs.id, not fingerprint. MAX(changed_at) picks
    # the most recent transition in case a job was rejected, reactivated, and
    # rejected again. See CLAUDE.md §"audit_log timestamp format".
    return (
        "FROM jobs j "
        "LEFT JOIN ("
        "  SELECT job_id, MAX(changed_at) AS rejected_date "
        "  FROM audit_log "
        "  WHERE field_changed = 'stage' AND new_value IN ('rejected','not_selected') "
        "  GROUP BY job_id"
        ") al ON al.job_id = j.id"
    )


def _rejected_query(parsed: ParsedFilters) -> tuple[str, list[object]]:
    specs = filter_registry.REJECTED_COLUMNS
    clauses, params = build_filter_clauses(specs, parsed)
    sort = parsed.sort or _REJECTED_DEFAULT_SORT
    sort_spec = next((s for s in specs if s.name == sort), None)
    sort_ref = sort_spec.sql_ref if sort_spec else "al.rejected_date"
    order = "DESC" if parsed.desc else "ASC"
    sql = (
        "SELECT j.fingerprint, j.title, j.company, j.url, j.stage, j.reject_reason, "
        "       j.id, j.relevance_score, j.location, j.remote_status, j.ai_notes, j.user_notes, j.synthetic, "
        "       CASE j.stage WHEN 'not_selected' THEN 'company' ELSE 'user' END AS rejection_source, "
        "       al.rejected_date "
        f"{_rejected_source()} "
        f"WHERE ({_REJECTED_BASE_WHERE}){clauses} "
        f"ORDER BY {sort_ref} {order}"
    )
    return sql, params


@router.get("/board/rejected", response_class=HTMLResponse)
def rejected(
    request: Request,
    density: str = Query(default=_DEFAULT_DENSITY),
    db: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    specs = filter_registry.REJECTED_COLUMNS
    parsed = parse_filter_params(specs, request.query_params)
    view_prefs_redirect = _maybe_redirect_to_persisted(request, "rejected", parsed, db)
    if view_prefs_redirect is not None:
        return view_prefs_redirect  # type: ignore[return-value]
    _persist_view(db, "rejected", parsed)
    sql, params = _rejected_query(parsed)
    rows = db.execute(sql, params).fetchall()
    materials_base_url = os.environ.get("FINDAJOB_MATERIALS_BASE_URL", "")
    visible = _resolve_visible(specs, parsed)
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="board/rejected.html",
        context={
            "specs": specs,
            "visible": visible,
            "parsed": parsed,
            "rows": rows,
            "density": _normalize_density(density),
            "tab": "rejected",
            "materials_base_url": materials_base_url,
        },
    )


@router.get("/board/rejected/rows", response_class=HTMLResponse)
def rejected_rows(
    request: Request,
    db: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    specs = filter_registry.REJECTED_COLUMNS
    parsed = parse_filter_params(specs, request.query_params)
    _persist_view(db, "rejected", parsed)
    sql, params = _rejected_query(parsed)
    rows = db.execute(sql, params).fetchall()
    materials_base_url = os.environ.get("FINDAJOB_MATERIALS_BASE_URL", "")
    visible = _resolve_visible(specs, parsed)
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="_job_rows_fragment.html",
        context={
            "specs": specs,
            "visible": visible,
            "rows": rows,
            "tab": "rejected",
            "materials_base_url": materials_base_url,
        },
    )


_NOT_SELECTED_BASE_WHERE = "j.stage = 'not_selected'"
_NOT_SELECTED_DEFAULT_SORT = "not_selected_date"


def _not_selected_source() -> str:
    """LEFT JOIN audit_log for the most recent →not_selected transition date.

    Mirrors _rejected_source() but filters to a single stage so the column
    name not_selected_date stays semantically precise.
    """
    return (
        "FROM jobs j "
        "LEFT JOIN ( "
        "  SELECT job_id, MAX(changed_at) AS not_selected_date "
        "  FROM audit_log "
        "  WHERE field_changed = 'stage' AND new_value = 'not_selected' "
        "  GROUP BY job_id"
        ") al ON al.job_id = j.id"
    )


def _not_selected_query(parsed: ParsedFilters) -> tuple[str, list[object]]:
    specs = filter_registry.NOT_SELECTED_COLUMNS
    clauses, params = build_filter_clauses(specs, parsed)
    sort = parsed.sort or _NOT_SELECTED_DEFAULT_SORT
    sort_spec = next((s for s in specs if s.name == sort), None)
    sort_ref = sort_spec.sql_ref if sort_spec else "al.not_selected_date"
    order = "DESC" if parsed.desc else "ASC"
    sql = (
        "SELECT j.id, j.fingerprint, j.title, j.company, j.url, j.stage, "
        "       j.reject_reason, j.relevance_score, j.location, j.remote_status, "
        "       j.ai_notes, j.user_notes, j.synthetic, j.stage_updated, "
        "       al.not_selected_date "
        f"{_not_selected_source()} "
        f"WHERE ({_NOT_SELECTED_BASE_WHERE}){clauses} "
        f"ORDER BY {sort_ref} {order}"
    )
    return sql, params


@router.get("/board/not-selected", response_class=HTMLResponse)
def not_selected(
    request: Request,
    density: str = Query(default=_DEFAULT_DENSITY),
    db: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    specs = filter_registry.NOT_SELECTED_COLUMNS
    parsed = parse_filter_params(specs, request.query_params)
    view_prefs_redirect = _maybe_redirect_to_persisted(request, "not_selected", parsed, db)
    if view_prefs_redirect is not None:
        return view_prefs_redirect  # type: ignore[return-value]
    _persist_view(db, "not_selected", parsed)
    sql, params = _not_selected_query(parsed)
    rows = db.execute(sql, params).fetchall()
    materials_base_url = os.environ.get("FINDAJOB_MATERIALS_BASE_URL", "")
    visible = _resolve_visible(specs, parsed)
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="board/not_selected.html",
        context={
            "specs": specs,
            "visible": visible,
            "parsed": parsed,
            "rows": rows,
            "density": _normalize_density(density),
            "tab": "not_selected",
            "materials_base_url": materials_base_url,
        },
    )


@router.get("/board/not-selected/rows", response_class=HTMLResponse)
def not_selected_rows(
    request: Request,
    db: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    specs = filter_registry.NOT_SELECTED_COLUMNS
    parsed = parse_filter_params(specs, request.query_params)
    _persist_view(db, "not_selected", parsed)
    sql, params = _not_selected_query(parsed)
    rows = db.execute(sql, params).fetchall()
    materials_base_url = os.environ.get("FINDAJOB_MATERIALS_BASE_URL", "")
    visible = _resolve_visible(specs, parsed)
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="_job_rows_fragment.html",
        context={
            "specs": specs,
            "visible": visible,
            "rows": rows,
            "tab": "not_selected",
            "materials_base_url": materials_base_url,
        },
    )


_ARCHIVE_DEFAULT_SORT = "created_at"
_ARCHIVE_PAGE_SIZE = 100


def _archive_query(parsed: ParsedFilters, offset: int, page_size: int = _ARCHIVE_PAGE_SIZE) -> tuple[str, list[object]]:
    specs = filter_registry.ARCHIVE_COLUMNS
    clauses, filter_params = build_filter_clauses(specs, parsed)
    sort = parsed.sort or _ARCHIVE_DEFAULT_SORT
    order = "DESC" if parsed.desc else "ASC"
    # Strip leading " AND " — Archive has no base WHERE.
    where_body = clauses[len(" AND ") :] if clauses else ""
    # #281: Default-exclude rejected rows so the score-5/6 triage workflow doesn't
    # re-show already-rejected rows. Reachable via ?stage=rejected or /board/rejected.
    if "stage" not in parsed.enum:
        where_body = f"{where_body} AND stage != 'rejected'" if where_body else "stage != 'rejected'"
    where_sql = f" WHERE {where_body}" if where_body else ""
    sql = (
        "SELECT fingerprint, title, company, stage, relevance_score, fit_score, "
        "probability_score, location, remote_status, source, url, user_notes, created_at, stage_updated "
        f"FROM jobs{where_sql} ORDER BY {sort} {order} LIMIT ? OFFSET ?"
    )
    params: list[object] = [*filter_params, page_size, offset]
    return sql, params


def _hidden_rejected_count(db: sqlite3.Connection, parsed: ParsedFilters) -> int:
    """#718: count rows that the default-exclude would hide given current filters.

    Mirrors `_archive_query`'s WHERE composition but pins `stage='rejected'`
    instead of the `stage != 'rejected'` default-exclude. Caller is responsible
    for only invoking when `"stage" not in parsed.enum` — otherwise the
    operator's explicit `stage=` choice would be silently overridden here.
    """
    specs = filter_registry.ARCHIVE_COLUMNS
    clauses, filter_params = build_filter_clauses(specs, parsed)
    stripped = clauses[len(" AND ") :] if clauses else ""
    where_body = f"stage = 'rejected' AND {stripped}" if stripped else "stage = 'rejected'"
    row = db.execute(f"SELECT COUNT(*) FROM jobs WHERE {where_body}", filter_params).fetchone()
    return int(row[0]) if row else 0


@router.get("/board/archive", response_class=HTMLResponse)
def archive(
    request: Request,
    density: str = Query(default=_DEFAULT_DENSITY),
    db: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    specs = filter_registry.ARCHIVE_COLUMNS
    parsed = parse_filter_params(specs, request.query_params)
    view_prefs_redirect = _maybe_redirect_to_persisted(request, "archive", parsed, db)
    if view_prefs_redirect is not None:
        return view_prefs_redirect  # type: ignore[return-value]
    _persist_view(db, "archive", parsed)
    sql, params = _archive_query(parsed, offset=0)
    rows = db.execute(sql, params).fetchall()
    has_more = len(rows) == _ARCHIVE_PAGE_SIZE
    materials_base_url = os.environ.get("FINDAJOB_MATERIALS_BASE_URL", "")
    visible = _resolve_visible(specs, parsed)
    # #718: count hidden rejects only when the default-exclude is active. The chip
    # is suppressed at template time when the count is zero.
    hidden_rejected = _hidden_rejected_count(db, parsed) if "stage" not in parsed.enum else 0
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="board/archive.html",
        context={
            "specs": specs,
            "visible": visible,
            "parsed": parsed,
            "rows": rows,
            "density": _normalize_density(density),
            "tab": "archive",
            "next_offset": _ARCHIVE_PAGE_SIZE if has_more else None,
            "materials_base_url": materials_base_url,
            "hidden_rejected_count": hidden_rejected,
        },
    )


@router.get("/board/archive/rows", response_class=HTMLResponse)
def archive_rows(
    request: Request,
    offset: int = Query(default=0),
    db: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    specs = filter_registry.ARCHIVE_COLUMNS
    parsed = parse_filter_params(specs, request.query_params)
    _persist_view(db, "archive", parsed)
    sql, params = _archive_query(parsed, offset=offset)
    rows = db.execute(sql, params).fetchall()
    has_more = len(rows) == _ARCHIVE_PAGE_SIZE
    materials_base_url = os.environ.get("FINDAJOB_MATERIALS_BASE_URL", "")
    visible = _resolve_visible(specs, parsed)
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="board/_archive_rows.html",
        context={
            "specs": specs,
            "visible": visible,
            "parsed": parsed,
            "rows": rows,
            "tab": "archive",
            "next_offset": offset + _ARCHIVE_PAGE_SIZE if has_more else None,
            "materials_base_url": materials_base_url,
        },
    )


# ──────────────────────────────────────────────────────────────────────
# HTMX rows endpoints — each tab renders only its <tbody> rows as
# _job_rows_fragment.html, driven by the filter framework.
# ──────────────────────────────────────────────────────────────────────


@router.get("/board/dashboard/rows", response_class=HTMLResponse)
def dashboard_rows(
    request: Request,
    db: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    specs = filter_registry.DASHBOARD_COLUMNS
    parsed = parse_filter_params(specs, request.query_params)
    _persist_view(db, "dashboard", parsed)
    sql, params = _dashboard_query(parsed)
    rows = db.execute(sql, params).fetchall()
    history_by_fp = build_history_by_fp(rows, fetch_company_history(db))
    materials_base_url = os.environ.get("FINDAJOB_MATERIALS_BASE_URL", "")
    visible = _resolve_visible(specs, parsed)
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="_job_rows_fragment.html",
        context={
            "specs": specs,
            "visible": visible,
            "rows": rows,
            "history_by_fp": history_by_fp,
            "tab": "dashboard",
            "materials_base_url": materials_base_url,
        },
    )


@router.get("/board/applied/rows", response_class=HTMLResponse)
def applied_rows(
    request: Request,
    db: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    specs = filter_registry.APPLIED_COLUMNS
    parsed = parse_filter_params(specs, request.query_params)
    _persist_view(db, "applied", parsed)
    sql, params = _applied_query(parsed)
    rows = db.execute(sql, params).fetchall()
    materials_base_url = os.environ.get("FINDAJOB_MATERIALS_BASE_URL", "")
    visible = _resolve_visible(specs, parsed)
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="_job_rows_fragment.html",
        context={
            "specs": specs,
            "visible": visible,
            "rows": rows,
            "tab": "applied",
            "materials_base_url": materials_base_url,
        },
    )


@router.get("/board/review/rows", response_class=HTMLResponse)
def review_rows(
    request: Request,
    db: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    specs = filter_registry.REVIEW_COLUMNS
    parsed = parse_filter_params(specs, request.query_params)
    _persist_view(db, "review", parsed)
    sql, params = _review_query(parsed)
    rows = db.execute(sql, params).fetchall()
    materials_base_url = os.environ.get("FINDAJOB_MATERIALS_BASE_URL", "")
    visible = _resolve_visible(specs, parsed)
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="_job_rows_fragment.html",
        context={
            "specs": specs,
            "visible": visible,
            "rows": rows,
            "tab": "review",
            "materials_base_url": materials_base_url,
        },
    )


@router.get("/board/waitlist/rows", response_class=HTMLResponse)
def waitlist_rows(
    request: Request,
    db: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    specs = filter_registry.WAITLIST_COLUMNS
    parsed = parse_filter_params(specs, request.query_params)
    _persist_view(db, "waitlist", parsed)
    sql, params = _waitlist_query(parsed)
    rows = db.execute(sql, params).fetchall()
    history_by_fp = build_history_by_fp(rows, fetch_company_history(db))
    materials_base_url = os.environ.get("FINDAJOB_MATERIALS_BASE_URL", "")
    visible = _resolve_visible(specs, parsed)
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="_job_rows_fragment.html",
        context={
            "specs": specs,
            "visible": visible,
            "rows": rows,
            "history_by_fp": history_by_fp,
            "tab": "waitlist",
            "materials_base_url": materials_base_url,
        },
    )


# ──────────────────────────────────────────────────────────────────────
# #277 — Reset view prefs. Auto-save (persist) lives inline in every
# page + /rows GET; explicit reset is the only POST surface.
# ──────────────────────────────────────────────────────────────────────


_URL_TAB_TO_STORAGE: dict[str, str] = {
    "dashboard": "dashboard",
    "applied": "applied",
    "review": "review",
    "waitlist": "waitlist",
    "rejected": "rejected",
    "not-selected": "not_selected",
    "archive": "archive",
}


@router.post("/board/{tab}/reset-view")
def reset_view(
    tab: str,
    db: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> RedirectResponse:
    """Clear the per-tab persisted filter / sort / cols state.

    The Reset-to-defaults link in ``_filters.html`` and the "Clear all"
    link in ``_active_filters.html`` both POST here. 303 redirects to
    the bare ``/board/{tab}`` URL so the page renders with no
    querystring — the cascade then falls through to
    ``ColumnSpec.default_visible``.
    """
    storage_tab = _URL_TAB_TO_STORAGE.get(tab)
    if storage_tab is None:
        raise HTTPException(status_code=404, detail=f"unknown tab: {tab}")
    view_prefs.reset(db, storage_tab)
    return RedirectResponse(url=f"/board/{tab}", status_code=303)


@router.post("/board/{tab}/reset-filter/{name}")
def reset_filter(
    tab: str,
    name: str,
    db: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> RedirectResponse:
    """Drop a single named filter (or ``cols``/``sort``) from persisted state.

    Backs the ✕ buttons on the chip strip in ``_active_filters.html``
    (#844). The previous GET-anchor approach landed in
    ``_maybe_redirect_to_persisted``'s cold-load branch when the chip
    was the last filter, silently snapping the user back to the
    persisted state. POST + explicit reset + redirect to the bare or
    filtered URL avoids the loop entirely.

    Semantics:
    - Reads the persisted query string for the tab (source of truth
      after every URL settle — auto-saved on every page + /rows GET).
    - Removes the named key from a parsed copy of that state. ``name``
      may be a column name (text / numeric_range / enum / date_range),
      the literal ``cols``, or the literal ``sort``.
    - Re-serializes (with ``default_cols`` so cols-matching-defaults
      stays dropped).
    - Empty result -> ``view_prefs.reset`` + 303 to bare ``/board/{tab}``.
    - Non-empty result -> ``view_prefs.save`` + 303 to
      ``/board/{tab}?{new_qs}`` so the URL explicitly carries the
      remaining state.

    Unknown ``name`` is a no-op (404 would be hostile — operators
    upgrading mid-session may hit a stale form). Unknown ``tab`` is
    a 404.
    """
    from dataclasses import replace
    from urllib.parse import parse_qsl

    from findajob.web.filters import parse_filter_params

    storage_tab = _URL_TAB_TO_STORAGE.get(tab)
    if storage_tab is None:
        raise HTTPException(status_code=404, detail=f"unknown tab: {tab}")
    specs = _STORAGE_TAB_SPECS[storage_tab]

    persisted = view_prefs.load(db, storage_tab) or ""
    parsed = parse_filter_params(specs, dict(parse_qsl(persisted, keep_blank_values=False)))

    new_parsed = replace(
        parsed,
        text={k: v for k, v in parsed.text.items() if k != name},
        numeric_range={k: v for k, v in parsed.numeric_range.items() if k != name},
        enum={k: v for k, v in parsed.enum.items() if k != name},
        date_range={k: v for k, v in parsed.date_range.items() if k != name},
        cols=None if name == "cols" else parsed.cols,
        sort=None if name == "sort" else parsed.sort,
    )

    default_cols = _default_cols_for_storage_tab(storage_tab)
    new_qs = view_prefs.serialize(new_parsed, default_cols=default_cols)
    if new_qs:
        view_prefs.save(db, storage_tab, new_qs)
        return RedirectResponse(url=f"/board/{tab}?{new_qs}", status_code=303)
    view_prefs.reset(db, storage_tab)
    return RedirectResponse(url=f"/board/{tab}", status_code=303)
