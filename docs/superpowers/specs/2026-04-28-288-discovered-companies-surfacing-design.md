# Surfacing discovered_companies.md to the operator (#288 — Sections A + B)

**Status:** Design — pre-implementation. Authoritative scope for the merge of #288's Section A (success-path ntfy) and Section B (Dashboard widget). Sections C (weekly diff) and D (promote-to-target) remain split-out follow-ups; not covered here.

**Why this spec exists:** The discoverer ships novel-company suggestions weekly to `candidate_context/discovered_companies.md`. The operator currently has no path to seeing them — no notification on cron completion, no Dashboard mention, no link from anywhere natural. The discoverer is producing value-quality output and no one is looking at it. This spec wires both surfaces in one merge so operators on `:latest` get the file and the visibility on the same release.

---

## Scope

### In scope (this merge)

- **Section A — success-path ntfy.** After `findajob.discoverer.runner.run()` succeeds, send a single ntfy push with the count + top-5 names.
- **Section B — Dashboard widget.** A small banner on `/board/dashboard` showing the count + last-run date + link to the file, sourced from the existing `discovered_companies.json` sidecar.

### Explicitly out of scope (file as separate issues)

- **Section C — weekly diff.** Computing what changed between this week and last; either a written diff file or inlined in the ntfy. Track the previous-run snapshot. Real work; defer.
- **Section D — promote-to-target affordance.** Click-to-add-to-Tier-2 from the discoveries view. Biggest UX work in #288; deserves its own design pass.
- **Schema changes to `discovered_companies.json`.** Treat the existing schema as fixed; widget reads what's there, no new fields. If C or D need a schema bump, they spec it.
- **Replacing the `/config/files/...` link target with a dedicated `/discoveries/` page.** Cheap link-to-config editor is fine for v1; richer page is itself a follow-up.

### Decisions adopted

1. **One PR for A + B, not two.** A and B share an artifact (the JSON sidecar) and a user moment ("did this thing run? where is it?"). Splitting them creates a release where ntfy fires but the link goes to a viewer with no widget, which is worse UX than either alone.
2. **Widget reads JSON, not markdown.** The JSON sidecar already exists and has count + generated_at; parsing markdown is fragile and slow. JSON-or-bust.
3. **Empty-state is rendered, not hidden.** When `discovered_companies.json` is missing (fresh install, never-run), the widget tells the operator the cron will run weekly — better than the absence-of-widget which silently obscures the feature's existence.
4. **Click target is `/config/files/candidate_context/discovered_companies.md` for v1.** The existing config editor renders the file inline; cheap and consistent. Dedicated `/discoveries/` page is a Section D dependency anyway.
5. **Staleness shown as absolute date, not relative.** "(updated 2026-04-26)" rather than "(updated 2 days ago)" — operators reading the dashboard can compute relative trivially, and absolute dates don't go stale-by-rendering as the page sits.
6. **No degrade for the failure case.** Cron failure ntfys already exist (`discovery: timeout`, `discovery: parse error`, etc.); A's success ntfy doesn't suppress or replace them.

---

## Section A — success-path ntfy

### Where it hooks

`findajob.discoverer.runner.run()`, after the existing `commit_atomically(...)` call at runner.py:137 and before the success `return RunResult(...)` at runner.py:151. Already inside the `if ntfy_enabled:` envelope's logical block.

Insert a new conditional ntfy call between the existing `log_event("discovery_complete", ...)` and the cost-threshold check. Order matters: log first (durable), ntfy second (best-effort), cost-threshold check last (overlays the success ntfy with a warning if cost was high).

### Message shape

- **Title:** `findajob: discovered N companies` (literal "findajob:" prefix matches existing failure-ntfy convention; literal `N` substituted with the count)
- **Body:** comma-separated top-5 company names from `parsed.companies[:5]`. If fewer than 5, list all. If zero, body reads `(no novel companies surfaced this run)` — still send the ntfy so cron-silent-success is observable.

Examples:

| Count | Title | Body |
|---|---|---|
| 10 | `findajob: discovered 10 companies` | `Lightmatter, Lambda Labs, Hyperbolic, Nautilus, Giga Infra` |
| 3 | `findajob: discovered 3 companies` | `Anduril, Exoplanet, Relativity Space` |
| 0 | `findajob: discovered 0 companies` | `(no novel companies surfaced this run)` |

### Behavior contract

