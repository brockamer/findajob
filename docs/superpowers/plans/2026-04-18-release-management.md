# Release Management Implementation Plan (#69)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the release-management process documentation (`docs/release-process.md`), the external-user Docker install guide (rewrite of `docs/setup/install-docker.md`), the `migration-required` GitHub label, and supporting updates to `CLAUDE.md` and `CHANGELOG.md` — so that the first real release (`v0.1.0` from #13) can be cut post-merge using a written runbook.

**Architecture:** Docs-only PR. Automation already exists in `.github/workflows/create-release.yml` (auto-generated notes + `migration-required` surfacing) and `.github/workflows/build-image.yml` (tag pushing). This plan fills the process + documentation layer on top.

**Tech Stack:** Markdown, `gh` CLI (for label creation), git.

**Spec:** `docs/superpowers/specs/2026-04-18-release-management-design.md`

**Branch:** `feat/69-release-management` (already created off `origin/main`, spec doc already committed as `714741f`)

---

## File Structure

New / modified files, each with a single clear responsibility:

| File | Responsibility |
|---|---|
| `docs/release-process.md` (**new**) | Claude's release orchestration runbook — dogfood gate, tag mechanics, verification, rollback. 9 sections. |
| `docs/setup/install-docker.md` (**rewrite**) | External-user Docker install + operations guide. Replaces current "(stub)" version. |
| `CLAUDE.md` (**modify**) | Add release-process.md to "Key File Locations"; add "Release Management" subsection before "Working Style". |
| `CHANGELOG.md` (**modify**) | Add `Added` entries under `[Unreleased]` for #69; update Notes line in `[0.1.0] — TBD` block. |
| `migration-required` label (**create on GitHub**) | One-shot side effect via `gh label create`. Documented in release-process.md §5. |

Two-file split of the meat: the **Claude-facing runbook** (`release-process.md`) and the **user-facing operations guide** (`install-docker.md`). They cross-reference each other but serve different audiences — separation prevents one file from trying to do both jobs.

---

## Task 1: Create `migration-required` GitHub label

**Files:**
- Side effect only — creates a GitHub label on the `brockamer/findajob` repo. No file changes in this task.

- [ ] **Step 1: Check label doesn't already exist**

Run: `gh label list --repo brockamer/findajob --search migration-required`
Expected output: empty (no existing label).

- [ ] **Step 2: Create the label**

Run (note: GitHub enforces a 100-char max on label descriptions — the original spec phrasing is shortened):
```bash
gh label create migration-required \
  --repo brockamer/findajob \
  --color BF5700 \
  --description "Manual step required (schema/config/crontab/mount/compose-down) before docker compose pull"
```
Expected: no error output on success.

- [ ] **Step 3: Verify the label**

Run: `gh label list --repo brockamer/findajob | grep migration-required`
Expected output (tab-separated):
```
migration-required	Manual step required (schema/config/crontab/mount/compose-down) before docker compose pull	#BF5700
```

- [ ] **Step 4: No commit** — label creation is a GitHub side effect. Note in the PR description that Task 1 ran successfully.

---

## Task 2: Author `docs/release-process.md`

**Files:**
- Create: `/home/brockamer/Code/findajob/docs/release-process.md`

- [ ] **Step 1: Write the full runbook**

Create `docs/release-process.md` with the 9-section structure below. Write fluent prose; do not include section numbering in headers (use the literal titles, not "§4 Dogfood gate").

**Preamble** (3-5 sentences before the first heading):

- What this doc is: the runbook Claude follows when cutting a release of the findajob pipeline's Docker image.
- Who executes it: Claude orchestrates, Daniel reviews and approves the cut (reference `feedback_release_management.md` memory).
- What automation handles: link out to `.github/workflows/create-release.yml` and `.github/workflows/build-image.yml` for the CI side.

**Section 1: Ownership**

One paragraph codifying the split:
- Claude: proposes the cut, drafts CHANGELOG entries, runs the dogfood gate, flags migration markers, writes release notes, executes the tag push, runs post-tag verification, owns rollback.
- Daniel: reviews the proposed cut, approves or requests changes. No authoring required.

**Section 2: Version scheme**

Until 1.0 (pipeline stabilizes), all 0.x releases are unstable. Discipline:
- **Breaking change** → minor bump (0.1.0 → 0.2.0). Examples: schema change requiring DB migration; removed config key; crontab semantics changed.
- **Bugfix / non-breaking addition** → patch bump (0.1.0 → 0.1.1).

Tag taxonomy, explicitly mapped to `build-image.yml`'s push logic:

