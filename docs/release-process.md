# Release Process

This is the runbook Claude follows when cutting a release of the findajob pipeline's
Docker image. Claude orchestrates the release end-to-end — proposing the cut, drafting
the CHANGELOG, running the dogfood gate, writing notes, pushing the tag, verifying the
outcome, and owning any rollback. The user reviews and approves the proposed cut but does
not author any of the release artifacts. This split is codified in the
`feedback_release_management.md` memory.

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
entries from the merged PRs in the range, running the full 48-hour dogfood gate,
flagging migration markers on PRs as they come in (or retroactively if a trigger was
missed), writing the release notes content, executing `git tag` and `git push origin
vX.Y.Z`, running post-tag verification against GitHub Actions and GHCR, and owning
rollback if anything goes wrong. The user's role is review-only: they look at the
proposed cut, confirm the dogfood signals, and approve or request changes. They
do not write CHANGELOG bullets, author the notes, or run the tag commands. If the release breaks something, Claude owns the recovery.

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
A user pinned to `:v0.1` can pull and restart without reading anything.

The image tag taxonomy — which determines what a user pulling from GHCR actually gets
— is set by [`build-image.yml`](../.github/workflows/build-image.yml):

| Tag | Type | Who pushes | Purpose |
|---|---|---|---|
| `:latest` | moving | `build-image.yml` on every `main` push | dogfood track — bleeding edge, what the maintainer's LXC runs |
| `:main-<sha>` | immutable | `build-image.yml` on every `main` push | bisecting, precise pinning for diagnosis |
| `:v0.1.0` | immutable | `build-image.yml` on `v*.*.*` tag push | pinned release, never moves |
| `:v0.1` | moving | `build-image.yml` on `v*.*.*` tag push | auto-advances to the latest `v0.1.x` — the recommended user pin |

The recommended user pin is `:vMAJOR.MINOR` (e.g. `:v0.1`). It gets bugfixes
automatically on `docker compose pull` and only moves to a potentially breaking
version when Claude explicitly cuts a new minor.

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

## Dogfood gate

This is a hard binary gate. All six signals below must be clean across a continuous
48-hour window on `:latest` running on the maintainer's LXC at `findajob.lan`. If any signal
fails at any point in the window, the clock restarts after the fix lands on `main`
and `:latest` rebuilds. No averaging, no "mostly green" — the point is to catch
regressions that only surface after multiple scheduler cycles.

The checks target the supercronic scheduler container running from
`/opt/stacks/findajob-brock/compose.yaml`. The service is named `scheduler`.

**1. At least two full triage runs completed.** Triage runs once daily at 00:00 PT,
which is 07:00 UTC in PDT or 08:00 UTC in PST. Across a 48h window there should be
at least two completions.

```bash
ssh findajob.lan 'docker compose -f /opt/stacks/findajob-brock/compose.yaml logs scheduler --since 48h' \
  | grep pipeline_complete
```

Expected: at least 2 `pipeline_complete` events.

**2. poll_flags cycles firing every 10 min without exception rows.** `poll_flags.py`
runs on a `*/10` supercronic line, so a 2h sample is enough to confirm it's healthy
right now — but the 48h gate above catches intermittent failures.

```bash
ssh findajob.lan 'docker compose -f /opt/stacks/findajob-brock/compose.yaml logs scheduler --since 2h' \
  | grep -E 'poll_flags|Traceback'
```

Expected: many `poll_flags` invocations, zero `Traceback` lines. Supercronic prints
each job's exit code — every `poll_flags` exit should be `0`.

**3. 07:00 UTC daily health-check notify fired on the maintainer's phone.** The
`notify.py health-check` line fires daily at 07:00 UTC. The phone confirmation is
the authoritative check (the notification actually reached ntfy). As a secondary
scheduler-side check:

```bash
ssh findajob.lan 'docker compose -f /opt/stacks/findajob-brock/compose.yaml logs scheduler --since 48h' \
  | grep 'health-check'
```

