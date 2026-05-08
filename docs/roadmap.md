# Project roadmap

Canonical phase-narrative for findajob. Captures *why* the work is ordered the way it is — the board captures *what* is being worked on right now.

See [`project-board.md`](project-board.md) for the full division of labor between this doc and the board. Short version: phase ordering, cross-issue decisions, and milestone-level acceptance live here. Issue state, per-issue acceptance, and dependencies live on the board.

If a roadmap fact drifts between this doc and an issue, *this doc wins*.

---

## Active phase — parallel close-out

GA shipped 2026-05-04 (Decision 20); Multi-Tenancy Foundations shipped 2026-05-06 (Decision 21, ahead of due 2026-05-18). The active phase is a parallel close-out of five concurrent open milestones over a ~30-day window before pivoting to Open-Source Launch Readiness.

**Active goal:** ship the five open milestones to their deliverable sentences (see [Post-GA horizon](#post-ga-horizon) below for status of each), then pivot to OS Launch Readiness (#377).

The most strategically-anchoring milestone of the five is **Funnel + Triage UX** — its deliverable is closest to the operator's daily apply-gate goal. The other four (Cost + Credentials Hardening, OpenRouter Native Migration, Tuning Loop + Stats, Ops Hardening) ship in parallel as cost-correctness, infrastructure, and tuning substrate.

---

## Shipped milestone: General Availability

[Milestone 4 · closed 2026-05-04, ahead of due 2026-05-12](https://github.com/brockamer/findajob/milestone/4)

**Goal.** A second person (the first external tester) runs the pipeline independently in a different field. Config layer fully externalized, user docs written, onboarding flow exists.

**Milestone-level acceptance** (all must hold to close):

1. External tester is deriving daily utility — not merely testing.
2. Her data survives code updates with zero admin action on her part.
3. Web frontend has retired Sheets reads (Dashboard/Applied/Review/Waitlist), writes (STATUS/REJECT_REASON), manual JD ingest, and Google Drive materials viewing.
4. Apply-gate stayed met on average during the arc (daily applies ≥ 1/day averaged across any 7-day window).

### Remaining scope

Phase labels were retired in Decision 17 (2026-04-29) — work is now organized by milestone with deliverable sentences, not by phase. The phase-arc that got GA to its current near-shippable state is preserved in shipped-and-retired form below for historical context.

Currently open in GA: a stripped-down sprint focused on tester-onboarding correctness. Most polish/follow-up was reassigned to other milestones in the 2026-05-01 structural review (see Decision 19).

```
GA sprint scope (post-2026-05-01 reshape):
  - #345  Discoverer regex matches onboarding profile.md schema (real bug)
  - #84   excluded_employers config + prefilter enforcement
  - #76   Docs refresh for Docker deploy
  - #275  gmail_linkedin docs expansion
  - #373  Tracking: dave/judy/tango onboarding (acceptance proof)
```

### Shipped phases (historical)

```
Phase 1 (shipped)          Config externalization + frontend evaluation
Phase 2 (shipped)          Docker migration + release management
Phase 2.5 (shipped)        v0.1.0 tag cut (48h dogfood gate)
Phase 3 (shipped)          Web materials viewer (retired rclone + Drive)
Phase 4 (shipped)          First-tester deployment (alice 2026-04-30; papa 2026-04-29)
Phase 5 (shipped)          Web-frontend sub-phases: read-only views,
                           STATUS/REJECT workflows, manual JD ingest, stats/trends
Phase 6 (in-flight)        User-facing documentation — partially shipped via /docs/;
                           remaining sweep tracked under #76 + #275
```

Phase ordering rationale preserved in Decisions 8 (Phase 3 before Phase 4) and 11 (onboarding interview critical path to Phase 4).

### Decisions

Append-only. Numbers are stable references. Amend an entry in place only for factual corrections (renamed file, etc.); supersede with a new entry otherwise.

1. **rclone replacement — tabled.** Accepted pending the Phase 3 materials viewer, which retires the rclone layer entirely. No tactical hardening pass in the interim.
2. **Beta-tester deployment workflow — image-pull cycle.** Bind-mounted `state/` subtree for data, image pulls for code. Operator's Claude Code session (on his laptop, SSHed into `<deployment-host>`) runs `docker compose pull && docker compose up -d` for each stack. Tester data never at risk from updates.
3. **Auth — the perimeter VPN perimeter only.** No session auth in the app, no TLS code, no public exposure. See [`deployment-model.md`](deployment-model.md) for topology.
4. **Timeline — 3–5 weeks full arc.** Contingent on: scope holds, apply-gate stays met daily, no surprise complexity in the board-write pivot (14c / #61 — `poll_flags.py` replaced by web handlers + `watchdog.py` 2026-04-22).
5. **Phase 1 parallelizes** via `superpowers:dispatching-parallel-agents` + ralph-loop (mechanical config-externalization work) + `frontend-design` (Phase 5 viewer).
6. **Registry + image-tagging:** GHCR. `:main-<sha>` on merge, `:latest` floating, `:v<x.y.z>` on git tag, plus moving alias `:v0.1` repointed on each patch. Testers pin to the minor alias, auto-accept patches; operator dogfoods `:latest`.
7. **Release ownership:** Claude orchestrates releases (tagging, CHANGELOG, notes, migration markers, dogfood verification); user reviews and approves. See [`release-process.md`](release-process.md).
8. **Phase reorder — Phase 3 before Phase 4.** Shipping a beta tester without materials access was a fake win. The web materials viewer was promoted to Phase 3 so the tester's first pull has working materials access with zero rclone on her side. Operator keeps rclone through the Docker migration (env-var gated) — deleted when Phase 3 lands.
9. **aichat-ng build strategy:** pull prebuilt musl binary from `blob42/aichat-ng` GitHub Releases pinned to a tag; no Rust toolchain in image. Fallback to source build is a one-line change if the fork goes stale.
10. **Scheduler inside container:** `supercronic` running a crontab that mirrors the old systemd timers 1:1. Timezone set per instance via `TZ=America/Los_Angeles`. On-demand scripts via `docker compose exec`.
11. **Onboarding interview — Phase 4 prerequisite.** Hand-curating a second tester's candidate config is untenable; the interview is critical path to Phase 4.
12. **v0.1.0 dogfood gate discipline:** during the 48h observation on `:latest`, any merge to `main` restarts the clock. Phase 3 + Phase 4 work happens on unmerged branches.
13. **User-facing documentation — split planned.** The umbrella scope is too broad for one issue. Plan: three focused issues, each paired with the shipping work that makes the doc concrete — (a) day-1 Dashboard usage guide paired with Phase 4 tester deploy, (b) tuning guide paired with first feedback-calibration cycle, (c) troubleshooting guide paired with first real tester failure.
14. **Post-GA milestone split (2026-04-24).** Post-GA Hardening grab-bag replaced with four dated release milestones (v1.1 / v1.2 / v1.3 / v1.4 — see "Post-GA horizon" below). Driven by a structural review: the single grab-bag milestone couldn't pass the one-sentence-deliverable test, and the Roadmap view showed no visible arc past GA. v1.4 (Funnel + Triage UX) precedes v1.2/v1.3 because active-operator triage friction is the binding constraint on the daily job-search loop.
15. **Umbrella-epic convention (2026-04-24).** GitHub's native sub-issue field is the canonical parent-child relationship for grouping related work (example: #228 tuning loop → #229/#230/#231; #239 credentials hygiene → #67/#225; #240 cost observability → #48/#87). Epics are `enhancement`-labeled, Medium priority, and live on the board like any other issue — not labels, not milestones. Milestones are release boundaries; epics are thematic groupings that may span milestones.
16. **Optional HTTP Basic Auth for internet-exposed instances (2026-04-28, supersedes #3 for the public-exposure case).** A FastAPI middleware (`findajob.web.auth`) gates the entire web UI behind shared-secret HTTP Basic Auth when `FINDAJOB_AUTH_USER` / `FINDAJOB_AUTH_PASS` env vars are both set, with `/healthz`, `/static/*`, and `/favicon.ico` allowlisted. When env vars are unset the middleware is a no-op — the perimeter VPN-only deployments are unchanged. Intended for per-tester instances at `findajob-{tester}.example.com`; defends against drive-by scanning, not against a determined adversary. Real per-user identity / RBAC remains out of scope (still a future change). See `docs/operations/internet-exposure.md` (#327).
17. **Multi-tenancy promoted from wishlist to v0.9 milestone, post-GA dates compressed, phase labels retired (2026-04-29).** Structural review on 2026-04-29 made four changes. (a) Multi-tenancy was reframed from "far-future wishlist" to urgent — operator has multiple testers ready to onboard but the platform isn't quite there yet. New milestone v0.9 — Multi-Tenancy Foundations (between GA and v1.1) anchored by [Epic] #338, parenting #330 / #333 / #336 / #339. (b) All post-GA milestone dates compressed 2-3x to reflect actual ~80-issue/week shipping pace observed in the prior 14 days. (Dates further compressed in Decision 18 the same day — see below.) (c) Phase labels (`phase-4` / `phase-5` / `phase-6`) retired entirely — vestigial after Decision 14's milestone-based reorganization; stripped from all open issues and deleted from the repo. (d) PII drift cleanup: GA milestone description previously named the operator's first beta tester; redacted to generic "the first external tester" per the lowercase-handle convention adopted 2026-04-28 in CLAUDE.local.md. Six closed-but-stuck items (#15, #16, #86, #88, #252, #303) bulk-moved to Done; #228 renamed with `[Epic]` prefix per Decision 15.
18. **Second date compression + version-codename convention recorded (2026-04-29 — same day as #17).** Structural review later 2026-04-29 made four changes. (a) Dates compressed again, this time pulled into a single ~30-day window targeting last milestone (v1.3) at 2026-05-29. New dates: GA 2026-05-12; v0.9 2026-05-18; v1.1 2026-05-22; v1.4 2026-05-25; v1.2 2026-05-27; v1.3 2026-05-29. Justification: the previous compression in #17 was still conservative relative to actual shipping cadence; #17's dates were anchored on calendar-month feel rather than throughput. (b) Version-numbering convention recorded: chronological release order is GA → v0.9 → v1.1 → v1.4 → v1.2 → v1.3 — v1.4 ships before v1.2 / v1.3 per Decision 14's "funnel-first" framing. **Versions are codenames, not semver** — readers should not assume v1.2 ships after v1.1. (c) Bundle of mechanical drift fixes and milestone moves applied: #186 (TLS+auth proxy) removed from GA — Decision 16 already shipped Basic Auth as the practical answer, remaining scope is far-future big-idea no-milestone. #344 (multi-tenant scheduler stagger) moved v1.3→v0.9 (it's multi-tenant work). #211 / #212 / #215 (onboarding paste-back polish) moved v1.4→v0.9 (strengthens v0.9's onboarding deliverable). #275 (gmail_linkedin docs) moved v1.4→GA (paired with tester onboarding). #345 (discoverer regex bug) given GA milestone (real bug affecting tester onboarding output). #150 (`/tools/` page) lost vestigial `big-idea` label (concrete Phase 1 implementation already in CLAUDE.md). (d) v1.4 deliverable rewritten to span both halves: "More job sources flow into the funnel, and the operator's daily triage loop makes every candidate row actionable in one click with prior-application context inline." Title was already "Funnel + Triage UX" but description was triage-only.

20. **Structural review (2026-05-04): GA shipped + minor reshape.** Five changes. (a) **GA milestone closed** — all 5 stripped-down sprint issues (#345, #84, #76, #275, #373) shipped; alice + papa onboarded and deriving daily utility per the milestone-level acceptance from §1. Active milestone shifted to Multi-Tenancy Foundations (the next-due milestone after GA). (b) Three real-work milestone-orphans assigned: #429 (reject-reason taxonomy generalization + drift fix) → Funnel + Triage UX; #428 (CLAUDE.md data-ownership table) → Ops Hardening; #425 (cron stagger collisions) → Multi-Tenancy Foundations (sibling of #344). (c) Drift fix: #342 (programmatic NotebookLM investigation) downgraded from Priority=High → Priority=Low to reconcile with its `big-idea` label (per project-board.md "big-idea always pairs with Priority=Low"). (d) Date-clustering risk surfaced for awareness: 3 milestones now stack within 4 days (Funnel+Triage 5-25, Tuning Loop 5-27, Ops Hardening 5-29) — realistic at observed shipping cadence but fragile to a slip cascade. No date adjustment proposed. (e) Future arc confirmed orphan: #378 (outcome-driven self-tuning) and #379 (cross-user signal aggregation) stay `big-idea` + milestone-orphan until OS Launch readiness (#377) ships and the post-launch arc can crystallize. Dependency graph remains clean (5 edges, 4 issues, 0 cycles, 0 priority inversions).

19. **Structural review (2026-05-01): rename milestones, scope-tighten GA, file future arc.** Five changes. (a) Milestones renamed to drop `v0.9 / v1.1 / v1.2 / v1.3 / v1.4` codename prefixes after v0.9.x release tags began shipping work from the v0.9 milestone — the codename-vs-semver collision called out in Decision 18 had become an actual naming bug. New names: `Multi-Tenancy Foundations`, `Cost + Credentials Hardening`, `Funnel + Triage UX`, `Tuning Loop + Stats`, `Ops Hardening`. Semver release tags continue floating on the release-process schedule independently. (b) GA scope-tightened from 10 issues to 5 (#345, #84, #76, #275, #373). Six polish/follow-up issues moved to other milestones: #181/#126/#301 → Ops Hardening; #283/#287 → Multi-Tenancy Foundations; #85 → Funnel + Triage UX. GA acceptance criteria already largely met by alice (deployed 2026-04-30) + papa (2026-04-29). (c) Five real-work milestone-orphans assigned: #358 → Funnel + Triage UX; #359/#360 → Ops Hardening; #362 → Funnel + Triage UX (has fresh design spec from today); #372 → Multi-Tenancy Foundations. #373 promoted from `tracking`-labeled to `enhancement,open-source` and assigned to GA — getting dave/judy/tango onboarded IS the GA acceptance test. (d) Phase 5/6 framing in GA Active milestone section retired per Decision 17; replaced with "Remaining scope" + "Shipped phases (historical)" subsections. (e) Future arc filed as three big-idea issues on the board: #377 (Open-source launch readiness epic), #378 (Outcome-driven self-tuning), #379 (Cross-user signal opt-in telemetry). Far-future wishlist section refreshed to point at issue numbers and to acknowledge that "Multi-tester scaling" was promoted out of wishlist on 2026-04-29. (f) **Reverted later same day:** an earlier draft of this decision retired the `Up Next` and `In Progress` Status columns. Reverted because the jared plugin is designed around the full `Backlog → Up Next → In Progress → Blocked → Done` flow; flattening to `Backlog → Done` broke alignment with the plugin's flow primitives. Sticking with the canonical 5-column model.

21. **Structural review (2026-05-06): MTF shipped + OpenRouter Native Migration milestone added.** Four changes. (a) **Multi-Tenancy Foundations closed** ahead of due 2026-05-18 — alice / papa / dave / judy / tango all onboarded with isolated credentials; in-app onboarding chat, multi-tenant operator dashboard, per-stack Gmail IMAP, cron stagger all landed. Active framing shifted to **parallel close-out** of five concurrent open milestones (CCH, OpenRouter Native Migration, Funnel + Triage UX, Tuning Loop + Stats, Ops Hardening); singular `## Active milestone:` section retired in favor of `## Active phase — parallel close-out`. (b) New milestone **[OpenRouter Native Migration](https://github.com/brockamer/findajob/milestone/12)** (m12, due 2026-05-10) anchored by epic #469 with Phase 1/2/3 children #470/#471/#472. Deliverable: aichat-ng fully replaced by `findajob.llm.openrouter`; prompt caching active on Opus-4.7; `cost_log.cost_usd` from `response.usage.cost`; calibration multiplier stack retired. Subsumes #463 in Phase 2; eliminates the heuristic-+-multiplier hack from Decision-log neighbors #87/#467. Tight 4-day window deliberately chosen by operator. (c) Drift fix: 4 unmilestoned non-big-idea issues triaged — #446 (emergent reject-category prompt) → Funnel + Triage UX; #462 (dead cost-extraction code) → CCH; #463 left open as tracker (will close when #471 lands); #434 ("[BUG] No Dark Mode" satire) closed as joke. (d) Date-cluster risk re-noted from Decision 20: 3 milestones still stack 5-25/5-27/5-29; OpenRouter Native Migration adds a 4-day milestone at 5-10. Operator chose to leave dates alone — observed shipping cadence supports it, but slip-cascade fragility persists. Dependency graph clean (4 edges, 3 issues, 0 cycles, 0 priority inversions). 11 unmilestoned items remaining are all `big-idea` or `personal` (correctly orphaned per project-board.md convention).

### Scope out (explicit)

- Per-user identity / RBAC inside findajob — Decision 16 added shared-secret auth, not identity.
- Separate rclone-replacement project.
- Manual RAG source document editing.
- Alternative LLM provider exploration.

---

## Shipped and retired milestones

- **Reliable Materials** (m1) — shipped; all acceptance criteria closed.
- **Expanded Coverage** (m2) — retired 2026-04-18; scope pivoted into the generalization work (first-tester beta) now tracked under General Availability.
- **Feedback Loop v2** (m3) — closed 2026-04-18; initial scope shipped.
- **Multi-Tenancy Foundations** (m10) — shipped 2026-05-06, ahead of due 2026-05-18 (Decision 21). Anchored by epic #338. Per-tester key isolation (#339), in-app onboarding chat (#336), multi-tenant operator dashboard (#333), Gmail IMAP per-stack credentials (#330), cron stagger (#344), onboarding paste-back polish (#283/#287/#372), discoverer schema fix (#345). alice / papa / dave / judy / tango all onboarded with isolated credential paths.

## Post-GA horizon

Post-GA Hardening was a single undated grab-bag milestone through 2026-04-24; a structural review that day split it into four dated release milestones so the Roadmap view renders past GA. The 2026-04-29 reviews added Multi-Tenancy Foundations between GA and Cost + Credentials Hardening, and twice compressed dates — see Decisions 17 and 18 — landing at the current ~30-day window. The 2026-05-01 review (Decision 19) renamed milestones to drop the v0.9/v1.1/etc. prefixes, eliminating the codename-vs-semver collision that had emerged once v0.9.x release tags started shipping while the v0.9 milestone was still mid-flight. Deliverable sentences are authoritative — if a proposed issue doesn't fit exactly one, it belongs in NO_MILESTONE (big-idea) or in a new milestone, not wedged into an existing one.

**Milestone names are now deliverable-anchored, not version-numbered.** Chronological release order is GA → Multi-Tenancy Foundations → OpenRouter Native Migration → Cost + Credentials Hardening → Funnel + Triage UX → Tuning Loop + Stats → Ops Hardening (Decisions 18 + 21). Funnel + Triage UX ships before Tuning Loop / Ops Hardening per Decision 14's "funnel-first" framing. Semver release tags (v0.9.x, v0.10.x, v1.0.0, …) float on the release-process schedule independently of milestone names.

- **[OpenRouter Native Migration](https://github.com/brockamer/findajob/milestone/12)** (due 2026-05-10) — "aichat-ng is fully replaced by `findajob.llm.openrouter`; prompt caching is active on Opus-4.7 calls; `cost_log.cost_usd` reads from `response.usage.cost` and the calibration multiplier stack is retired." Anchored by epic #469 with Phase 1/2/3 children #470/#471/#472. Filed and milestoned 2026-05-06 per Decision 21; subsumes #463 in Phase 2.
- **[Cost + Credentials Hardening](https://github.com/brockamer/findajob/milestone/6)** (due 2026-05-22) — "The user sees per-job and per-week LLM spend in-app, and no plaintext API key lives on disk." Anchored by umbrella epics #239 (credentials hygiene) and #240 (cost observability). Picked up #462 in the 2026-05-06 reshape.
- **[Funnel + Triage UX](https://github.com/brockamer/findajob/milestone/9)** (due 2026-05-25) — "More job sources flow into the funnel, and the operator's daily triage loop makes every candidate row actionable in one click with prior-application context inline." Scheduled ahead of Tuning Loop / Ops Hardening because funnel/UX friction is the active-operator pain. Picked up #85 / #358 / #362 in the 2026-05-01 reshape and #446 in the 2026-05-06 reshape.
- **[Tuning Loop + Stats](https://github.com/brockamer/findajob/milestone/7)** (due 2026-05-27) — "The pipeline recommends scorer tunes from user-behavior metrics, and /stats/* dashboards show precision, outcome, recall, and cost trends over time." Anchored by epic #228 (data-driven tuning loop) with C.0/C.1/C.2 children.
- **[Ops Hardening](https://github.com/brockamer/findajob/milestone/8)** (due 2026-05-29) — "Fresh-install smoke is CI-gated, pipeline.jsonl rotates, DB schema migrates cleanly, and folder/DB drift is detectable on demand." Can ship in parallel with Tuning Loop + Stats; date is outside-in. Picked up #126 / #181 / #301 / #359 / #360 in the 2026-05-01 reshape.

When a single milestone is the strategic anchor (e.g., Multi-Tenancy Foundations after GA closes), add a `## Active milestone: <name>` section with the same shape (goal, acceptance, remaining scope, decisions, scope-out). Move the outgoing milestone's section up to `## Shipped and retired milestones`. When multiple milestones run concurrently with no single anchor (the post-MTF phase, per Decision 21), use the `## Active phase — parallel close-out` framing instead and list the active milestones in this Post-GA horizon section.

---

## Far-future wishlist

Filed as `big-idea` issues on the board so they're trackable without cluttering the active roadmap. Each becomes worth promoting only after the prerequisite milestone arc has demonstrated real-world signal.

**Multi-tester scaling** — *promoted to active roadmap 2026-04-29 as Multi-Tenancy Foundations milestone (Decision 17).* No longer wishlist.

**Open-source launch readiness** — [#377](https://github.com/brockamer/findajob/issues/377). Public-repo polish: domain-neutral README, external user install guide with zero operator intervention, `GENERALIZATION.md` complete, CI green for external contributors, LICENSE / CONTRIBUTING / CODE_OF_CONDUCT. Promote when Ops Hardening ships and the operator + testers have produced real-world hire-rate signal.

**Outcome-driven self-tuning** — [#378](https://github.com/brockamer/findajob/issues/378). Once Tuning Loop + Stats has accumulated 3–6 months of behavioral signal, evaluate whether the system can propose its own scorer tunes (operator approves with one click, never auto-applied). Substrate is the C.0/C.1/C.2 work in epic #228.

**Cross-user signal (opt-in)** — [#379](https://github.com/brockamer/findajob/issues/379). Once 3+ external testers have multi-month data, evaluate value of opt-in telemetry surfacing career-cluster funnel benchmarks. Privacy model dominates implementation; do not start before testers say "yes please" enthusiastically.

**Hosted variant** — Only if demand materializes from the open-source launch. Not in scope until then. No issue filed.

_(Original wishlist migrated from issue #88, closed 2026-04-21; refreshed 2026-05-01 — concrete items now have issue numbers.)_

---

## Conventions

- **One active milestone at a time** gets a full `## Active milestone` section. When in parallel close-out (multiple milestones shipping concurrently), use `## Active phase — parallel close-out` instead and list the milestones in `## Post-GA horizon` (Decision 21).
- **Phases are a narrative layer on top of issues.** An issue can belong to exactly one phase; a phase is a group of issues that ship together or in close sequence.
- **Issue numbers are not embedded in the phase arc** — they drift. Look up execution units via the milestone page.
- **Decision log is append-only.** Numbers are stable references.
- **Close the meta-issue** that originally held this narrative with a pointer here. The meta-issue was scaffolding for the doc, not a work item.