| Tag | Type | Who pushes | Purpose |
|---|---|---|---|
| `:latest` | moving | `build-image.yml` on every `main` push | dogfood track |
| `:main-<sha>` | immutable | `build-image.yml` on every `main` push | bisecting / precise pinning |
| `:v0.1.0` | immutable | `build-image.yml` on `v*.*.*` tag | pinned release |
| `:v0.1` | moving | `build-image.yml` on `v*.*.*` tag (auto-advances to latest v0.1.x) | recommended user pin |

**Section 3: Pre-release checklist**

Checklist Claude completes before proposing a cut:
- `CHANGELOG.md` `[Unreleased]` block contains entries for every merged PR since the last tag. Verify with `gh pr list --state merged --search "merged:>$(git log -1 --format=%cI <last-tag>)" --json number,title`.
- For each merged PR in that range: check the diff for schema changes, new required env keys, config file moves/renames, crontab schedule changes, bind-mount layout changes, or `docker compose down`-requiring changes. If any found, the `migration-required` label should be applied (see Section 5 criteria). Apply retroactively if missed; the release notes auto-surface labeled PRs regardless of when the label was set.
- Verify `CHANGELOG.md`'s version cross-reference links at the bottom are intact and consistent.

**Section 4: Dogfood gate (48h)**

Binary gate. All six signals must be clean across a continuous 48h window on `:latest` running on Daniel's LXC. If any signal fails, restart the clock after the fix lands on `main` and `:latest` rebuilds.

For each signal, show the command + expected result:

1. **At least two full triage runs completed.** Triage runs once daily at 00:00 PT (07:00 UTC in PDT / 08:00 UTC in PST).
   ```bash
   ssh findajob.lan 'docker compose -f /opt/stacks/findajob-brock/compose.yaml logs scheduler --since 48h' | grep pipeline_complete
   ```
   Expected: at least 2 `pipeline_complete` events.

2. **poll_flags cycles firing every 10 min without exception rows.**
   ```bash
   ssh findajob.lan 'docker compose -f /opt/stacks/findajob-brock/compose.yaml logs scheduler --since 2h' | grep -E 'poll_flags|Traceback'
   ```
   Expected: many `poll_flags` invocations, zero `Traceback` lines. Supercronic prints each job's exit code — all poll_flags exits should be 0.

3. **07:00 UTC daily health-check notify fired on Daniel's phone.**
   Manual: confirm via phone. Or check:
   ```bash
   ssh findajob.lan 'docker compose -f /opt/stacks/findajob-brock/compose.yaml logs scheduler --since 24h' | grep 'health-check'
   ```
   Expected: at least one health-check invocation with exit code 0 in the last 24h.

4. **Sheet1 + Dashboard + Applied tab syncs landed on two consecutive cycles.**
   ```bash
   ssh findajob.lan 'docker compose -f /opt/stacks/findajob-brock/compose.yaml logs scheduler --since 2h' | grep sync_sheet
   ```
   Expected: at least two `sync_sheet` invocations, zero non-zero exit codes.

5. **form-ingest runs clean.**
   ```bash
   ssh findajob.lan 'docker compose -f /opt/stacks/findajob-brock/compose.yaml logs scheduler --since 24h' | grep -E 'ingest_form|Traceback'
   ```
   Expected: many `ingest_form` invocations (every 30 min), zero tracebacks.

6. **No stack traces in scheduler logs across the 48h window.**
   ```bash
   ssh findajob.lan 'docker compose -f /opt/stacks/findajob-brock/compose.yaml logs scheduler --since 48h' | grep -c Traceback
   ```
   Expected: `0`.

If all six signals pass across the continuous 48h window, the gate is cleared and Claude may propose the cut.

**Section 5: `migration-required` label criteria**

Claude applies this label at PR-open time when the PR contains any of the following. Daniel can challenge in review if a label was applied incorrectly.

