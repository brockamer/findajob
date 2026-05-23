# Release Process

This is the runbook Claude follows when cutting a release of the findajob pipeline's
Docker image. Claude orchestrates the release end-to-end — proposing the cut, drafting
the CHANGELOG, running the fresh-install pre-tag smoke check (see §"Pre-tag smoke
check"), writing notes, pushing the tag, verifying the outcome, and owning any
rollback. The user reviews and approves the proposed cut but does not author any
of the release artifacts. This split is codified in the `feedback_release_management.md`
memory.

Two GitHub Actions workflows do the mechanical work once a tag is pushed.
[`.github/workflows/build-image.yml`](../.github/workflows/build-image.yml) builds the
image and pushes the three tags (`:vX.Y.Z`, `:vX.Y`, `:latest`) to GHCR.
[`.github/workflows/create-release.yml`](../.github/workflows/create-release.yml)
generates the GitHub Release page, using GitHub's auto-generated notes and prepending
an "Action required before upgrade" section that surfaces any PRs labeled
`migration-required` in the range. Everything in this doc assumes those two workflows
remain in place; if they change, update this doc in the same commit.

## Ownership

Claude drives every release. That means proposing when to cut, drafting the CHANGELOG
entries from the merged PRs in the range, running the fresh-install pre-tag smoke
check (see §"Pre-tag smoke check"), flagging migration markers on PRs as they come
in (or retroactively if a trigger was missed), writing the release notes content,
executing `git tag` and `git push origin vX.Y.Z`, running post-tag verification
against GitHub Actions and GHCR, and owning rollback if anything goes wrong. The
user's role is review-only: they look at the proposed cut, confirm the smoke-check
output, and approve or request changes. They do not write CHANGELOG bullets, author
the notes, or run the tag commands. If the release breaks something, Claude owns
the recovery.

## Version scheme

Until the pipeline hits `1.0`, all `0.x` releases are considered unstable and the
regular semver guarantees do not apply. Within that window, the versioning discipline
is still meaningful — it tells users whether they can pull blindly or whether they
need to read the notes.

A **minor bump** (`0.1.0` → `0.2.0`) signals a breaking change. Examples that qualify:
a SQLite schema change that needs a migration, a removed config key that existing
stacks still reference, a crontab semantic change (not a bugfix to a broken line, but
a genuine shift in when a job fires or what it does), or any change that forces
existing users to edit their bind mounts or `.env` before pulling the new image.

A **patch bump** (`0.1.0` → `0.1.1`) is for bugfixes and non-breaking additions.
A user on `:latest` can pull and restart without reading anything.

The image tag taxonomy — which determines what a user pulling from GHCR actually gets
— is set by [`build-image.yml`](../.github/workflows/build-image.yml):

| Tag | Type | Who pushes | Purpose |
|---|---|---|---|
| `:latest` | moving | `build-image.yml` on every `main` push | the recommended pin — every stack tracks it |
| `:main-<sha>` | immutable | `build-image.yml` on every `main` push | bisecting, precise pinning for diagnosis |
| `:vX.Y.Z` | immutable | `build-image.yml` on `v*.*.*` tag push | audit trail of what shipped under that tag |
| `:vX.Y` | moving | `build-image.yml` on `v*.*.*` tag push | available for stacks that need to freeze on a specific minor |

Every stack — operator, dogfood, staging, beta testers, Fly — runs `:latest`.
The deploy pass is substrate-aware: docker stacks get
`docker compose pull && up -d && verify_auth` per stack; Fly apps get
`fly deploy --config <fly.toml>` followed by `fly ssh console -C "python -m findajob.web.verify_auth"`.
Per the Post-Launch Tester Sunset arc (milestone m26), the docker per-stack
loop covers the operator's primary stacks plus any beta tester still on
docker; testers drop out of the docker loop as they migrate to self-host
on their own Fly accounts.

## Three-gate dev pipeline (#565)

Three application tiers exist, each with a single clear purpose:

| Tier | Purpose | Pin | Reset trigger |
|---|---|---|---|
| `findajob-clean` | Factory-fresh / NUX walkthrough / structural-migration gate | `:latest` | Per-NUX or per-onboarding-touching release |
| `findajob-staging` | Populated soak under realistic activity, pre-cohort gate | `:latest` | Minor cut + persona-fixture edit |
| `<operator-stack>` | Production | `:latest` | n/a |

Plus the 5 tester stacks, all on `:latest`. Tester stacks share the operator's
release cadence — every deploy reaches every surface in the same operational
pass.

The Post-Launch Tester Sunset arc (milestone m26) progressively migrates the
self-onboarded tester docker stacks (dave / judy / papa / tango) to per-tester
Fly accounts via `findajob.migrate` (see [`tester-migration.md`](tester-migration.md)).
alice remains on operator-administered Fly (operator's account). The operator's
own daily-utility deployment also runs on Fly post-cutover. While the arc is in
flight, the release pass is dual-track: docker tester stacks still in place run
through `§"Docker cohort deploy"`, and the operator's Fly app plus any migrated
testers' Fly apps run through `§"Fly app deploy"`. When m26 closes, the docker
tester loop drops; only `findajob-clean` (factory-reset) and `findajob-staging`
(populated soak) remain on docker — both dev tiers, no production traffic.

The pre-tag checklist becomes:

| Gate | Tier | Validates |
|---|---|---|
| Pre-tag throwaway smoke (`scripts/test_container_integration.sh`) | ephemeral | Single full triage cycle on empty mounts; image boots cleanly |
| Pre-tag `findajob-clean` structural pre-flight | persistent factory-fresh | Migration correctness, onboarding gate, app-boot |
| Pre-tag `findajob-staging` behavioral soak | persistent populated | Triage / scoring / notify / M6-launcher behavior on populated DB |
| Pre-tag parity matrix verification (minor-bump or higher, conditional) | Docker + Fly | Every user-visible surface behaves identically on both substrates — see [`release-parity-matrix.md`](release-parity-matrix.md) |
| Cohort wave per-stack verification | tester + operator | Migration on real populated data, `verify_auth` |

The first three rows are unconditional pre-tag gates on every release. The parity-matrix row is conditional — it gates *minor bumps* (the breaking-change tier under `0.x` semver per [§ Version scheme](#version-scheme)) and every *major* bump once `1.0` lands. Patch releases inherit the prior matrix state; if a patch touches a surface, only that row gets re-verified in the patch PR per the same-PR docs rule.

## Pre-release checklist

Before proposing the cut, Claude runs through this list and reports results to the user.

First, confirm the CHANGELOG is ready. The `[Unreleased]` block at the top of
`CHANGELOG.md` should have an entry for every PR that has merged since the last tag.
To enumerate merged PRs in the range:

```bash
gh pr list --state merged \
  --search "merged:>$(git log -1 --format=%cI <last-tag>)" \
  --json number,title
```

Cross-reference the output against the `[Unreleased]` block. Any PR in the list
without a CHANGELOG entry should either get one (drafted now, committed as part of
the release PR) or be deliberately omitted as internal-only — but that call is rare
and should be justified.

Second, walk each merged PR in the range and check the diff for triggers that warrant
the `migration-required` label. The triggers are enumerated in the *migration-required
label criteria* section below. Anything that forces a manual step on the user's side
before `docker compose pull && up -d` needs the label. If a PR that merged without
the label should have had it, apply it retroactively — the release notes generator
queries labels at release time, so a late label still surfaces in the notes.

Third, sanity-check the CHANGELOG's version cross-reference links at the bottom of
the file. They should follow the existing pattern: `[Unreleased]` compares the latest
tag against `HEAD`, and each released version links to its tag page. If the latest
release added a new link and the previous `[Unreleased]` line wasn't updated to
reference the new tag, the file is inconsistent — fix before cutting.

- For PRs touching `src/findajob/gmail_imap.py` or `src/findajob/fetchers/__init__.py:fetch_gmail_jobs`: re-run `uv run pytest tests/test_transparency_invariants.py -v` and link the green run in the PR description.

- For releases that include any `migration-required` PR touching schema, onboarding, mounts, or the entrypoint: confirm a recent (≤ 1 release cycle) restore exercise has passed against a backup tarball produced by the current image. The procedure is documented in [`operations/restore.md`](operations/restore.md). If no recent exercise is on file, run one before tagging — a backup that has not been restored is not a backup, and a release that breaks restore must not ship.

- **Install templates ship `:latest`.** `ops/fly.toml.example` and `ops/compose.yaml.example` (defaulted via `FINDAJOB_IMAGE_TAG=latest`) both point at `:latest`. Fresh users following `install-fly.md` or `install-docker.md` land on the same image every existing stack runs; no per-release template maintenance is required.

- [ ] **Staging soak (docker substrate).** Ensure `findajob-staging` has been on `:latest` for at least one completed daily triage cycle. Run the green-check from `<deployment-host>`:

      ```
      ssh <deployment-host> 'docker exec -u 1000 findajob-staging-scheduler-1 python -m findajob.staging.green'
      ```

      Must exit 0 before tagging. On non-zero, investigate using the failure summary printed to stderr; either fix and re-run, or document the override justification in the release CHANGELOG entry.

      The Fly-substrate behavioral signal comes from the operator's own
      daily-utility Fly app (and alice's app on the operator's account),
      which run `:latest` under continuous real traffic — Watchtower does
      not auto-update Fly apps (no equivalent poller exists on the Fly
      platform), so any drift between the operator's running Fly machine
      and current `main` is bounded by the operator's own `fly deploy`
      cadence. If neither has had a clean daily triage on the
      release-candidate `:latest` digest, run `fly deploy --config
      ops/fly.toml --app findajob-<operator-handle>` against the
      operator's Fly app and wait one triage cycle before tagging.

## Pre-tag parity matrix verification (minor-bump and major)

This gate applies to every minor bump under `0.x` semver (the breaking-change tier per [§ Version scheme](#version-scheme) above) and to every major bump once `1.0` lands. Patch releases do not re-verify the matrix wholesale; they only re-verify rows the patch actually touched, in the same PR.

When the gate applies, the parity matrix at [`release-parity-matrix.md`](release-parity-matrix.md) must be re-verified before tagging. The matrix asserts every user-visible feature surface behaves identically on Docker (`findajob-staging` reference) and Fly (operator's reference deploy).

Each cell in the matrix must be either `✓ YYYY-MM-DD <sha>` against the release SHA, or `✗ #NNN` with a follow-up issue the operator has explicitly classified as release-acceptable. `(unverified)` cells block the tag.

## Pre-tag smoke check

Before cutting any `v0.1.x` tag, the fresh-install smoke test must pass. The smoke
test spins up a throwaway stack with **empty bind mounts**, runs the documented
install procedure end-to-end, triggers `triage.py`, and asserts:

1. `pipeline.jsonl` contains a `pipeline_complete` event with `scored > 0`
2. `jobs` table has rows in stage `scored` or `manual_review`
3. `cost_log` has rows (proves the #117 schema fold is working on fresh DBs)

Run locally on a docker-equipped host before proposing the tag. From the
maintainer's dev laptop, the workflow is: build the image on `<deployment-host>` (or
any docker-equipped host), then run the smoke script against it.

```bash
# On <deployment-host> (or any host with docker + this repo checked out)
cd /path/to/findajob
docker build -t findajob:local .
FINDAJOB_TEST_IMAGE=findajob:local scripts/test_container_integration.sh
```

The script takes 2–5 minutes (dominated by ~20 LLM scoring calls over the real
network) and costs ≤$0.10 of API budget per run.

**Run as your normal docker-group user — do not `sudo` this script.** The
compose snippet embeds `$(id -u):$(id -g)` as PUID/PGID; running under sudo
collapses both to 0, collides with the container's root GID, and prevents
the unprivileged `lad` user from being created. The script now fails fast
with a clear diagnostic if it detects uid=0 or gid=0, so this is a
one-line correction rather than a 60s startup timeout to debug.

**If the smoke is green on the commit you intend to tag, the gate is cleared
and Claude may propose the cut.** No time window, no 24h/48h observation. A
binary signal tied to what a fresh tester actually exercises.

A separate clean-NUX simulator stack (distinct from the smoke script's
throwaway stack) is reset after any release that touches onboarding, schema,
config layout, or entrypoint. Procedure is operator-private; see
`CLAUDE.local.md` for the per-operator stack name and reset script path.

CI wiring for this smoke is deferred to a follow-up issue: the script depends
on live API keys, and wiring those into GitHub Actions is a meaningful
security and ops decision orthogonal to the smoke itself. Until CI runs the
smoke, Claude runs it locally before proposing each tag cut and reports the
result to the user as part of the cut proposal.


## migration-required label criteria

Claude applies the `migration-required` label at PR-open time when the PR contains
any of the triggers below. The user can challenge the call in review if it looks
wrong. The label drives the "Action required before upgrade" section at the top of
the release notes — mislabeling is low-stakes because the label is editable
post-merge.

Triggers:

- **SQLite schema changes** — new tables, new columns, new indexes on existing
  tables, type changes, or dropped columns. Anything that changes the shape of
  `data/pipeline.db`.
- **New required env keys.** A new key in `state/data/.env` that the pipeline
  depends on. Optional overrides and new feature toggles that default off are
  *not* triggers — they ship without forcing user action.
- **Moves or renames of files in `state/config/`** that break existing stacks.
  If a user's current config layout won't work after pulling the new image,
  label it.
- **Crontab schedule changes.** A genuine shift in when a timer fires or what
  it runs — not a bugfix to a broken cronline. #75 (fixing three broken
  `notify.py` subcommands) was a correction, not a schedule change. Moving
  triage from 00:00 to 03:00, or adding a new scheduled job, *is* a change.
- **Bind-mount layout changes in `ops/compose.yaml.example`.** If the user's
  existing compose file needs editing before pulling, label it.
- **Changes that require `docker compose down` before `pull && up -d`.**
  Network config changes, removing and re-adding a service, or anything else
  where a rolling pull-and-restart won't cut it.

If unsure, don't apply the label. Re-labeling post-merge works — the release
notes generator picks up the label whenever it's set, so a late add still
surfaces in the "Action required before upgrade" section.

## CHANGELOG workflow

When cutting `vX.Y.Z`, the CHANGELOG moves from the `[Unreleased]` scratch pad to
a named version block. The format follows Keep-a-Changelog and matches the current
state of `CHANGELOG.md` — don't diverge from the existing convention.

The steps, in order:

1. Under `## [Unreleased]`, Claude drafts the entries into their destination block,
   grouping by Keep-a-Changelog category (`Added`, `Changed`, `Deprecated`,
   `Removed`, `Fixed`, `Security`). Every entry references its PR with `(#NNN)`.
   If the PR introduced a migration trigger, the entry mentions that in plain
   prose — the label surfacing is separate from the prose and both are valuable.

2. Replace the `## [Unreleased]` header with `## [X.Y.Z] — YYYY-MM-DD`. The date
   is ISO 8601 in Pacific Time. Convert from server UTC by subtracting 7 hours
   in PDT or 8 hours in PST, per the `feedback_pt_user_calendar.md` convention.

3. Insert a fresh empty `## [Unreleased]` block at the top of the file, above
   the newly-named version. Future changes land there.

4. At the bottom of the file, add the version cross-reference link:
   `[X.Y.Z]: https://github.com/brockamer/findajob/releases/tag/vX.Y.Z`. Update
   the existing `[Unreleased]` line to point at the new tag:
   `[Unreleased]: https://github.com/brockamer/findajob/compare/vX.Y.Z...HEAD`.

5. Commit with `docs: move [Unreleased] → [X.Y.Z] in CHANGELOG (#69 process)`.
   Open this as a PR — it should not land directly on main.

6. Wait for the PR to merge, then wait for `:latest` to finish rebuilding off the
   CHANGELOG commit before moving on to the tag cut. This ordering matters: the
   tag is applied to the commit whose CHANGELOG *describes itself*, so the
   image identified by `:vX.Y.Z` literally *is* the code the release notes
   describe.

## Tag cut mechanics

After the CHANGELOG commit is on `main` and `:latest` has rebuilt off of it, Claude
cuts the tag. Set `VERSION` locally (e.g. `VERSION=0.1.0`) before running:

```bash
VERSION=0.1.0
MAJOR_MINOR="${VERSION%.*}"  # e.g., 0.1 — used in Section 9 Rollback
cd /home/brockamer/Code/findajob
git fetch origin
git checkout main
git pull
# Sanity-check that CHANGELOG.md is current:
grep -c "## \[${VERSION}\]" CHANGELOG.md   # Expected: 1
git tag "v${VERSION}"
git push origin "v${VERSION}"
```

That single push triggers both workflows: `build-image.yml` pushes the three tags
(`:v${VERSION}`, `:v${MAJOR_MINOR}`, `:latest`) to GHCR, and `create-release.yml`
generates the GitHub Release page with auto-generated notes, prepending the
"Action required before upgrade" section if any merged PRs in the range carry the
`migration-required` label.

## Post-tag verification

Within roughly ten minutes of the tag push, Claude verifies the release landed
cleanly.

1. Open the GitHub Release page at
   `https://github.com/brockamer/findajob/releases/tag/v${VERSION}`. The title
   should be `v${VERSION}`. If any PRs in the range were labeled
   `migration-required`, the "⚠️ Action required before upgrade" section must
   appear at the top with those PRs listed. If the section is missing and should
   be there, the label was applied after the workflow ran — re-run
   `create-release.yml` manually.

2. Confirm the release workflow succeeded:

   ```bash
   gh run list --workflow=create-release.yml --limit 1
   ```

   Status `completed`, conclusion `success`.

3. Confirm the image build workflow succeeded:

   ```bash
   gh run list --workflow=build-image.yml --limit 1
   ```

   Status `completed`, conclusion `success`.

4. Verify the image is pullable on `<deployment-host>`:

   ```bash
   ssh <deployment-host> "docker pull ghcr.io/brockamer/findajob:v${VERSION}"
   ```

   Expected: clean pull, no 401 (auth) or 404 (tag missing).

If any of steps 1–4 fail, either re-run the failing workflow job from the GitHub
Actions UI or move to the rollback procedure in the next section.

## Cohort deploy

Once the image is on GHCR and post-tag verification passes, every stack
gets the image roll in a single operational pass. Every surface tracks
`:latest`, so the cohort wave is one uniform operation per stack — no
per-tester pin advancement and no `.env` edits. Per the
`feedback_deploy_both_stacks` memory, no stack is left behind. The pass is
substrate-aware: docker stacks roll through §"Docker cohort deploy" below;
Fly apps roll through §"Fly app deploy". Both substrates run in the same
operational pass during the Post-Launch Tester Sunset arc (milestone m26),
and the docker tester rows drop as each tester completes their Fly cutover.

### Docker cohort deploy

Covers the operator's docker stacks (`findajob-clean`, `findajob-staging`,
and the operator's primary docker stack if any remain post-cutover) plus
every beta tester docker stack still in place pre-m26. The tester rows
drop out of this loop as each tester completes their Fly cutover; when m26
closes, only `findajob-clean` and `findajob-staging` remain — both dev
tiers.

For each stack on `<deployment-host>`:

```bash
cd /opt/stacks/<stack>
docker compose pull
docker compose up -d
docker exec <stack>-scheduler-1 python -m findajob.web.verify_auth
```

The verifier line is **not optional**. It exists because every stack that has
basic auth configured must continue to enforce it after a recompose, and a
silent regression of the auth gate is a real incident class (see CLAUDE.md
"Auth Gate Must Be Verified Post-Deploy").

If the verifier exits non-zero on any stack:

```bash
cd /opt/stacks/<stack> && docker compose down
```

Treat the stack as broken until the verifier passes. Common failure modes:
`FINDAJOB_AUTH_USER`/`FINDAJOB_AUTH_PASS` missing from `state/data/.env`
(exit 2); auth middleware silently dropped from the route stack (exit 3);
configured creds don't match the running container's env (exit 4 — usually
a stale `.env` not picked up because the stack was `up -d` instead of full
`down`/`up`).

### Fly app deploy

Covers the operator's own Fly app, alice's Fly app on the operator's
account, and any beta tester's Fly app that has completed its cutover per
milestone m26. Each Fly app deploys against its own `ops/fly.toml`
(operator's checkout) or the tester's checkout of the same template.

For each Fly app (operator runs from a local checkout of this repo, logged
in via `fly auth login` as the appropriate account):

```bash
fly deploy --config ops/fly.toml --app findajob-<handle>
fly ssh console --app findajob-<handle> --command "python -m findajob.web.verify_auth"
```

`fly deploy` returns once the http_service health check passes, so the
machine is already serving the new image by the time the SSH'd
`verify_auth` runs. The verifier is **not optional** here either — same
auth-gate-regression incident class as docker, same exit-code taxonomy.

If `verify_auth` exits non-zero, treat the app as broken until it passes.
Roll back via §"Rollback" below — re-deploying the prior immutable tag is
the canonical recovery, not editing the app in place.

Operator + alice are the initial Fly cohort; each tester joins this loop
when their migration completes (per `findajob.migrate` runbook at
[`tester-migration.md`](tester-migration.md)) and drops out of the docker
loop in the same operational pass.

## Rollback

If post-tag verification fails or a regression is reported after the release
is out in the wild, Claude rolls back by re-pointing `:latest` back to the
prior immutable tag, then propagating the restored image to every stack on
both substrates.

1. Identify the last-known-good immutable tag, e.g. `v${VERSION_PREV}`.

2. Re-point `:latest`. Requires being logged in to GHCR with a PAT that has
   `write:packages` scope:

   **Prerequisite:** You must be logged into GHCR with a personal access token that has `write:packages` scope. If not already logged in, run `echo $GHCR_PAT | docker login ghcr.io -u brockamer --password-stdin` first.

   ```bash
   docker pull ghcr.io/brockamer/findajob:v${VERSION_PREV}
   docker tag ghcr.io/brockamer/findajob:v${VERSION_PREV} ghcr.io/brockamer/findajob:latest
   docker push ghcr.io/brockamer/findajob:latest
   ```

   (Also re-point `:v${MAJOR_MINOR}` for any stack that has been individually
   frozen on a specific minor — but the default cohort runs on `:latest`, so
   the `:latest` repoint is the load-bearing step.)

3. Propagate the restored image to every stack on both substrates. The
   `:latest` repoint in step 2 is necessary but not sufficient — docker
   stacks pull on their next `docker compose pull` (automatic on
   Watchtower-enabled stacks per #768, manual otherwise), and Fly apps do
   not pull on a poll at all.

   - **Docker stacks:** for stacks with Watchtower auto-update enabled
     (per #768; testers + `findajob-staging` scheduler opt in, operator
     primary + `findajob-clean` + `findajob-staging` gmail-auth opt out),
     the host's hourly poll picks up the restored `:latest` automatically.
     For Watchtower-disabled stacks — and as belt-and-suspenders for the
     opt-in stacks if speed matters — re-run §"Docker cohort deploy" for
     each stack to force the pull and restart immediately.
   - **Fly apps:** re-run §"Fly app deploy" for each Fly app to force the
     deploy against the restored `:latest`. Fly does not have an
     image-refresh equivalent to Watchtower; the explicit re-deploy is the
     only way the rollback reaches Fly apps.

4. The immutable `:v${VERSION}` tag for the bad release stays pinned. Anyone who
   specifically pinned to it keeps the broken image — that's intentional, because
   the immutable tag is the audit trail. If a user is on that tag and reports a
   bug, Claude can reproduce exactly what they're running.

5. Document the rollback in `CHANGELOG.md` by adding a `## [Reverted] — YYYY-MM-DD`
   block near the top of the file (above `[Unreleased]`, below any intervening
   entries) naming the bad tag and stating the reason in a sentence or two.

6. Notify external users (alice and any other beta testers) via whatever
   channel is active at the time.

**First-release note (v0.1.0 specifically).** There is no prior tag to roll back
to. If `v0.1.0` ships broken, the only path is "stop recommending the release,
fix forward on `main`, cut `v0.1.1` as soon as the pre-tag smoke check clears."
Do not attempt to revive an older `:latest` by ad-hoc re-tagging — the immutable
`:main-<sha>` tags on every `main` push are the audit trail, and rewriting
`:latest` breaks that.