Expected: at least two `health-check` invocations with exit code 0 in the last 48h,
and the maintainer confirms the phone notification landed.

**4. Sheet syncs landed on two consecutive cycles.** Sheet1, Dashboard, and Applied
tab writes are driven by `sync_sheet.py`. Two consecutive healthy cycles is enough
to establish the Google Sheets path is working.

```bash
ssh findajob.lan 'docker compose -f /opt/stacks/findajob-brock/compose.yaml logs scheduler --since 2h' \
  | grep sync_sheet
```

Expected: at least two `sync_sheet` invocations, zero non-zero exit codes in that
window.

**5. form-ingest runs clean.** `ingest_form.py` runs every 30 min; in a 24h window
there should be roughly 48 invocations.

```bash
ssh findajob.lan 'docker compose -f /opt/stacks/findajob-brock/compose.yaml logs scheduler --since 24h' \
  | grep -E 'ingest_form|Traceback'
```

Expected: many `ingest_form` invocations (every 30 min), zero tracebacks.

**6. No stack traces in scheduler logs across the 48h window.** This is the
belt-and-suspenders check — if any of the per-job checks above missed something,
a `Traceback` count will catch it.

```bash
ssh findajob.lan 'docker compose -f /opt/stacks/findajob-brock/compose.yaml logs scheduler --since 48h' \
  | grep -c Traceback
```

Expected: `0`.

If and only if all six signals pass across the continuous 48h window, the gate is
cleared and Claude may propose the cut to the user.

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

4. Verify the image is pullable from the LXC:

   ```bash
   ssh findajob.lan "docker pull ghcr.io/brockamer/findajob:v${VERSION}"
   ```

   Expected: clean pull, no 401 (auth) or 404 (tag missing).

If any of steps 1–4 fail, either re-run the failing workflow job from the GitHub
Actions UI or move to the rollback procedure in the next section.

## Rollback

If post-tag verification fails or a regression is reported after the release is
out in the wild, Claude rolls back by re-pointing the moving `:vMAJOR.MINOR`
alias back to the prior immutable tag. Users pinned to `:v0.1` will then pull
the prior image on their next `docker compose pull`.

1. Identify the last-known-good immutable tag, e.g. `v${VERSION_PREV}`.

2. Re-point the moving alias. This requires being logged in to GHCR with a PAT
   that has package-write scope:

   **Prerequisite:** You must be logged into GHCR with a personal access token that has `write:packages` scope. If not already logged in, run `echo $GHCR_PAT | docker login ghcr.io -u brockamer --password-stdin` first.

   ```bash
   docker pull ghcr.io/brockamer/findajob:v${VERSION_PREV}
   docker tag ghcr.io/brockamer/findajob:v${VERSION_PREV} ghcr.io/brockamer/findajob:v${MAJOR_MINOR}
   docker push ghcr.io/brockamer/findajob:v${MAJOR_MINOR}
   ```

3. The immutable `:v${VERSION}` tag for the bad release stays pinned. Anyone who
   specifically pinned to it keeps the broken image — that's intentional, because
   the immutable tag is the audit trail. If a user is on that tag and reports a
   bug, Claude can reproduce exactly what they're running.

4. Document the rollback in `CHANGELOG.md` by adding a `## [Reverted] — YYYY-MM-DD`
   block near the top of the file (above `[Unreleased]`, below any intervening
   entries) naming the bad tag and stating the reason in a sentence or two.

5. Notify external users (Amy and any future beta testers) via whatever channel
   is active at the time.

**First-release note (v0.1.0 specifically).** There is no prior tag to roll back
to. If `v0.1.0` ships broken, the only path is "stop recommending the release,
fix forward on `main`, cut `v0.1.1` as soon as the dogfood gate clears again."
Do not attempt to revive an older `:latest` by ad-hoc re-tagging — the immutable
`:main-<sha>` tags on every `main` push are the audit trail, and rewriting
`:latest` breaks that.
