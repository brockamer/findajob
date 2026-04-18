# Release Management Process for Docker Image Distribution — Design

**Issue:** [#69](https://github.com/brockamer/findajob/issues/69)
**Date:** 2026-04-18
**Author:** Claude (Opus 4.7) + Daniel Brock (review)

## Goal

Codify a release process for the containerized findajob pipeline so external users (Amy and future beta testers) can pull new versions without being hit by rolling breakages, and so Claude can execute releases consistently across sessions without improvising the procedure each time.

The automation side of releases already ships in #13 — `build-image.yml` pushes immutable and moving tags on every `main` push and `v*.*.*` tag; `create-release.yml` auto-generates release notes and surfaces PRs labeled `migration-required` in a prominent "Action required" section. This spec covers the **process + documentation** layer that those workflows depend on: when to cut a release, how to verify it's safe, how to write its notes, how to roll back.

## Scope

### In scope

- `docs/release-process.md` — the Claude-facing orchestration runbook. Structure, dogfood gate, migration-label criteria, CHANGELOG workflow, tag mechanics, post-tag verification, rollback.
- `docs/setup/install-docker.md` — full rewrite of the current stub. External-user guide covering tag pinning, updates, reading release notes.
- `migration-required` GitHub label — created on the repo, criteria documented in release-process.md.
- `CLAUDE.md` — one-line addition to "Key File Locations" pointing to `docs/release-process.md`.
- `CHANGELOG.md` — `[Unreleased]` gets a new *Added* entry for #69; the `[0.1.0] — TBD` "Notes" line gets "(#69 shipped)".
- Whole-PR self-review confirms all six acceptance criteria from issue #69 are met.

### Out of scope

- **v0.1.0 cut itself** — executed post-merge per the new runbook, gated on Daniel's approval. Issue #69 closes only after the release is published on GitHub. Covered as a follow-up task after PR merges.
- **Deep Docker operations/architecture rewrite** — owned by [#76](https://github.com/brockamer/findajob/issues/76).
- **`README.md` "Prerequisites" native-only section (L35–42)** — noted in PR #77's description, folded into #76 or a future fix-up PR.
- **Any CI changes** — `create-release.yml` and `build-image.yml` already implement the automation side.

## Components

### 1. `docs/release-process.md` (new, ~350–500 lines)

Sections:

1. **Ownership** — Claude drives release orchestration; Daniel reviews and approves the cut. Anchored to `feedback_release_management.md` memory.
2. **Version scheme** — 0.x semver discipline: breaking changes → minor bump (0.1 → 0.2); bugfixes → patch bump (0.1.0 → 0.1.1). Immutable `v0.1.0` tag vs moving `v0.1` alias explained.
3. **Pre-release checklist** — CHANGELOG `[Unreleased]` has entries for every merged PR since last tag; every such PR is checked for `migration-required` applicability.
4. **Dogfood gate (48h) — the Q1-B checklist.** Exact commands + expected signals:
   - At least two full triage runs completed with `pipeline_complete` event in `logs/pipeline.jsonl`
   - poll_flags cycles firing every 10min without exception rows (verify via `docker compose logs scheduler --since 2h` on the LXC — supercronic prints each job's exit code; zero non-zero exits expected for poll_flags during the window)
   - 07:00 UTC daily health-check notify fired on Daniel's phone
   - Sheet1 + Dashboard + Applied tab syncs landed on two consecutive sync cycles
   - form-ingest runs clean (no "duplicate submission" or API-error entries)
   - `docker compose logs scheduler` shows no stack traces in the window
5. **`migration-required` label criteria — the Q2-B list.** Applied by Claude at PR-open time, challengeable in review. Triggers:
   - SQLite schema changes (new tables, new columns, new indexes on existing tables)
   - New *required* `state/data/.env` keys (not optional overrides)
   - Moves/renames of files in `state/config/` that break existing stacks
   - Crontab schedule changes that shift when a timer fires
   - Changes to bind-mount layout in compose.yaml.example
   - Changes that require `docker compose down` before pulling (rather than plain `pull && up -d`)
6. **CHANGELOG workflow** — how to move entries from `[Unreleased]` to `[x.y.z] — YYYY-MM-DD`. Version cross-reference link format at the bottom. Date format (ISO 8601, in PT per `feedback_pt_user_calendar.md`).
7. **Tag cut mechanics** — `git fetch && git checkout main && git pull`, then `git tag v0.x.y && git push --tags`. Note: requires the CHANGELOG commit to be on `main` already and `:latest` to have rebuilt from it, so the tagged image *is* the code described in the release notes.
8. **Post-tag verification**:
   - GitHub Release page populated correctly (title, notes, migration-required section if any)
   - `create-release.yml` run green
   - `build-image.yml` run green; new tags visible at `ghcr.io/brockamer/findajob/pkgs/container/findajob`
   - `docker pull ghcr.io/brockamer/findajob:v0.x.y` succeeds from Daniel's LXC
9. **Rollback** — if post-tag verification or post-release regression requires reverting:
   - Identify last-known-good immutable tag `v0.x.y-1`
   - `docker pull ghcr.io/brockamer/findajob:v0.x.y-1`
   - `docker tag ghcr.io/brockamer/findajob:v0.x.y-1 ghcr.io/brockamer/findajob:v0.x`
   - `docker push ghcr.io/brockamer/findajob:v0.x`
   - Add `## [Reverted] — YYYY-MM-DD` entry to CHANGELOG.md naming the bad tag and reason
   - Note: immutable `v0.x.y` tag stays pinned so users who pinned specifically can be diagnosed

### 2. `docs/setup/install-docker.md` (rewrite of stub)

Replaces the current "(stub)" file. Sections:

- **Who this is for** + **Prerequisites** — lightly edited from the stub.
- **1. Create the stack directory** — unchanged.
- **2. Drop in the compose template and env** — unchanged.
- **3. Populate state/** — unchanged.
- **4. Initial auth: Gmail (optional)** — unchanged.
- **5. Deploy** — unchanged.
- **6. Verify** — unchanged.
- **Tag pinning strategy** *(new substantive section)* — when to pick each option:
  - `FINDAJOB_IMAGE_TAG=v0.1` — **recommended default.** Moving minor alias. Auto-accepts patch bumps (v0.1.x) on next `docker compose pull`. Breaking changes don't auto-land.
  - `FINDAJOB_IMAGE_TAG=v0.1.0` — immutable tag. Pin exactly when you need a known-good version (e.g., during an active job-hunt push where you can't afford surprises).
  - `FINDAJOB_IMAGE_TAG=latest` — dogfood track. Tip of main, may break. Daniel runs this on his LXC to exercise releases before tagging.
  - `FINDAJOB_IMAGE_TAG=main-<sha>` — immutable commit-sha tag. For precise pinning or bisecting when diagnosing a regression.
- **Updating** *(new substantive section)* — before running `docker compose pull && up -d`, check the latest GitHub Release for the "⚠️ Action required before upgrade" section. If present, follow the linked PR's migration notes before pulling. If absent, a straight `pull && up -d` is safe.
- **Rolling back locally** *(new section)* — edit `.env` to a prior immutable tag (`FINDAJOB_IMAGE_TAG=v0.1.0` if `v0.1.1` broke you) and `docker compose up -d`. This doesn't fix the shared `:v0.1` alias — that's Daniel's to roll back; report the regression so he can.
- **Troubleshooting** — unchanged.

### 3. `migration-required` GitHub label

Created with:
```bash
gh label create migration-required \
  --color BF5700 \
  --description "Changes require a manual step (schema, config, crontab, mount layout, docker compose down) before pulling"
```

Documented in release-process.md §5.

### 4. `CLAUDE.md` update

Add a line under "Key File Locations" referencing `docs/release-process.md`. Existing structure has a "Quality" subsection near the bottom; add a short "Operations" block or append to an adjacent block.

Also add a short subsection to the body of CLAUDE.md under an existing relevant heading (or a new "Release management" heading before "Working Style") that reads approximately:

> ### Release management
> Docker image releases follow `docs/release-process.md`. Claude owns orchestration; Daniel reviews and approves the cut. Dogfood gate is 48h on `:latest` before any `v*.*.*` tag push.

### 5. `CHANGELOG.md`

- Under `## [Unreleased]`, add:
  ```markdown
  ### Added
  - `docs/release-process.md` — Claude-facing release orchestration runbook (#69)
  - `docs/setup/install-docker.md` full external-user guide replacing the stub (#69)
  - `migration-required` GitHub label for PRs needing post-pull manual steps (#69)
  ```
- In the existing `## [0.1.0] — TBD` "Notes" block, update the line that says "Release management process itself is tracked in #69" to "Release management process is documented in `docs/release-process.md` (#69)".

## Data flow — the release lifecycle

The runbook codifies this sequence. Every release is Claude executing it.

```
[Merged PRs accumulate on main]
        │
        │  build-image.yml pushes :latest, :main-<sha> on every main push (already shipped)
        ▼
[:latest on Daniel's LXC via FINDAJOB_IMAGE_TAG=latest]
        │
        │  Claude proposes release cut when changelog is substantive
        ▼
[Dogfood gate — 48h checklist (§4 of runbook)]
        │    ✓ 2+ pipeline_complete events
        │    ✓ poll_flags cycles clean
        │    ✓ 07:00 UTC health-check notify fired
        │    ✓ sheet syncs landed
        │    ✓ form-ingest runs clean
        │    ✓ no stack traces in scheduler logs
        │
        ├── Fail → fix forward on main, restart gate when clean
        │
        ▼
[Claude drafts release cut proposal]
        │    – Which tag (v0.x.y)
        │    – CHANGELOG diff from [Unreleased] → [x.y.z]
        │    – List of migration-required PRs in the range
        │
        ▼
[Daniel reviews proposal, approves or requests changes]
        │
        ▼
[Claude commits CHANGELOG update on main → merges → waits for :latest rebuild]
        │
        ▼
[Claude tags: git tag v0.x.y && git push --tags]
        │
        │  build-image.yml pushes :v0.x.y, :v0.x, :latest
        │  create-release.yml creates GH release with auto-generated notes
        │  + migration-required surfacing (already shipped)
        ▼
[Claude verifies post-tag (§8 of runbook)]
        │    ✓ GitHub Release page looks right
        │    ✓ Both workflows green
        │    ✓ `docker pull ghcr.io/brockamer/findajob:v0.x.y` succeeds from LXC
        │
        ▼
[Users on :v0.x auto-pull on their next `docker compose pull`]
```

**Rollback path** (from post-tag if verification fails or regression reported):

```
[Claude identifies last-known-good tag v0.x.y-1]
        │
        ▼
[docker pull ghcr.io/brockamer/findajob:v0.x.y-1 from LXC]
        │
        ▼
[docker tag + docker push to overwrite :v0.x alias pointer]
        │
        ▼
[CHANGELOG gets a "## [Reverted]" note naming the bad tag and why]
        │
        ▼
[Users on :v0.x get the prior image on next `docker compose pull`]
```

## Error handling & edge cases

- **Dogfood gate fails partway through** — restart the 48h clock after the fix lands on `main` and `:latest` rebuilds. No "it was fine for 40 hours, close enough." The gate is binary.
- **Post-tag workflows fail** — release-process.md §8 documents diagnostics: check `create-release.yml` run logs, check `build-image.yml` tag-push job, manual re-trigger via `gh workflow run`. If the image push succeeded but release-notes failed, re-run that job alone; don't delete and re-push the tag.
- **User review finds a CHANGELOG mistake after tag is pushed** — don't re-tag. Open a correction PR against `[Unreleased]` for future releases; the GitHub Release itself can be edited in-place via `gh release edit v0.x.y --notes-file`.
- **`migration-required` label missed on a PR that needed it** — after merge, label can still be applied; subsequent release notes for the inclusive range will pick it up.
- **Bad release ships** — run the rollback flow. `:v0.x` alias re-pointed; immutable `:v0.x.y` stays pinned so users on the bad tag can be diagnosed.
- **Two releases within 48h** — the gate starts over for each. If a critical bugfix needs to ship inside the window, it's a patch release on the current minor, and the abbreviated gate is allowed *only* if Daniel explicitly approves the exception, documented in the release notes.

## Acceptance (whole-feature verification gate)

Distinct from per-task verification. This is the gate for the #69 PR itself, not for future releases cut via the runbook.

1. `docs/release-process.md` exists, renders cleanly on GitHub, and a read-through against issue #69's six acceptance criteria confirms each is met.
2. `docs/setup/install-docker.md` no longer has the "(stub)" header or the "full guide in #69" banner; every section listed above is present; the file passes a "would a stranger follow this?" review.
3. `gh label list --repo brockamer/findajob` shows `migration-required` with the documented color + description.
4. `CHANGELOG.md` is still valid: version cross-reference links at the bottom intact, `[Unreleased]` and `[0.1.0] — TBD` sections present, new entries under Unreleased for #69.
5. `CLAUDE.md` "Key File Locations" references `docs/release-process.md`, and a body subsection explains release management at a high level.
6. **Functional dry-run of the runbook** — Claude executes Sections 1–6 of the runbook on paper against the pending v0.1.0: shows Daniel the draft CHANGELOG diff, list of migration-required PRs (expected: zero for v0.1.0), and the proposed tag command. If Claude can produce a coherent proposal using only the written runbook, the runbook works. The actual tag push happens post-merge after Daniel's approval.

## Ownership & workflow

Per issue #69 and `feedback_release_management.md`:

- **Claude:** proposes the release cut, drafts CHANGELOG entries, flags migration markers, writes release notes, runs the dogfood gate, executes the tag push, runs post-tag verification, owns rollback.
- **Daniel:** reviews and approves the proposed cut; reviews this spec, the resulting plan, and the implementation PR.

## Documentation Impact

This is the Documentation-Impact checkpoint required by `docs/plan-conventions.md`. Every doc surface this spec touches:

- **`docs/release-process.md`** — NEW. The core deliverable.
- **`docs/setup/install-docker.md`** — full rewrite of current stub.
- **`CLAUDE.md`** — "Key File Locations" entry + short "Release management" subsection.
- **`CHANGELOG.md`** — new entries under `[Unreleased]`, tweak to "Notes" line in `[0.1.0] — TBD` block.
- **`README.md`** — *not* touched in this spec. Prerequisites section L35–42 still reads native-only; flagged in PR #77, folded into #76 or a future fix-up PR.
- **Role prompts / in-code docstrings** — none affected.
- **CLAUDE.local.md** — not touched; no PII, no installation-specific state.

If an additional surface is discovered during implementation, the plan's Documentation Impact section (authored in the next step) amends this list.

## Risks & mitigations

- **Runbook drift** — the runbook is useless if future changes to `build-image.yml` or `create-release.yml` aren't mirrored. Mitigation: CLAUDE.md's "Documentation Sync Rule" memory applies; any PR touching those workflows must also touch release-process.md in the same commit.
- **Dogfood gate gets skipped "just this once"** — the runbook states the gate is binary, and the spec records that exceptions require explicit Daniel approval documented in the release notes. Mitigation: written discipline plus the "Release management is Claude's responsibility" memory.
- **External user reads outdated install-docker.md** — mitigation: the file is baked into the image but also served from `main` on GitHub. Users land on the GitHub copy via the link in the release notes. Keep the two in sync by treating install-docker.md as part of every release's verification.
- **`migration-required` label applied too aggressively** — dilutes the signal. Mitigation: Q2-B criteria codified in the runbook; if unsure, don't label. Re-labeling post-merge is allowed.
- **`migration-required` label missed when it was needed** — users get hit by a breaking change silently. Mitigation: release-process.md §3 (pre-release checklist) includes a per-PR review of the changed files for schema/config/mount changes, catching label misses before the cut.

## Self-review checklist

Spec-to-implementation coverage map, to be validated against the plan's tasks:

| Spec section | Implemented by |
|---|---|
| Component 1 (release-process.md) | Plan task: create `docs/release-process.md` |
| Component 2 (install-docker.md rewrite) | Plan task: rewrite `docs/setup/install-docker.md` |
| Component 3 (migration-required label) | Plan task: `gh label create` + mention in release-process.md |
| Component 4 (CLAUDE.md update) | Plan task: edit CLAUDE.md |
| Component 5 (CHANGELOG.md entries) | Plan task: edit CHANGELOG.md |
| Whole-feature verification gate | Plan's Verification section |
| Documentation Impact | Plan's Documentation Impact section (must re-derive from this spec, not just copy) |

Placeholder scan: no TBDs, no TODOs in this spec. Type/contract consistency: N/A (docs-only). Scope check: contained in a single PR per Q4-A; v0.1.0 cut tracked as a distinct follow-up task after PR merges.
