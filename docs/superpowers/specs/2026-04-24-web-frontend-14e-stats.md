# Web Frontend Phase 14e — Stats, Trends & History Dashboards

**Issue:** [#63](https://github.com/brockamer/findajob/issues/63)
**Parent:** #14 (web frontend arc)
**Spec date:** 2026-04-24
**Status:** DRAFT — vertical slice in flight.

Depends on #60 (shipped) — `base.html`, `_nav.html`, Tailwind-via-CDN + HTMX pattern. Depends on #55 (shipped) — `feedback_stats` events in `logs/pipeline.jsonl` (for the feedback-trends dashboard, **not** the funnel slice this spec ships).

---

## Overview

14e gives the pipeline a visual home: a `/stats/` top-nav group with sub-tabs for each stats view, mirroring how `/board/*` organizes operational views. Rather than ship all six dashboards in one PR, this spec lands the **infrastructure + one vertical slice** — the pipeline funnel — and defers the other five dashboards to follow-up issues that reuse the infra.

The follow-ups are concrete, filed, and reference this spec. #63 closes when the funnel slice ships; the deferred dashboards track on their own issues.

### Acceptance criteria (from the issue — narrowed)

1. `GET /stats/funnel` renders day-by-day stage-transition counts over the last 30 days, with a chart + a data table.
2. `/stats/` is a top-nav group. The sub-tab bar under it mirrors the board sub-tab pattern (active tab via `aria-current="page"` + background highlight).
3. Spec enumerates the five deferred dashboards with a one-line data source + view idea each; follow-up issues are filed and linked from this spec.

**Meta-gates from original #63** (carry over but satisfied by the deferred issues, not this PR):
- Weekly #55 feedback ntfy push can link to a trends page → fulfilled by the feedback-trends follow-up.
- #56 can be closed/reduced → fulfilled by the feedback-trends follow-up.
- #57 can move forward → fulfilled by the feedback-effectiveness follow-up.

---

## Foundational decisions

### D1 — URL IA: `/stats/` as a new top-nav group

The top nav today has Home / Board / Materials / Ingest / Tools / Config / Docs. Stats is a seventh peer, not a `/board/*` sub-tab.

**Rejected:** `/board/stats/` as a sub-tab within Board. Board views are operational (one-row-per-job queues). Stats views are aggregate (one-cell-per-day cohorts). Sharing the board sub-tab bar conflates the two. Users reaching for "how is the pipeline doing?" should not have to land on Dashboard first.

**Consequence:** `_nav.html` gets a new `("/stats/", "Stats")` entry. `test_web_nav.py::test_every_nav_link_resolves` is updated to include `/stats/`.

### D2 — Sub-tab bar reuses the `_tabs.html` pattern from #191

`/stats/` gets its own `stats/_tabs.html` partial — identical structure to `board/_tabs.html`, different tab list. Each stats view includes it.

**Tab list (full set, shipping across this + deferred):**

| Tab | Ships in | Data source |
|---|---|---|
| Funnel | **this PR** (`/stats/funnel`) | `audit_log` stage transitions |
| Feedback | deferred (feedback-trends follow-up) | `logs/pipeline.jsonl` `feedback_stats` events |
| Scoring | deferred (score-distribution follow-up) | `jobs.relevance_score` / `fit_score` histograms |
| Rejections | deferred (reject-reason-breakdown follow-up) | `jobs.reject_reason` + `audit_log` stage='rejected' |
| Throughput | deferred (application-throughput follow-up) | `audit_log` transitions per week |
| Effectiveness | deferred (feedback-effectiveness follow-up) | `jobs.feedback_version` + outcome join |

The deferred tabs render as **disabled** entries in `_tabs.html` (grey, no `href`) until their view ships. This keeps the taxonomy visible from day one and makes each follow-up's surface area obvious.

### D3 — Chart library: Chart.js via CDN

Pipeline.db has <10k jobs; rendering a 30-day × ~9-stage funnel is a 270-cell chart. Chart.js handles this trivially.

- CDN URL: `https://cdn.jsdelivr.net/npm/chart.js@4` — pinned major version, auto-patches.
- No build step (matches the Tailwind-via-CDN pattern from 14b's D-foundations).
- The server renders a `<canvas>` + a `<script>` block with the already-serialized data; no fetch-on-load.
- Graceful degradation: every chart is accompanied by a `<table>` with the same data. JS-disabled clients see the table.

**Rejected:**
- **ApexCharts / Plotly** — bigger footprint (~300KB vs ~60KB); the extra features are unused.
- **uPlot** — lighter still, but API is lower-level and we'd reimplement legends/tooltips Chart.js provides for free.
- **Server-side inline SVG sparklines** — zero-JS but hand-rolling responsive + legends is busywork on a solo timeline.

### D4 — Data layer: query SQLite at render time

The funnel reads `audit_log` directly in the route handler. A 30-day × full-stage count takes <10ms on production-sized data.

**Rejected:**
- **Materialized `stats_daily` table** — requires a refresh job and a schema migration. Premature for a dataset that fits in RAM.
- **Replay `pipeline.jsonl` into a stats DB at request time** — adds I/O, the feedback-trends follow-up can do that for its specific need without spilling into this slice.
- **HTMX polling for live updates** — the pipeline's cadence is daily triage + occasional user actions. Hitting refresh is fine.

**Consequence:** each stats view declares its own SQL in `routes/stats.py` next to the route. No cross-view abstraction yet — when the third view lands, we refactor.

### D5 — Funnel stage ordering

The canonical funnel, top-to-bottom:

```
enriched → scored → manual_review → prep_in_progress → materials_drafted
        → applied → interview → offer
```

Terminal exits (rendered as separate columns, not continuations):
```
rejected          (user rejection; any pre-application stage)
not_selected      (company rejection; any post-application stage)
waitlisted        (deferred)
```

The funnel view counts **transitions into** each stage per day — a row for the day, a column per stage, a cell = count of `audit_log` rows where `field_changed='stage'` and `new_value=<stage>` on that date.

**Rejected:** stage snapshots (how many jobs were in stage X at end of day). Requires windowed inner join on audit_log to recover historical state — ~10× the query complexity for a less actionable view (what flowed where matters more than where things are stuck).

### D6 — Test strategy

- Route smoke test — `/stats/funnel` returns 200 with test fixtures covering one transition per stage.
- SQL correctness — for a synthetic audit_log with known transitions on known dates, the rendered table cell counts match expected values.
- Tab bar — parametrize across every tab path (Funnel is enabled; others are disabled anchors → assert they render but `href` attribute is absent).
- Top-nav regression — `/stats/` added to `test_every_nav_link_resolves`.

---

## Deferred dashboards (each gets its own follow-up issue)

Each follow-up issue links back to this spec and inherits D1–D4. AC for each is a concrete "this chart renders X from query Y" — not the meta-gates from original #63.

### Feedback trends (`/stats/feedback`) — **[#193](https://github.com/brockamer/findajob/issues/193)**

**Data source:** `logs/pipeline.jsonl`, filtered to `event_type='feedback_stats'`.
**View:** this-week and 4-week rolling per-reject_reason counts. Multi-line chart + table.
**Unblocks:** closes / reduces #56.

### Score distribution (`/stats/scoring`) — **[#194](https://github.com/brockamer/findajob/issues/194)**

**Data source:** `jobs.relevance_score`, `jobs.fit_score`, `jobs.probability_score` (where non-null).
**View:** histogram per score type for the last 30 days of scored jobs. Bar chart + table.

### Rejection breakdown (`/stats/rejections`) — **[#195](https://github.com/brockamer/findajob/issues/195)**

**Data source:** `jobs WHERE stage IN ('rejected','not_selected')` + `jobs.reject_reason` + company.
**View:** Per-reason count (global) and per-company top-5. Companion to the `/board/rejected` operational view.

### Application throughput (`/stats/throughput`) — **[#196](https://github.com/brockamer/findajob/issues/196)**

**Data source:** `audit_log` transitions into `applied` / `interview` / `offer` per ISO week.
**View:** Stacked bar chart + table. Answers "how many apps did I send this month?".

### Feedback effectiveness (`/stats/effectiveness`) — **[#197](https://github.com/brockamer/findajob/issues/197)**

**Data source:** `jobs.feedback_version` (stored when the scorer ran against a given feedback snapshot) joined with outcome stage.
**View:** For each feedback_version, what fraction of jobs scored under it reached `materials_drafted` / `applied`? Bar chart.
**Unblocks:** #57.

---

## Files touched by this PR (funnel vertical slice only)

### New
- `src/findajob/web/routes/stats.py` — `GET /stats/` (redirect to `/stats/funnel`), `GET /stats/funnel`.
- `src/findajob/web/templates/stats/_tabs.html` — sub-tab bar.
- `src/findajob/web/templates/stats/funnel.html` — funnel view.
- `tests/test_web_stats_funnel.py` — route + SQL correctness.
- `tests/test_web_stats_tabs.py` — parametrized tab-bar test.

### Modified
- `src/findajob/web/routes/__init__.py` — register `stats.router`.
- `src/findajob/web/templates/_nav.html` — add `("/stats/", "Stats")` entry.
- `tests/test_web_nav.py::test_every_nav_link_resolves` — include `/stats/`.
- `CLAUDE.md` — one-line addition under "Web Frontend Architecture" noting the new `/stats/` group and that deferred dashboards are filed as their own issues.

---

## Documentation Impact

- **CLAUDE.md** — add `/stats/` to the "Grouped URL IA" list under Web Frontend Architecture. One line.
- **CHANGELOG.md** — entry under Unreleased: "feat(web): /stats/funnel dashboard (#63); follow-up dashboards in #193–#197". Also note: #31 + #112 retired (superseded).
- **No migration-required flag** — no schema, no config, no compose, no crontab changes. Pure UI add.
- **Spec self-archival** — on merge, this spec moves to `docs/superpowers/specs/archived/2026-04/` and gains an ARCHIVED header pointing at the merged PR. Deferred follow-up issues keep the link to the archived copy.

---

## Self-review checklist

| Spec section | Task |
|---|---|
| D1 — `/stats/` top-nav | `_nav.html` edit; `test_every_nav_link_resolves` regression |
| D2 — sub-tabs partial | `stats/_tabs.html`, parametrized `test_web_stats_tabs.py` |
| D3 — Chart.js via CDN | `funnel.html` `<script src="cdn.jsdelivr.net/...">` + `<canvas>` + table |
| D4 — query SQLite directly | `routes/stats.py::funnel` inline SQL |
| D5 — stage ordering | `FUNNEL_STAGES` constant in `routes/stats.py` |
| D6 — tests | new test files |
| Deferred dashboards | 5 follow-up issues filed referencing this spec |
| Whole-feature verification | Smoke-render `/stats/funnel` with seeded audit_log; visually verify chart + table agree; confirm navigation between `/stats/` and `/board/*` works in both directions |
