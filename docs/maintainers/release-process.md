# Release Process

How tagged releases of the findajob Docker image reach GHCR.

Two GitHub Actions workflows do the mechanical work once a tag is pushed:

- [`.github/workflows/build-image.yml`](../../.github/workflows/build-image.yml) builds a
  multi-arch image (linux/amd64 + linux/arm64) and pushes it to GHCR.
- [`.github/workflows/create-release.yml`](../../.github/workflows/create-release.yml)
  generates a GitHub Release page with auto-generated notes, prepending an
  "Action required before upgrade" section that surfaces any PRs labeled
  `migration-required` in the range.

Everything in this doc assumes those two workflows remain in place. If they
change, update this doc in the same commit.

---

## Version scheme

Semver. Until `1.0`, all `0.x` releases are unstable:

- **Minor bump** (`0.27.0` → `0.28.0`): breaking change — schema migration,
  removed config key, compose-file change, anything that forces the user to act
  before pulling the new image.
- **Patch bump** (`0.27.0` → `0.27.1`): bugfixes and non-breaking additions.
  Users on `:latest` can pull and restart without reading anything.

### Image tag taxonomy

Set by `build-image.yml`:

| Tag | Type | Pushed on | Purpose |
|-----|------|-----------|---------|
| `:latest` | moving | every `main` push | recommended pin for all deployments |
| `:main-<sha>` | immutable | every `main` push | bisecting, precise pinning |
| `:vX.Y.Z` | immutable | `v*.*.*` tag push | audit trail for a specific release |
| `:vX.Y` | moving | `v*.*.*` tag push | freeze on a specific minor series |

---

## Pre-tag checklist

Before tagging, confirm:

1. **CHANGELOG is ready.** The `[Unreleased]` block in `CHANGELOG.md` has an
   entry for every PR merged since the last tag. Enumerate with:

   ```bash
   gh pr list --state merged \
     --search "merged:>$(git log -1 --format=%cI <last-tag>)" \
     --json number,title
   ```

   Cross-reference against `[Unreleased]`. Any PR without a CHANGELOG entry
   should either get one or be deliberately omitted as internal-only.

2. **`migration-required` labels applied.** Walk each merged PR in the range
   and check for triggers (see [§ migration-required criteria](#migration-required-criteria)
   below). Late labels are fine — the release-notes generator queries labels at
   release time.

3. **CHANGELOG cross-reference links correct.** The `[Unreleased]` comparator
   at the bottom of the file should point at `compare/<last-tag>...HEAD`.

4. **Smoke test green (recommended).** The fresh-install smoke at
   `scripts/test_container_integration.sh` proves the image boots on empty mounts
   and completes a triage cycle. Run on any docker-equipped host:

   ```bash
   docker build -t findajob:local .
   FINDAJOB_TEST_IMAGE=findajob:local scripts/test_container_integration.sh
   ```

   Takes 2–5 minutes, costs ≤$0.10 in API budget. Requires live API keys in
   `data/.env` (or `$HOME/.findajob/state/data/.env`). Do not run as root.

5. **Transparency invariants green** (if the release touches Gmail/IMAP code):

   ```bash
   uv run pytest tests/test_transparency_invariants.py -v
   ```

---

## CHANGELOG workflow

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

When cutting `vX.Y.Z`:

1. Rename `## [Unreleased]` to `## [X.Y.Z] — YYYY-MM-DD`. Group entries by
   category: `Added`, `Changed`, `Deprecated`, `Removed`, `Fixed`, `Security`.
   Every entry references its PR with `(#NNN)`.

2. Insert a fresh empty `## [Unreleased]` block above the newly-named version.

3. At the bottom of the file, add the version link:
   `[X.Y.Z]: https://github.com/<owner>/findajob/releases/tag/vX.Y.Z`.
   Update the `[Unreleased]` line to compare against the new tag.

4. Commit: `docs: move [Unreleased] → [X.Y.Z] in CHANGELOG`.

5. Wait for the commit to land on `main` and `:latest` to rebuild before
   tagging. The tag should point at the commit whose CHANGELOG describes
   itself.

---

## Tag cut

After the CHANGELOG commit is on `main` and `:latest` has rebuilt:

```bash
VERSION=X.Y.Z
git fetch origin
git checkout main && git pull
grep -c "## \[${VERSION}\]" CHANGELOG.md   # expect: 1
git tag "v${VERSION}"
git push origin "v${VERSION}"
```

That push triggers both workflows: `build-image.yml` pushes the image tags,
`create-release.yml` generates the GitHub Release.

---

## Post-tag verification

Within ~10 minutes of the tag push:

1. Confirm the release workflow succeeded:
   ```bash
   gh run list --workflow=create-release.yml --limit 1
   ```

2. Confirm the image build workflow succeeded:
   ```bash
   gh run list --workflow=build-image.yml --limit 1
   ```

3. Confirm the image is pullable:
   ```bash
   docker pull ghcr.io/<owner>/findajob:v${VERSION}
   ```

4. If any PRs in the range were labeled `migration-required`, confirm the
   "Action required before upgrade" section appears in the GitHub Release page.

If any step fails, re-run the failing workflow from the GitHub Actions UI or
move to [§ Rollback](#rollback).

---

## Deploying to your instance

The release is complete once the tagged image is on GHCR. How you deploy it
depends on your infrastructure:

- **Fly.io:** `fly deploy` against your `fly.toml`. See
  [`docs/getting-started/install-fly.md`](../getting-started/install-fly.md).
- **Docker Compose:** `docker compose pull && docker compose up -d`. See
  [`docs/operations/install-docker.md`](../operations/install-docker.md).

After deploying, verify the auth gate:
```bash
python -m findajob.web.verify_auth
```

If `verify_auth` exits non-zero, the deployment is broken until it passes.
See CLAUDE.md § "Auth Gate Must Be Verified Post-Deploy" for exit codes.

---

## Rollback

If a regression is found after a release ships:

1. Identify the last-known-good immutable tag (e.g. `v0.27.10`).

2. Re-point `:latest` to the good tag. Requires GHCR write access:
   ```bash
   docker pull ghcr.io/<owner>/findajob:v<GOOD>
   docker tag ghcr.io/<owner>/findajob:v<GOOD> ghcr.io/<owner>/findajob:latest
   docker push ghcr.io/<owner>/findajob:latest
   ```

3. Redeploy your instance(s) against the restored `:latest`.

4. The broken `:vX.Y.Z` immutable tag stays — it's the audit trail.

5. Fix forward on `main`, cut a patch release once the pre-tag smoke clears.

---

## `migration-required` criteria

Apply the `migration-required` label at PR-open time when the PR contains any of:

- **SQLite schema changes** — new tables, columns, indexes, type changes, dropped columns.
- **New required env keys** — a key in `state/data/.env` the pipeline depends on.
  Optional overrides defaulting off are not triggers.
- **Config file moves or renames** — breaking existing stacks' `state/config/` layout.
- **Crontab schedule changes** — genuine shift in timer behavior, not a bugfix to a
  broken cronline.
- **Bind-mount layout changes** in `ops/compose.yaml.example`.
- **Changes requiring `docker compose down` before `pull && up -d`** — network
  config changes, service removal/re-add.

The label drives the "Action required" section at the top of the GitHub Release
notes. Mislabeling is low-stakes — the label is editable post-merge.

---

## Install templates

`ops/fly.toml.example` and `ops/compose.yaml.example` both default to `:latest`.
Fresh users following the install docs land on the same image every existing
deployment runs. No per-release template maintenance is required.
