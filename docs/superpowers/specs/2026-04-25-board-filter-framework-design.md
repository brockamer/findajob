# Board Filter+Sort Framework Design

**Date:** 2026-04-25
**Issue:** [#273](https://github.com/brockamer/findajob/issues/273)
**Follow-up:** [#277](https://github.com/brockamer/findajob/issues/277) — column-visibility UI + persisted prefs

---

## Problem

The dashboard hides 200+ triageable jobs the operator never sees (140 @ score 6, 68 @ score 5 in `scored` stage as of 2026-04-25). Filter is hard-coded `relevance_score >= 7 AND stage IN (scored, manual_review)` plus prep stages. Beyond the dashboard, every other board tab (`/board/applied`, `/review`, `/waitlist`, `/rejected`, `/archive`) has the same brittle pattern — one-off WHERE clause per tab, single `?q=` text-search input, click-to-sort headers, no per-column filtering.

The pipeline has applied to 31 jobs lifetime; score-5/6 has never been triaged. Likely real gems sitting there (a hand-graded sample shows ~12% of score-6 are operator-shape matches — Director-of-NPI, Sr PDM, Production Manager — that the scorer underweighted).

The shortfall isn't dashboard-specific. Applied lacks a days-since-applied filter; Rejected lacks a reject-reason filter; Archive lacks a source filter. We need **one filter mechanism that applies to every board tab** so we don't re-engineer this on the next request.

## Goals

1. **Generic per-column filter framework** that works on all 6 board tabs — dashboard, applied, review, waitlist, rejected, archive — with the same machinery.
2. **Per-column filter affordances** appropriate to data type: text contains (case-insensitive), numeric range (min/max), enum multi-select, date range.
3. **Sort independent of filter** — change sort, filters persist; change filter, sort persists.
4. **All state in URL query string** — bookmarkable, deep-linkable, shareable. No client-only state.
5. **Server-rendered HTML + HTMX** — consistent with existing IA. Alpine.js permitted for ephemeral popover state only.
6. **Generalization-safe** — no operator-specific column lists, title patterns, or company names hardcoded into tracked files.
7. **Future-proof** — column-visibility (show/hide) and per-tab persistence (#277) drop in without refactoring the framework.

## Non-goals (deferred to follow-ups)

- **#277:** Columns ▾ dropdown UI for show/hide; per-tab pref persistence; "Reset to defaults" link.
- Saved filter presets / "named views" (e.g., "Director-only score-5+").
- Drag-to-reorder columns.
- Multi-user-per-stack persistence.
- Per-user authentication (single-user-per-stack today).

## Architecture overview

A small filter-framework package under `findajob.web.filters`:

```
findajob/web/filters/
  __init__.py
  spec.py        # ColumnSpec dataclass + Kind enum
  registry.py    # _DASHBOARD_COLUMNS, _APPLIED_COLUMNS, etc. as ColumnSpec lists
  query.py       # build_filter_query(specs, params, base_where) -> (sql, params)
  url.py         # parse_filter_params(specs, querystring) -> ParsedFilters
                 #   render_filter_param(name, value) -> str (for href construction)
```

The board route handlers shrink. Each tab:
1. Looks up its spec list from `registry`.
2. Calls `parse_filter_params(specs, request.query_params)` to get a typed `ParsedFilters`.
3. Calls `build_filter_query(specs, parsed, base_where=...)` to get parameterized SQL.
4. Renders the same `board/_table.html` template, passing specs + parsed + rows.

The header partial reads specs to render the right inline input or popover trigger per column. The popovers are Alpine.js components in `static/app.css` + a new `static/filters.js`.

### ColumnSpec

```python
@dataclass(frozen=True)
class ColumnSpec:
    name: str            # SQL column reference, e.g. "relevance_score" or "j.applied_date"
    label: str           # display name in <th>, e.g. "Rel"
    kind: Kind           # TEXT | SCORE | INTEGER | ENUM | DATE | COMPUTED
    sortable: bool = True
    filterable: bool = True
    default_visible: bool = True
    enum_values: tuple[str, ...] | None = None  # required when kind=ENUM
    db_expr: str | None = None  # override for computed columns; otherwise = name
```

`COMPUTED` columns (`company_history`, `blocking_app`) are render-only — `filterable=False`, `sortable=False`. They render via existing partials.

`db_expr` lets a column whose `name` is render-friendly map to a real SQL expression. Example: `days_since_applied` on Applied has `kind=Kind.INTEGER` and `db_expr="CAST((julianday('now') - julianday(al.applied_date)) AS INTEGER)"`. Filter clauses substitute the expression directly. `applied_date` similarly uses `db_expr="al.applied_date"` because the value comes from the audit_log LEFT JOIN, not the `jobs` table.

`Kind`:

| Kind | Filter UI | URL params | SQL clause shape |
|---|---|---|---|
| TEXT | inline `<input>` | `?{name}=...` | `LOWER(col) LIKE LOWER(?)` |
| SCORE | inline `<input>–<input>` | `?{name}_min=&{name}_max=` | `col >= ? AND col <= ?` |
| INTEGER | inline `<input>–<input>` | `?{name}_min=&{name}_max=` | `col >= ? AND col <= ?` |
| ENUM | popover checkboxes | `?{name}=a,b,c` | `col IN (?, ?, ?)` |
| DATE | popover from/until | `?{name}_from=&{name}_to=` | `col >= ? AND col <= ?` |

ENUM values are split on `,` with no escape mechanism. All ENUM columns we filter on (`stage`, `source`, `remote_status`, `reject_reason`) are controlled vocabularies whose values do not contain commas; the spec asserts this at registry-load time.

### URL contract

Flat, type-suffixed param names. Multi-select uses comma-separated values. Sort and visibility are separate, non-overlapping namespaces.

```
?title=director                           # TEXT
&relevance_score_min=5&relevance_score_max=10   # SCORE / INTEGER
&stage=scored,manual_review               # ENUM
&applied_date_from=2026-04-01&applied_date_to=2026-04-25  # DATE
&sort=relevance_score&desc=1              # sort (existing convention)
&cols=title,company,relevance_score,created_at   # explicit visible-set override
&density=compact                          # existing (preserved)
```

Param names = column `name`. Suffixes (`_min`, `_max`, `_from`, `_to`) reserved for the filter framework — column names must not end in these (assert at registry-load time).

`?cols=` is an explicit set: if present, it replaces the default-visible set entirely (not additive). Empty value (`?cols=`) is treated as missing. Invalid column names are silently dropped.

### UI: Layout B — hybrid header row

Inline under each column header:
- TEXT: a small `<input>` placeholder="contains…"
- SCORE / INTEGER: two narrow `<input>` joined by `–` for min/max
- ENUM / DATE: a `▾` icon next to the column label that opens a popover

When a column has an active filter, its `<th>` gets a dot indicator (`●▾`) and a subtle background tint so it's visible at a glance.

Below the header, an active-filter chip strip plus a **Copy link** affordance:
```
Stage: scored, manual_review · ✕   Score: 5–10 · ✕   [Clear all]   [🔗 Copy link]
```

The chip strip is a single Jinja partial `_active_filters.html` shared across all tabs, rendered from `ParsedFilters`.

**Copy link** is a small button that writes `window.location.href` to the clipboard via `navigator.clipboard.writeText()` and flashes a 1.5s "Copied!" confirmation. Because every filter / sort / column-visibility change is pushed into the URL via `hx-push-url`, the current href always reflects the rendered view — so "Copy link" produces a bookmarkable + shareable URL with no extra plumbing. Implementation is a vanilla-JS one-liner attached to the button (no Alpine needed; clipboard API is widely supported and fails closed if unavailable).

HTMX behavior: every filter input has `hx-get` to the tab's `/rows` endpoint with `hx-trigger="keyup changed delay:200ms"` (text/numeric) or `hx-trigger="change"` (popover apply). All inputs share `hx-include="[data-filter-input]"` so the URL is rebuilt from the entire current state on every change. The browser URL is updated via `hx-push-url="true"` so reload preserves state and back-button works.

Popovers use Alpine.js (`x-data`/`x-show`) for open/close — ephemeral UI state that doesn't belong in the URL. The popover holds form inputs in scratch state until **Apply** writes them into the hidden inputs that HTMX includes in the next request (which is also what updates the browser URL via `hx-push-url`). **Cancel** discards scratch state and closes the popover. **Clear** removes the values, writes empty strings to hidden inputs, and triggers the request to drop the filter.

### Per-tab visibility defaults

The current tabs surface columns that aren't equally useful. With filters now first-class, defaults are tightened. The hidden ones become reachable via `?cols=` (and via the future #277 dropdown).

| Tab | Visible by default | Hidden (in spec, opt-in) |
|---|---|---|
| Dashboard | Rel, Fit, Likelihood, Title, Company, History, AI notes, Location, Remote, Contacts, Date | Prob, Comp, Stage |
| Applied | Title, Company, Applied, Days, Stage, Notes (user), Contacts, Location, Remote | Comp, AI notes |
| Review | Title, Company, Flag reason, Source, Date | — |
| Waitlist | Title, Company, History, Rel, Fit, Likelihood, Location, Remote, Date, Blocking app | Prob, AI notes |
| Rejected | Title, Company, Reason, Rejected, Source | — |
| Archive | Rel, Title, Company, Stage, Location, Remote, Date, Source | URL |

The defaults are tuned by what the operator actually needs to *decide* on each tab:

- **Dashboard** = "what should I prep next?" — answered by score signals (Rel, Fit, Likelihood) plus the scorer's own reasoning (AI notes, with the existing `cell-text-wrap` truncation + hover tooltip pattern keeping rows scannable). History prevents re-applying to the same company; Contacts flags warm intros. Hide **Prob** (interview_likelihood is the actionable downstream score; probability is one input — keeping both is noise), **Comp** (sparse — most ingest sources don't scrape salary, so the column is mostly empty), and **Stage** (the per-row Status dropdown already exposes current stage; the column is in the spec as `default_visible=False` so it can be filtered via `?stage=...` and toggled on for score-5/6 triage where manual_review rows mix in).
- **Applied** = "where am I in the pipeline, who do I need to nudge?" — answered by Stage + Days + user_notes. Hide **AI notes** (the scorer's pre-application reasoning is no longer actionable once you've applied; user_notes is what you keep returning to) and **Comp** (sparse, matters mostly at offer time when the row is selected for full inspection anyway).
- **Review** = "is this a real prospect or a misclassification?" — every column is decisional. All visible.
- **Waitlist** = "what's deferred and why is it blocked? Should I reactivate?" — Blocking app is the load-bearing column; History + the full scoring trio (Rel + Fit + Likelihood, matching Dashboard's defaults) drive the reactivate decision since this is exactly where the original triage call gets re-evaluated. Hide **Prob** (interview_likelihood subsumes it) and **AI notes** (selected for full inspection on reactivate; the row count is small).
- **Rejected** = "find that one I killed and remember why." All visible.
- **Archive** = "find any historical job by company/title/date." Stage + Source filterable. Hide **URL** — it's already on the Title hyperlink, so the column adds visual noise.

### Default landings (no querystring)

Each tab keeps its current happy-path filter as the **base WHERE** that applies regardless of querystring. User filters layer on top of (intersect with) the base.

| Tab | Base WHERE (always applied) |
|---|---|
| Dashboard | `((relevance_score >= 7 AND stage IN ('scored','manual_review')) OR stage IN ('prep_in_progress','materials_drafted')) AND <dedup-sibling exclusion>` |
| Applied | `stage IN ('applied','interview','offer')` |
| Review | `stage = 'manual_review'` |
| Waitlist | `stage = 'waitlisted'` |
| Rejected | `stage IN ('rejected','not_selected')` |
| Archive | _(none — full table, paginated)_ |

The dashboard's score-7+ default is preserved — operator confirmed (a) over (b) earlier in the session. To surface score-5/6 gems the operator visits `?relevance_score_min=5&stage=scored,manual_review`. (Future #277 dropdown will save this as a one-click choice.)

### Backend query builder

`build_filter_query(specs, parsed, base_where, sort, desc)` returns `(sql, params)`:

```
SELECT <fields-from-specs>
FROM <tab-source>
WHERE (<base_where>) AND <filter-clauses>
ORDER BY <sort> <desc>
```

`<filter-clauses>` is the parameterized AND of all clauses produced by `ParsedFilters`. Empty filters drop out. Column references in filter clauses use `spec.db_expr or spec.name` so JOIN-aliased columns work (Applied/Rejected have `j.` prefixes; Waitlist has `w.`).

The Applied/Rejected JOINs (audit_log → applied_date / rejected_date) move into a tiny per-tab "source" function that returns the FROM/JOIN string. The framework's WHERE composer doesn't need to know about JOIN structure.

### Cascade for state resolution (today and #277-ready)

```
1. URL querystring  (per-request override; bookmarkable)
2. Persisted per-tab pref  (#277 — not in v1)
3. ColumnSpec.default_visible / default filters  (the framework baseline)
```

In v1, we only have layers 1 and 3. The `parse_filter_params` function takes only the request's querystring; layer 2 plugs in via a future `load_persisted_prefs(tab) -> dict` that's merged in before `parse_filter_params` runs.

### Performance

The `jobs` table is ~12k rows on the operator stack. Existing queries already do full-table scans plus a NOT EXISTS subquery for dedup; SQLite handles this in <50ms.

The new framework adds optional WHERE clauses, all on already-indexed columns (`relevance_score`, `stage`, `created_at`, `source`) or LIKE on `title`/`company` (still <100ms on 12k rows). HTMX debounces text inputs at 200ms (existing convention); popover-driven changes only fire on Apply.

We don't precompute filter pools or cache results. If a tab grows past 50ms render time we can add a single query-result memoization in front of the route handler keyed by the full URL; not in v1.

No new indexes required. Existing schema covers it.

### Subsumed code

Removed:
- `_filter_clause(q)` in `findajob/web/routes/board.py` — replaced by per-column TEXT filters.
- `_archive_score_where`, `_archive_select_sql` — replaced by the framework's SCORE clauses.
- The `?q=` text input in `_filters.html` — replaced by per-column TEXT inputs.

Existing `density=compact|expanded` toggle stays as-is — it's view density, not a filter.

### Testing

New tests under `tests/`:
- `test_filter_spec.py` — `ColumnSpec` invariants, suffix-collision assertion at registry load.
- `test_filter_url.py` — `parse_filter_params` round-trips for every Kind; invalid params dropped silently; comma-list edge cases (empty values, single value, trailing comma).
- `test_filter_query.py` — `build_filter_query` produces correct parameterized SQL for each Kind in isolation and combined; base_where preserved; sort param preserved.
- `test_web_board_filters.py` — integration tests per tab: hit `/board/{tab}/rows?...` with various combinations; assert correct rows returned and other rows excluded; assert sort+filter compose.

Existing tests under `test_web_board_*.py` and `test_web_board_sort.py` updated where the URL params changed (`?q=` removed).

Test fixtures must include `jobs.id` (per saved memory `feedback_test_fixtures_jobs_id`) so audit_log JOINs in Applied/Rejected don't silently mask production bugs.

### Migration path

This is a refactor of pure server-rendered code; no DB schema changes, no data migration, no compose changes. The container image rebuilds, restart picks up the new templates and routes. No `migration-required` label needed.

Bookmarks using the old `?q=foo` will silently drop the filter (since `q` is no longer a registered column). Acceptable — the bookmark scheme was internal to one feature and operator confirmed it's superseded.

## Decisions made during brainstorming

- **Filter UI layout: B (hybrid).** Inline inputs for text/numeric; popovers for enums/dates. A (pure inline-everything) didn't scale to 13-column dashboard cleanly across viewports. Same URL contract either way, so this is a pure UI choice that can revisit later.
- **Backend architecture: Approach 1 (declarative ColumnSpec registry).** Existing `_DASHBOARD_COLS = [(display, field), ...]` is already declarative; this enriches the tuple. Approach 3 (Django-style operator-suffix adapter) avoided due to mass-assignment foot-guns. Approach 2 (tab-local helpers) avoided to keep `board.py` from continuing to grow.
- **Computed columns (`company_history`, `blocking_app`):** render-only in v1 — `filterable=False`, `sortable=False`. They don't have obvious filter semantics; can revisit if a real use case appears.
- **Computed columns that DO get filters:** `days_since_applied`, `applied_date`, `rejected_date` (Applied + Rejected tabs).
- **Dashboard default landing: option (a)** — keep the 7+ happy path on cold load. Operator surfaces 5–6 via explicit URL params or via the future #277 dropdown. Decision made after operator hand-graded a 132-job score-6 sample and found ~88% role-shape mismatches that the scorer should be filtering upstream (filed as #276); the UI shouldn't dump that noise on every page load.
- **Column-visibility schema support in v1; UI + persistence in #277.** Schema cost is trivial (`default_visible: bool` + `?cols=` parsing); UI + persistence is where the work is.
- **Copy-link button in v1.** Operator request 2026-04-25 — small UI affordance that writes the current URL (already kept in sync via `hx-push-url`) to the clipboard. No URL contract or framework changes; fits beside the active-filter chip strip.

## Self-review checklist

- [ ] Every Goal maps to a section in the design.
- [ ] No `TBD` / `TODO` placeholders.
- [ ] No internal contradictions (filter cascade, default landings, URL contract).
- [ ] Generic across all 6 tabs (no tab gets special-cased except for its base WHERE + computed columns).
- [ ] Generalization-safe (no operator-specific lists).
- [ ] Compatible with #277 (cascade has the layer-2 hook).