Triggers (from the spec's Q2-B list):
- SQLite schema changes (new tables, new columns, new indexes on existing tables, type changes, dropped columns).
- New *required* `state/data/.env` keys — not optional overrides, not new feature toggles that default to off.
- Moves/renames of files in `state/config/` that break existing stacks.
- Crontab schedule changes that shift when a timer fires (a bugfix to a broken cronline like #75 is NOT a schedule change — it's a correction).
- Changes to bind-mount layout in `ops/compose.yaml.example`.
- Changes that require `docker compose down` before `docker compose pull && up -d` (e.g., network config change, removing a service and adding it back).

If unsure: don't apply. Re-labeling post-merge works — the release notes generator picks up the label whenever it's set.

**Section 6: CHANGELOG workflow**

When cutting `v0.x.y`:

1. Under `## [Unreleased]`, Claude drafts the moved entries to their destination block. Group by Keep-a-Changelog categories (`Added`, `Changed`, `Deprecated`, `Removed`, `Fixed`, `Security`). Reference PRs with `(#NNN)`.
2. Replace the `## [Unreleased]` header with `## [x.y.z] — YYYY-MM-DD`. Date is ISO 8601, PT (convert server UTC to PT per `feedback_pt_user_calendar.md` — `-7h` PDT, `-8h` PST).
3. Insert a fresh empty `## [Unreleased]` block at the top of the file.
4. At the bottom, add the version cross-reference link: `[x.y.z]: https://github.com/brockamer/findajob/releases/tag/vx.y.z`. Update the existing `[Unreleased]` line to `[Unreleased]: https://github.com/brockamer/findajob/compare/vx.y.z...HEAD`.
5. Commit with message `docs: move [Unreleased] → [x.y.z] in CHANGELOG (#69 process)`.
6. Wait for the PR to merge, then for `:latest` to rebuild off the CHANGELOG commit before tagging — this ensures the tagged image *is* the code described in the notes.

**Section 7: Tag cut mechanics**

After the CHANGELOG commit is on `main` and `:latest` has rebuilt:

```bash
cd /home/brockamer/Code/findajob
git fetch origin
git checkout main
git pull
# Sanity-check that CHANGELOG.md is current:
grep -c "## \[${VERSION}\]" CHANGELOG.md   # Expected: 1
git tag "v${VERSION}"
git push origin "v${VERSION}"
```

This triggers `build-image.yml` (pushes `:v${VERSION}`, `:v${MAJOR_MINOR}`, `:latest`) and `create-release.yml` (creates the GitHub Release).

**Section 8: Post-tag verification**

Within ~10 minutes of the tag push:

1. Check the GitHub Release page at `https://github.com/brockamer/findajob/releases/tag/v${VERSION}`. Title is `v${VERSION}`. If any PRs in the range were labeled `migration-required`, the "⚠️ Action required before upgrade" section appears at the top.
2. `gh run list --workflow=create-release.yml --limit 1` — status `completed`, conclusion `success`.
3. `gh run list --workflow=build-image.yml --limit 1` — status `completed`, conclusion `success`.
4. Verify the image is pullable from the LXC:
   ```bash
   ssh findajob.lan "docker pull ghcr.io/brockamer/findajob:v${VERSION}"
   ```
   Expected: clean pull, no 401/404.

If any of 1-4 fail, see Section 9 (Rollback) or re-run failing workflow jobs manually.

**Section 9: Rollback**

If post-tag verification fails OR a regression is reported post-release:

1. Identify the last-known-good immutable tag, e.g., `v${VERSION_PREV}`.
2. Rollback via moving-alias re-point (users pinned to `:v${MAJOR_MINOR}` get the prior image on next `docker compose pull`):
   ```bash
   # Must have docker login to ghcr.io first: echo $GHCR_PAT | docker login ghcr.io -u brockamer --password-stdin
   docker pull ghcr.io/brockamer/findajob:v${VERSION_PREV}
   docker tag ghcr.io/brockamer/findajob:v${VERSION_PREV} ghcr.io/brockamer/findajob:v${MAJOR_MINOR}
   docker push ghcr.io/brockamer/findajob:v${MAJOR_MINOR}
   ```
3. The immutable `:v${VERSION}` (bad) tag stays pinned — users who specifically pinned to it can be diagnosed.
4. Document in `CHANGELOG.md` by adding a `## [Reverted] — YYYY-MM-DD` block near the top (above `[Unreleased]`, below any intervening entries) naming the bad tag and the reason.
5. Notify external users (Amy, etc.) via whatever channel is active.

**First-release note (for v0.1.0 specifically):**
There is no prior tag to roll back to. If `v0.1.0` ships broken, the only recovery is "stop publishing; fix forward on main; cut v0.1.1 as soon as the dogfood gate clears." Don't attempt to revive an older `:latest` by ad-hoc re-tagging — the immutable `:main-<sha>` tags are the audit trail.

- [ ] **Step 2: Verify the file renders and covers all 9 sections**

Run:
```bash
grep -cE '^## ' docs/release-process.md
```
Expected: `9` (or `10` including a preamble-level H2 if the author chose to add one — acceptable as long as all 9 required sections exist).

Run:
```bash
for section in "Ownership" "Version scheme" "Pre-release checklist" "Dogfood gate" "migration-required" "CHANGELOG workflow" "Tag cut mechanics" "Post-tag verification" "Rollback"; do
  grep -c "$section" docs/release-process.md && echo "  ✓ $section" || echo "  ✗ $section MISSING"
done
```
Expected: each section name appears at least once.

- [ ] **Step 3: Commit**

```bash
git add docs/release-process.md
git commit -m "$(cat <<'EOF'
docs: add release-process.md runbook (#69)

Codifies the 48h dogfood gate (6 observable signals), the
migration-required label criteria (schema/config/crontab/mount/compose-down
triggers), the CHANGELOG [Unreleased]→[x.y.z] workflow, tag cut mechanics,
post-tag verification, and rollback procedure.

Claude owns release orchestration per feedback_release_management.md
memory; Daniel reviews and approves the cut.
EOF
)"
```

---

## Task 3: Rewrite `docs/setup/install-docker.md`

**Files:**
- Modify: `/home/brockamer/Code/findajob/docs/setup/install-docker.md`

- [ ] **Step 1: Replace stub header and preamble**

The current file starts with:
```markdown
# Docker Install (stub)

> **Full deploy guide is being authored under #69.** This page documents just enough to stand up a stack today. When #69 ships, `docs/release-process.md` and a complete install walkthrough land here.
```

Replace with:
```markdown
# Docker Install

This is the install + operations guide for external users running findajob from the prebuilt `ghcr.io/brockamer/findajob` image via Docker Compose. Claude's release orchestration runbook lives separately at [`docs/release-process.md`](../release-process.md).
```

- [ ] **Step 2: Keep existing sections 1-6 verbatim**

Sections to preserve without changes:
- "Who this is for"
- "Prerequisites on the Docker host"
- "Prerequisites for your Claude Code helper (for the admin)"
- "1. Create the stack directory"
- "2. Drop in the compose template and env"
- "3. Populate `state/`"
- "4. Initial auth: Gmail (optional)"
- "5. Deploy"
- "6. Verify"

- [ ] **Step 3: Replace the current "Updating" and "Troubleshooting" sections with the new operations material**

Remove the existing two sections:
```markdown
## Updating

```bash
docker compose pull && docker compose up -d
```

Or click **Pull** + **Deploy** in Dockge.

## Troubleshooting

See GitHub issues or open a new one at https://github.com/brockamer/findajob/issues.
```

Insert the following three new sections before a preserved (shortened) Troubleshooting section:

```markdown
## Tag pinning strategy

`FINDAJOB_IMAGE_TAG` in your `.env` controls which image Docker Compose pulls. Pick based on how much change tolerance you want.

| Value | Mutability | Recommended for |
|---|---|---|
| `v0.1` | moving (auto-advances to latest `v0.1.x` patch) | **Default.** Most users. Auto-accepts bugfixes; breaking changes require an explicit `.env` edit. |
| `v0.1.0` | immutable | Pin exactly when you need a known-good version and can't afford surprises (e.g., during an active job-hunt push). |
| `latest` | moving (tip of `main`) | Dogfood track. Daniel runs this on his LXC to exercise releases before tagging. May break. |
| `main-<sha>` | immutable (one tag per commit on `main`) | Precise pinning or bisecting when diagnosing a regression. |

Switching between tags is a one-line `.env` edit followed by `docker compose pull && docker compose up -d`.

## Updating

Before running `docker compose pull && docker compose up -d`:

1. Check the [latest GitHub Release](https://github.com/brockamer/findajob/releases/latest) for an "⚠️ Action required before upgrade" section at the top of the notes.
2. If the section is present, follow each linked PR's migration notes before pulling.
3. If the section is absent, a straight pull-and-up is safe:
   ```bash
   cd /opt/stacks/findajob-<you>/
   docker compose pull
   docker compose up -d
   ```
   Or click **Pull** + **Deploy** in Dockge.

The "Action required" section is driven by PRs labeled `migration-required` (see [`docs/release-process.md`](../release-process.md) for the criteria). If a release has no such PRs in its range, the section won't appear.

## Rolling back locally

If a pull broke your stack and you need to get back to a working state immediately:

1. Edit `.env` to pin to a prior immutable tag, e.g.,
   ```
   FINDAJOB_IMAGE_TAG=v0.1.0
   ```
2. Re-deploy:
   ```bash
   docker compose pull
   docker compose up -d
   ```
3. Report the regression via a GitHub issue so the shared `:v0.1` alias can be rolled back globally (Daniel's call — see [release-process.md Rollback section](../release-process.md#rollback)).

A local rollback via `.env` pin doesn't affect other users on `:v0.1`.

## Troubleshooting

- Container fails to start: `docker compose logs scheduler` usually points at the issue.
- Supercronic prints "schedule invalid": a crontab syntax error. Check `ops/crontab` for recent changes.
- Gmail ingestion silently disabled: re-run `docker compose --profile setup run --rm gmail-auth` to refresh the token.
- For anything else, open an issue at https://github.com/brockamer/findajob/issues.
```

- [ ] **Step 4: Verify the rewrite**

Run:
```bash
grep -c 'stub' docs/setup/install-docker.md
```
Expected: `0` (no stub references remain).

Run:
```bash
for section in "Tag pinning strategy" "Updating" "Rolling back locally" "Troubleshooting"; do
  grep -c "^## ${section}$" docs/setup/install-docker.md && echo "  ✓ $section" || echo "  ✗ $section MISSING"
done
```
Expected: each returns 1.

Run:
```bash
grep -c 'migration-required' docs/setup/install-docker.md
```
Expected: `≥ 1`.

- [ ] **Step 5: Commit**

```bash
git add docs/setup/install-docker.md
git commit -m "$(cat <<'EOF'
docs: rewrite install-docker.md for external users (#69)

Replaces the stub with a full operations guide: tag pinning strategy
(v0.1 default, v0.1.0 pin, latest dogfood, main-<sha> bisect), updating
workflow anchored to the release-notes "Action required" section, and
local rollback via .env tag pin.

Claude's orchestration-side runbook lives separately at
docs/release-process.md.
EOF
)"
```

---

## Task 4: Update `CLAUDE.md`

**Files:**
- Modify: `/home/brockamer/Code/findajob/CLAUDE.md`

- [ ] **Step 1: Add `docs/release-process.md` to "Key File Locations"**

Locate the "Quality" block near the end of the "Key File Locations" fenced code block (around line 167-171, preceded by the `# ── Quality ──` comment line). Insert a new "Operations" block before it:

Find this block:
```
# ── Quality ─────────────────────────────────────────────────────────────────
<repo>/pyproject.toml                       # deps, pytest, ruff, mypy config
<repo>/tests/                               # 430 unit tests (pytest)
<repo>/.github/workflows/ci.yml            # CI: ruff + mypy + pytest on every push
```

Insert above it:
```
# ── Operations ──────────────────────────────────────────────────────────────
<repo>/docs/release-process.md              # Claude's release orchestration runbook — dogfood gate, tag cut, rollback
<repo>/docs/setup/install-docker.md         # external-user Docker install + operations guide

```

Resulting order: `Output & logs` → `Operations` → `Quality`.

- [ ] **Step 2: Add a "Release Management" subsection before "Working Style"**

Locate the `## Working Style` heading (around line 319). Immediately before it, insert:

```markdown
## Release Management

Docker image releases follow [`docs/release-process.md`](docs/release-process.md). Claude owns orchestration (dogfood gate, CHANGELOG drafting, tag cut, post-tag verification, rollback); Daniel reviews and approves the proposed cut. The dogfood gate is a binary 48h window on `:latest` — six observable signals must all be clean before any `v*.*.*` tag is pushed. PRs containing schema/config/crontab/mount/compose-down changes get the `migration-required` label at PR-open time so that release notes surface them for external users.

---

```

- [ ] **Step 3: Verify**

Run:
```bash
grep -c 'docs/release-process.md' CLAUDE.md
```
Expected: `≥ 2` (one in the file-locations block, one in the Release Management subsection).

Run:
```bash
grep -c '^## Release Management$' CLAUDE.md
```
Expected: `1`.

Run:
```bash
# Ensure Key File Locations structure is still well-formed
grep -cE '^# ── ' CLAUDE.md
```
Expected: `≥ 7` (one more than before — should include the new "Operations" comment).

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md
git commit -m "$(cat <<'EOF'
docs: add Release Management section + file-locations entry to CLAUDE.md (#69)

Points future sessions at docs/release-process.md for the 48h dogfood
gate, migration-required label criteria, and the CHANGELOG workflow.
EOF
)"
```

---

## Task 5: Update `CHANGELOG.md`

**Files:**
- Modify: `/home/brockamer/Code/findajob/CHANGELOG.md`

- [ ] **Step 1: Add `Added` entries under `[Unreleased]`**

Locate the `## [Unreleased]` block (currently empty — line 11-12). Insert:

Find:
```markdown
## [Unreleased]

## [0.1.0] — TBD
```

Replace with:
```markdown
## [Unreleased]

### Added
- `docs/release-process.md` — Claude-facing release orchestration runbook: 48h dogfood gate, CHANGELOG workflow, tag cut mechanics, post-tag verification, rollback (#69)
- `docs/setup/install-docker.md` — full external-user Docker install + operations guide replacing the stub (#69)
- `migration-required` GitHub label for PRs needing post-pull manual steps; auto-surfaced by `create-release.yml` in "Action required" section of release notes (#69)
- `CLAUDE.md` "Release Management" subsection pointing future sessions at the runbook (#69)

## [0.1.0] — TBD
```

- [ ] **Step 2: Update the Notes line in `[0.1.0] — TBD`**

Find this line in the existing `## [0.1.0] — TBD` block's "Notes" section:
```markdown
- Release management process itself is tracked in #69; once that ships, the
  process doc lives at `docs/release-process.md`.
```

Replace with:
```markdown
- Release management process is documented in `docs/release-process.md` and
  followed for this cut (#69).
```

- [ ] **Step 3: Verify CHANGELOG integrity**

Run:
```bash
# Unreleased block has at least one Added entry
awk '/^## \[Unreleased\]/,/^## \[0\.1\.0\]/' CHANGELOG.md | grep -c '^- '
```
Expected: `4` (the four Added bullets).

Run:
```bash
# Version cross-reference links at bottom intact
tail -5 CHANGELOG.md | grep -cE '^\[.+\]: https://github.com'
```
Expected: `2` (the `[Unreleased]` and `[0.1.0]` links).

Run:
```bash
# No accidental double-header
grep -c '^## \[Unreleased\]' CHANGELOG.md
```
Expected: `1`.

- [ ] **Step 4: Commit**

```bash
git add CHANGELOG.md
git commit -m "$(cat <<'EOF'
docs: log release-management additions in CHANGELOG (#69)

Records the release-process.md runbook, the install-docker.md rewrite,
the migration-required label, and the CLAUDE.md subsection under
[Unreleased]. Updates the [0.1.0] — TBD Notes line to reflect that #69
has shipped.
EOF
)"
```

---

## Task 6: Whole-feature verification + runbook dry-run for v0.1.0

**Files:**
- No file changes. Produces a review artifact posted as a PR comment or in-session report.

- [ ] **Step 1: Re-verify all six acceptance criteria from issue #69**

Run each check and record the result:

```bash
# 1. CHANGELOG.md exists at repo root with Keep-a-Changelog format
test -f CHANGELOG.md && head -10 CHANGELOG.md | grep -q 'Keep a Changelog' && echo "✓ (1)" || echo "✗ (1)"

# 2. create-release.yml auto-creates a GitHub Release with notes + migration-required surfacing
test -f .github/workflows/create-release.yml && grep -q 'migration-required' .github/workflows/create-release.yml && echo "✓ (2)" || echo "✗ (2)"

# 3. docs/release-process.md covers dogfood gate, tag mechanics, verification, rollback
test -f docs/release-process.md && \
  grep -q 'Dogfood gate' docs/release-process.md && \
  grep -q 'Tag cut mechanics' docs/release-process.md && \
  grep -q 'Post-tag verification' docs/release-process.md && \
  grep -q 'Rollback' docs/release-process.md && \
  echo "✓ (3)" || echo "✗ (3)"

# 4. docs/setup/install-docker.md covers pinning, updating, reading release notes
test -f docs/setup/install-docker.md && \
  grep -q 'Tag pinning strategy' docs/setup/install-docker.md && \
  grep -q 'migration-required' docs/setup/install-docker.md && \
  ! grep -q 'stub' docs/setup/install-docker.md && \
  echo "✓ (4)" || echo "✗ (4)"

# 5. migration-required GitHub label exists and is documented
gh label list --repo brockamer/findajob --search migration-required --json name --jq '.[0].name' | grep -q '^migration-required$' && \
  grep -q 'migration-required' docs/release-process.md && \
  echo "✓ (5)" || echo "✗ (5)"

# 6. First release (v0.1.0) cut using this process
#    Acceptance criterion is satisfied POST-MERGE. At PR-open time this is a forward promise;
#    flag it in the PR description and close #69 only after v0.1.0 is published.
echo "⚠ (6) — satisfied post-merge; see Task 7 / post-merge step"
```

Expected: checks 1-5 print `✓`. Check 6 prints the reminder.

- [ ] **Step 2: Runbook dry-run against pending v0.1.0**

Execute Sections 1-6 of `docs/release-process.md` on paper. Produce a report (to be included in the PR description or posted as a comment):

a. **Target tag:** `v0.1.0`.

b. **PR range to summarize:** Since this is the first release, there is no prior tag. Pull the full list of merged PRs:
```bash
gh pr list --state merged --repo brockamer/findajob --limit 100 --json number,title,mergedAt --jq '.[] | "#\(.number) \(.title)"'
```

c. **Draft CHANGELOG diff — [Unreleased] → [0.1.0] — 2026-04-18:** Show the proposed rearrangement of entries (including #69's additions on top of #13's existing `[0.1.0] — TBD` block).

d. **Migration-required PRs in range:**
```bash
gh pr list --state merged --label migration-required --limit 50 --json number,title --repo brockamer/findajob
```
Expected for v0.1.0: empty. This is the first release; there is no prior version to migrate from.

e. **Proposed tag command:**
```bash
git tag v0.1.0
git push origin v0.1.0
```
(Not executed in this task — executed only after PR merge + user approval. See Task #5 in the task list.)

f. **Runbook coverage check:** For each of the 6 acceptance criteria from #69, point at the specific section of `docs/release-process.md` that implements it:
- Criterion 1 (CHANGELOG format) → already satisfied by existing CHANGELOG.md; Section 6 (CHANGELOG workflow) documents the process.
- Criterion 2 (CI auto-creates GH Release) → already satisfied by `create-release.yml`; Section 8 (Post-tag verification) documents how to confirm it ran.
- Criterion 3 (release-process.md coverage) → Sections 4, 7, 8, 9 of release-process.md itself.
- Criterion 4 (install-docker.md coverage) → Task 3 of this plan.
- Criterion 5 (migration-required label) → Section 5 of release-process.md; Task 1 of this plan creates the label.
- Criterion 6 (first release cut) → Task 5 of the overall session task list (post-merge).

- [ ] **Step 3: No commit** — Task 6 is a verification step, not a code change. The dry-run report goes in the PR description.

---

## Task 7: Open the PR

**Files:**
- No file changes. GitHub-side action.

- [ ] **Step 1: Push the branch**

```bash
cd /home/brockamer/Code/findajob
git push -u origin feat/69-release-management
```

- [ ] **Step 2: Open PR with a body that includes the dry-run report from Task 6**

```bash
gh pr create --title "Release management process + Docker install guide (#69)" --body "$(cat <<'EOF'
## Summary
- Adds `docs/release-process.md` — Claude's 48h-dogfood-gate release runbook (Sections: Ownership, Version scheme, Pre-release checklist, Dogfood gate, migration-required label criteria, CHANGELOG workflow, Tag cut mechanics, Post-tag verification, Rollback).
- Rewrites `docs/setup/install-docker.md` for external users — adds Tag pinning strategy, Updating (with "Action required" guidance), Rolling back locally.
- Creates the `migration-required` GitHub label and documents its criteria.
- Adds a "Release Management" subsection and file-locations entry to `CLAUDE.md`.
- Adds `Added` entries under `[Unreleased]` in `CHANGELOG.md` and updates the `[0.1.0] — TBD` Notes line.

Closes #69 (after the v0.1.0 cut ships, which is a post-merge follow-up task).

## Runbook dry-run for v0.1.0
<paste the Task 6 Step 2 report here — including the full PR list, the draft CHANGELOG diff, the migration-required count (expected 0), and the proposed tag command>

## Acceptance criteria (from #69)
- [x] CHANGELOG.md exists at repo root with Keep-a-Changelog format
- [x] CI workflow auto-creates a GitHub Release with notes + migration-required surfacing on `v*` tag push
- [x] docs/release-process.md covers dogfood gate, tag mechanics, verification, rollback
- [x] docs/setup/install-docker.md covers pinning, updating, reading release notes
- [x] migration-required GitHub label exists and is documented
- [ ] First real release (v0.1.0 from #13) cut using this process — **satisfied post-merge; Daniel approves before tag push**

## Test plan
- [ ] Review `docs/release-process.md` — does it tell a future Claude session enough to cut a release without improvising?
- [ ] Review `docs/setup/install-docker.md` — would a stranger follow this to stand up a stack and update it safely?
- [ ] Spot-check `CLAUDE.md` changes — file-locations entry + Release Management subsection fit the document's existing structure
- [ ] Confirm the `migration-required` label exists via [the labels page](https://github.com/brockamer/findajob/labels/migration-required)
- [ ] Approve the dry-run's proposed v0.1.0 CHANGELOG layout and tag command
- [ ] After merge: trigger Task 5 from the session task list — Claude runs the runbook's dogfood gate + post-tag verification, user approves, Claude tags v0.1.0

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 3: Verify PR creation**

```bash
gh pr view --json url,state,title --jq '"\(.title) [\(.state)] → \(.url)"'
```
Expected: single line with title, state `OPEN`, URL.

- [ ] **Step 4: No commit** — PR creation is a GitHub side effect.

---

## Documentation Impact

Per `docs/plan-conventions.md`, every doc surface this plan touches:

- **`docs/release-process.md`** — NEW. Task 2.
- **`docs/setup/install-docker.md`** — full rewrite. Task 3.
- **`CLAUDE.md`** — Key File Locations entry + "Release Management" subsection. Task 4.
- **`CHANGELOG.md`** — `[Unreleased]` entries + Notes line tweak in `[0.1.0] — TBD`. Task 5.
- **`CLAUDE.local.md`** — not touched; no PII, no installation-specific state introduced.
- **`README.md`** — not touched in this PR. Prerequisites L35–42 remain native-only; flagged in PR #77's description, folded into #76 or a future fix-up PR.
- **Spec doc (`docs/superpowers/specs/2026-04-18-release-management-design.md`)** — already committed as `714741f`; no amendment expected from this plan. If implementation reveals a flaw in the spec, amend per plan-conventions.md §5.
- **Role prompts / in-code docstrings** — none affected (docs-only PR).
- **`docs/project-board.md`** — not touched. Release management is a separate workflow from board management.

If a subagent discovers an additional surface during implementation (e.g., a README link that points to install-docker.md and would now need updating), add it to this list in the same commit as the fix.

---

## Whole-feature verification gate

Distinct from per-task verification. Before opening the PR, all of the following must pass:

1. `test -f docs/release-process.md && [ "$(wc -l < docs/release-process.md)" -gt 200 ]` — runbook exists and is substantive.
2. All nine required sections present in `docs/release-process.md` (see Task 2 Step 2).
3. `grep -c 'stub' docs/setup/install-docker.md` returns `0`.
4. `gh label list --repo brockamer/findajob --search migration-required` returns the label.
5. `grep -c '^## Release Management$' CLAUDE.md` returns `1`.
6. `grep -cE '### Added' CHANGELOG.md` — at least 2 (one under `[Unreleased]`, one under `[0.1.0] — TBD`).
7. All Task 2-5 commits are present on branch `feat/69-release-management`:
   ```bash
   git log --oneline origin/main..HEAD
   ```
   Expected: 5 commits total — the spec commit (`714741f`) plus one commit each for Tasks 2, 3, 4, 5. Tasks 1, 6, 7 are side effects and do not produce commits.

---

## Self-review checklist

Spec-to-implementation coverage map. Every section of `docs/superpowers/specs/2026-04-18-release-management-design.md` maps to at least one plan task.

| Spec section | Implemented by |
|---|---|
| Goal + In scope | Tasks 1-5 collectively |
| Out of scope | Explicitly not implemented; noted in plan's Documentation Impact |
| Component 1 (release-process.md) | Task 2 (9-section runbook) |
| Component 2 (install-docker.md rewrite) | Task 3 |
| Component 3 (migration-required label) | Task 1 (creation) + Task 2 Section 5 (documentation) |
| Component 4 (CLAUDE.md update) | Task 4 |
| Component 5 (CHANGELOG.md entries) | Task 5 |
| Data flow (release lifecycle + rollback) | Task 2 Sections 3-9 |
| Error handling & edge cases | Task 2 Section 4 (gate restart), Section 8 (workflow failures), Section 9 (rollback + first-release note) |
| Acceptance (whole-feature verification) | Task 6 + this plan's "Whole-feature verification gate" section |
| Ownership & workflow | Task 2 Section 1; Task 4's "Release Management" subsection references it |
| Documentation Impact | This plan's Documentation Impact section (re-derived, not copied) |
| Risks & mitigations | Runbook drift → addressed by "Documentation Sync Rule" memory; dogfood-gate skipping → addressed by Task 2 Section 4 ("binary gate"); install-docker.md staleness → Task 6 Step 1 check 4; label over/under-application → Task 2 Section 5 criteria |

Placeholder scan: no TBDs, no TODOs, no "implement later" in this plan. Every step has concrete commands or full content.

Type/contract consistency: N/A — docs-only plan. File paths match the spec. Section names in Task 2 Step 2's verification match the section names written in Task 2 Step 1.

Scope check: single-PR sized. Six implementation tasks + one verification task + one PR-creation task. All doc-layer. No code, no tests, no schema.