- Sends iff `ntfy_enabled` is True (existing flag on `run()`; already plumbed through CLI's `--no-ntfy`).
- Best-effort: failure to send must not poison the success path (existing `_send_ntfy` already swallows exceptions per its docstring).
- Idempotent — replays of the same `run()` call would re-send. That's fine; cron only invokes once per scheduled tick.
- Does NOT replace the cost-threshold ntfy. When cost exceeds threshold, both fire (success + warning). Operator wants to see both signals.

### Test surface

In `tests/discoverer/` (existing test directory for discoverer module):

- `test_runner.py` (or new `test_runner_ntfy.py` if scope warrants): on the success path, `_send_ntfy` is called once with a title containing `discovered N` and a body containing the top-5 names. Patch `_send_ntfy` to a recording mock.
- Test the zero-count case explicitly: ntfy still fires, body is the "no novel" sentinel string.
- Test `ntfy_enabled=False`: no call.
- Failure-path tests should NOT regress — confirm existing `discovery: timeout` / `discovery: parse error` ntfys still fire and the new success ntfy does NOT fire on those paths.

### Acceptance for Section A

- [ ] `_send_ntfy` invoked once on `run()` success regardless of count, with title and body matching the spec
- [ ] `--no-ntfy` (passed through `ntfy_enabled=False`) suppresses the new ntfy
- [ ] Existing failure ntfys unchanged
- [ ] Test added asserting all of the above

---

## Section B — Dashboard widget

### Where it renders

A new partial `templates/board/_discoveries_widget.html`, included in `templates/board/dashboard.html` between the page header and the table. Visual treatment: small banner-style block, single line on wide screens, two lines on narrow. Tailwind classes consistent with surrounding dashboard partials (`_tabs.html`, `_status_cell.html`).

### Data source

Read `{base_root}/candidate_context/discovered_companies.json`. New helper in `findajob.web.helpers` (or new module `findajob.web.discoveries` if logic grows past two functions):

```python
def load_discoveries_summary(base_root: Path) -> DiscoveriesSummary | None:
    """Read discovered_companies.json. Returns None if file missing/malformed.

    DiscoveriesSummary fields:
      - count: int
      - generated_at: str (ISO timestamp from the JSON)
      - generated_at_date: str (YYYY-MM-DD slice, for display)
      - days_since: int (computed from generated_at vs now)
      - is_stale: bool (True iff days_since > STALE_THRESHOLD_DAYS)
      - top_names: list[str] (first 5 names from the companies array)
    """
```

`STALE_THRESHOLD_DAYS = 10` — 7 (one cron interval) + 3-day grace. Operator wants visibility into "the cron skipped" but not noise from "the cron ran 8 hours late."

### Visual states

| State | Trigger | Display |
|---|---|---|
| **Fresh** | JSON exists, `days_since ≤ 7` | `🔍 Discoveries: 10 companies (updated 2026-04-26) — view` |
| **This-week-late** | JSON exists, `7 < days_since ≤ 10` | `🔍 Discoveries: 10 companies (updated 2026-04-21) — view` (no warning styling, just absolute date) |
| **Stale** | JSON exists, `days_since > 10` | `🔍 Discoveries: 10 companies — last run 2026-04-12 (15d ago, weekly cron may have skipped) — view` |
| **Empty (never run)** | JSON missing | `🔍 Discoveries: cron runs weekly Sundays — first results will appear here` |
| **Empty (run, zero hits)** | JSON exists, `count == 0` | `🔍 Discoveries: 0 companies this run (updated 2026-04-26) — view` |

`view` is a hyperlink to `/config/files/candidate_context/discovered_companies.md`. (Discovery: confirm the existing config editor's URL pattern matches; if not, fall back to direct file viewer or wait for richer page.)

### Where the widget lives in `dashboard.html`

After the `_tabs.html` include and before the table. The widget is a horizontal strip; on narrow viewports it wraps to a second line. Hidden when there's no dashboard content at all (fresh install) — actually no, render anyway, the empty-state is informative for fresh installs.

### Test surface

In `tests/test_web_board_dashboard.py` (existing) or a new `tests/test_web_discoveries_widget.py`:

- Widget renders with correct count + date when JSON is present and fresh
- Widget renders staleness warning when last run > 10d ago
- Widget renders empty-never-run state when JSON is missing
- Widget renders empty-zero-hits state when JSON has `count: 0`
- Widget link points at the configured config-editor path
- `load_discoveries_summary()` returns None on malformed JSON (defensive — survives a bad weekly run)

### Acceptance for Section B

- [ ] Dashboard renders the widget with count + date on standard happy path
- [ ] Widget renders empty state when JSON is missing (no exception)
- [ ] Widget shows staleness warning when last run > 10d ago
- [ ] Click goes to the rendered file in the config editor
- [ ] All four visual states exercised by tests

---

## Common acceptance (both sections)

- [ ] CHANGELOG `[Unreleased]` Added entry covering A + B in one bullet
- [ ] CLAUDE.md updated where it describes the discoverer (currently mentions the file's existence; should mention the surfacing path now)
- [ ] No new env vars, no schema migration, no scheduler changes — both sections sit on top of existing primitives

## What this spec deliberately does NOT decide

- The exact CSS / Tailwind classes — implementer's call within "consistent with surrounding partials"
- Whether to truncate long company names in the top-5 ntfy body — punt; if a name overflows, ntfy clients handle it
- Server-side caching of the JSON read — premature; a single file read per dashboard request is in the noise compared to the SQL queries the same page already runs
- Whether the widget should auto-refresh — no; dashboards are imperative-pull (operator clicks refresh)
- Promoted-companies tracking — Section D's territory, not this merge

## Provenance

Source: GitHub issue #288 (Sections A + B). Filed by operator on 2026-04-26 after the first manual discoverer run on docker.lan produced 10 novel companies and operator asked "how am I supposed to know this happened?"

Pre-implementation work this spec replaces: ad-hoc design decisions during the implementing PR. With this spec in place, the implementing PR has nothing to relitigate — only to execute.
