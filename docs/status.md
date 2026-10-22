# Status

One page on where findajob stands, who it is for, what is next and the rules every change follows. The numbered decisions behind each statement are in [`roadmap.md`](roadmap.md); the live work is on the [project board](https://github.com/users/brockamer/projects/1). If this page and an issue disagree, this page wins; if this page and `roadmap.md` disagree, fix this page.

## Who it is for

findajob is maintained for operators comfortable with Docker, a terminal, git and SQLite, who run it for themselves or on behalf of other people (Decision 37). Documentation assumes those skills. It is not optimized for a non-technical job seeker installing it alone, and contributions that add hand-holding for that reader are out of scope.

## Posture

Maintained, not wound down, with no feature arc and no cadence (Decisions 35 and 37). Work is triggered by a defect meeting the bar below, an operator-reported problem, or a documented surface being wrong. Pre-1.0: `0.x` releases are unstable and a minor bump may break an upgrade; read the release notes.

**Supported.** The Docker install path and one image, `ghcr.io/brockamer/findajob`, tagged from `main`. Security and data-integrity defects on paths that protect non-rebuildable state. Defects a new operator hits on first install. Dependency and Actions bumps. Documentation that is false.

**Abandoned.** Any hosted or zero-terminal on-ramp. Routine polish with no operator-visible failure behind it. Manufacturing a milestone on each close. Speculative backlog growth.

**Out of scope.** Per-user identity or roles inside one instance: running findajob for several people means one instance per person, each with its own data, credentials and keys (Decision 37(c)). Alternative LLM providers: every model call goes through OpenRouter.

## Next

In order. Each step says what it unblocks.

### Remove the retired on-ramp and correct the install reference
Delete the hosted-install documentation and scripts (#1103) and correct the Docker install and operations reference (#1105). Unblocks: a newcomer who follows the docs lands on the one supported path, and the documentation prune below can judge the rest.

### Prune and correct the documentation
Delete every doc that no longer applies; rewrite the ones whose subject exists but whose claims are wrong (2026-10 audit; `architecture/file-map.md` is the inventory). Unblocks: people and agents reading only true documentation.

### Operator-reported fixes
Fixes and requests reported by people running instances for others, labelled `feedback` + `external-report` (#1224, #1221, #1241). Unblocks: operators keeping their users on a current release.

### Reliability defects from the 2026-09 review
#1140 to #1146: adapter isolation in the nightly run, guarded prep writes, correct failure resets, transport retries, provider-only cost figures. Unblocks: a nightly triage that survives one source failing, and cost figures that are only what the provider reported.

### Cleanup from the 2026-09 review
#1147 to #1182: dead code, drifted docs, test fixtures, module boundaries, candidate-neutral prompts. Unblocks: a smaller codebase that no longer carries one person's search.

### Single-use link for a mailbox owner's app password
#1077, when the operator schedules it: the first endpoint that writes a credential before authentication, so it ships with a security review. Unblocks: a mailbox owner never hands an operator their app password.

## Rulings

Standing rules that bind every change. A change that cannot meet one is reshaped, not waved through.

- **Operator-reported items go before non-security work.** `feedback` + `external-report`; bugs are High, polish Medium. Their fix PRs write `Refs #N` and the issue closes only after the check on the reporting instance.
- **A merge to `main` reaches no instance.** It builds and pushes an image. Instances pin a release tag and are upgraded by their operator: back up, bump the tag, `compose pull && up -d`, then `python -m findajob.web.verify_auth` must exit 0, with a stated rollback. A release is a tag cut from `main` after the CHANGELOG `[Unreleased]` section is moved under the version; no merges land between that cut and the tag.
- **Schema, config, crontab, mount and compose changes carry `migration-required`** and a CHANGELOG `### Migration required` entry. Migrations are append-only numbered files under `src/findajob/migrations/`; never edit one that shipped.
- **Pre-PR checklist** ([CONTRIBUTING.md](../CONTRIBUTING.md)): branch off `origin/main`; `uv run ruff check` and `ruff format --check` on `src/ scripts/ tests/`; `uv run mypy src/findajob/`; `uv run pytest tests/`; a CHANGELOG line under `[Unreleased]`; `Closes #N` once per issue in the PR body.
- **Docs change in the same PR as the code.** A change updates every doc it makes wrong; a doc that no longer applies is deleted, not kept; `architecture/file-map.md` is updated when files are added, renamed or retired. The reference doc for each area is listed in [`AGENTS.md`](../AGENTS.md).
- **No personal, instance, client or host detail in tracked files.** Real names, employers, hostnames, deployment paths, infra product names and single-career-field vocabulary stay in gitignored config. CI's PII scan on the pull-request diff is the check of record; a fork PR is scanned by the maintainer locally before merge.
- **Every unsafe web route is classified.** `require_htmx` or an entry in `PLAIN_FORM_ROUTES`; the inventory test fails until it is. The cross-site middleware stays inside the auth gate.
- **Hard rejects are code, not prompts.** Boolean classification never relies on an LLM instruction alone. Every LLM call goes through `findajob.llm.openrouter.complete()`, and cost is only the provider's reported figure.
- **The web is the write surface.** Every stage transition is a POST handler calling `findajob.actions`; SQLite is the single source of truth; no transition logic in the watchdog or any out-of-band path.
- **Deletions are complete.** Retiring a feature deletes its code, tests, docs and config example together.
