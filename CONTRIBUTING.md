# Contributing to findajob

Welcome. This file is for people who want to contribute code, docs, or bug reports to findajob — the install-it-yourself job-search pipeline.

If you're trying to *use* findajob, you want [`docs/getting-started/`](docs/getting-started/), not this file.

---

## Reporting bugs and proposing features

File an issue on the [GitHub Issues page](https://github.com/brockamer/findajob/issues). Include:

- **What you tried** — exact command, exact URL, exact tab in the web UI.
- **What you expected.**
- **What happened** — paste the relevant `pipeline.jsonl` lines if it's a runtime error, or the browser network tab if it's a UI bug.
- **Stack details** — image tag (`docker compose ps` shows it), one-line `compose.yaml` env summary if you've customized it.

Don't paste API keys, real names, or anything from `data/.env` or `candidate_context/`. The repo is public; issues are public.

---

## Setting up a local development environment

```bash
# Clone
git clone https://github.com/brockamer/findajob.git
cd findajob

# Install dev dependencies (uv is the canonical package manager)
uv sync

# Run the test suite
uv run pytest

# Lint + format check
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/

# Type check (advisory; not a CI gate)
uv run mypy src/
```

The repo lays out as:

- `src/findajob/` — library code (installed editable into the venv via `uv sync`)
- `scripts/` — entry-point shims (≤ ~50 LOC each); business logic goes in `src/findajob/`
- `tests/` — pytest suite; uses real SQLite (tmpfile or `:memory:`), never mocks `sqlite3.connect`
- `config/roles/` — LLM role prompts (Markdown with YAML frontmatter)
- `ops/` — Docker compose template + scheduled-jobs.yaml
- `docs/getting-started/` — user-facing install + configure
- `docs/operations/` — operator-facing runbook
- `docs/maintainers/` — contributor-facing references (project board conventions, plan conventions, release process, generalization tracking)

---

## Pre-commit hook (PII protection)

The repo's pre-commit hook scans staged diffs against a PII pattern list before each commit. **The hook is not tracked in the repo** — each clone installs its own — because the patterns include personal identifiers that must not ride into the public repo.

To install it on a fresh clone:

```bash
cp docs/getting-started/pre-commit-hook.example.sh .git/hooks/pre-commit
chmod +x .git/hooks/pre-commit
```

If you're contributing to a fork, edit `.git/hooks/pre-commit` and either remove patterns that don't apply to you or add patterns for your own personal identifiers. Full details: [`docs/getting-started/configure.md`](docs/getting-started/configure.md).

The CI workflow at `.github/workflows/pii-scan.yml` runs an equivalent scan against PRs using a GitHub Secret `PII_PATTERNS_REGEX`. Commits that pass your local hook will pass CI.

---

## Commit conventions

This repo uses [Conventional Commits](https://www.conventionalcommits.org/) prefixes:

- `feat(area):` — new functionality
- `fix(area):` — bug fix
- `docs(area):` — documentation only
- `test(area):` — test changes only
- `refactor(area):` — code restructure with no behavior change
- `chore(area):` — tooling, dependencies, build config

The commit message body should describe the **why**, not just the **what**. Reference issue numbers (`#123`) where relevant; CHANGELOG entries are drafted in the same PR.

---

## Pull requests

### When to open a PR vs. commit to main

| Change type | Flow |
|-------------|------|
| Docs, comment edits, board conventions | Commit to `main` |
| Code touching pipeline behavior (scoring, fetchers, DB schema, LLM roles) | Feature branch → PR |
| Anything qualifying for `migration-required` (schema, config, compose, crontab, mounts) | PR — release-notes workflow depends on it |

When in doubt: does this change affect what users see when they pull `:latest`? If yes, PR. If no, commit to main.

### `migration-required` label

PRs containing schema changes, config additions/removals, compose-file changes, crontab edits, or bind-mount changes get the `migration-required` label at PR-open time. The release-notes workflow surfaces these PRs to external users so they know an upgrade isn't pure `docker compose pull`. Full criteria: [`docs/maintainers/release-process.md` § migration-required](docs/maintainers/release-process.md#migration-required-criteria).

If your PR introduces any of the above, add the label yourself or call it out in the PR description; a maintainer will tag it.

### Pre-PR checklist

- [ ] `uv run pytest` passes locally.
- [ ] `uv run ruff check src/ tests/` and `uv run ruff format --check src/ tests/` are clean.
- [ ] If your change touches a documented surface (README, `docs/getting-started/*`, `CLAUDE.md`, `docs/operations/`, `docs/maintainers/`), update the docs in the **same** PR.
- [ ] If your change adds a state transition, schema column, or new env var: add a `### Migration required` line to your CHANGELOG entry.
- [ ] If your change touches a known-repeat-bug boundary (cross-stack SQLite immutable URI; audit_log timestamp formats; jobs.id JOIN dependencies; blank-string `company_match` guards), add a regression test.

### Branching off

Local `main` drifts from `origin/main` because squash-merges leave the local branch behind. Always branch off `origin/main`:

```bash
git fetch
git checkout -b feat/<n>-<description> origin/main
```

---

## Architectural invariants

These are non-negotiable. CLAUDE.md describes them in detail; the short list:

- **SQLite is the single source of truth.** Every state transition writes through `findajob.actions`; every read goes through SQL queries against `data/pipeline.db`.
- **Web is the write surface.** Every status / reject-reason transition is a POST handler in `findajob.web.routes.board_actions` calling into `findajob.actions`.
- **All LLM calls go through `findajob.llm.openrouter.complete()`.** No new HTTP transports.
- **Job sources implement the `JobSourceAdapter` Protocol.** Adding a new feed = one new adapter file + one entry in `REGISTERED_ADAPTERS`.
- **Hard rejects are code, not prompt instructions.** Use `scorer_prefilter.py` regex stages for boolean classification; don't trust the LLM alone.
- **Use paths from `findajob.paths`.** Never hardcode `/home/...` or `/app/`. Tests must not depend on absolute paths — use tmpdirs or `BASE`-relative paths.

---

## Code style and patterns

**Patterns new code must follow:**
- `findajob.llm.openrouter.complete` for every LLM call.
- `findajob.actions` for every state transition.
- Route-matrix tests for new POST handlers.
- `findajob.audit.log_event` / `write_audit` for events. No `logging.getLogger`.
- No mocking of `sqlite3.connect` in tests. Use real SQLite (tmpfile or `:memory:`).
- No prompt-string snapshots. Assert structural properties.

**Patterns to retreat from on every pass-through:**
- Bare `sqlite3.connect` — use `findajob.db.connect`.
- Business logic in `scripts/*.py` — extract to `src/findajob/<domain>/`.
- `.in_progress` sentinel files — use the `background_tasks` table.
- Inline schema changes — use the versioned migration runner in `src/findajob/migrations/`.

**File size soft caps** (hard signals at ~1.5×):
- `src/findajob/` modules: ~300 LOC.
- `scripts/` shims: ≤50 LOC (entry-points only).
- Route modules: ~400 LOC.
- Tests: ~500 LOC.

**Tests required when:**
- New POST handler in `routes/`.
- New `findajob.actions` helper.
- Schema change.
- New adapter registered in `REGISTERED_ADAPTERS`.
- Change to `complete()` or `cost_rollups`.
- Change to dedup/cleaning helpers.
- Change crossing a known-repeat-bug boundary.

**Split a refactor across PRs when:**
- It crosses a `migration-required` boundary.
- It exceeds ~500 LOC of behavior change.
- It mixes cleanup with behavior change.
- It risks a partial-state outage.

Otherwise keep it one PR.

---

## Adding a dependency

Before adding a new Python dependency:

- [ ] Is it actively maintained? (Last release < 18 months.)
- [ ] Is it pure-Python or does it ship native binaries that affect the Docker image size?
- [ ] Is the API surface small enough that a vendored 50-line implementation would be cheaper to maintain than the dependency? (Often yes for one-off utilities.)
- [ ] Is its license compatible? (MIT / BSD / Apache 2.0 / PSF — yes. GPL-flavored — needs explicit discussion.)

Add via `uv add <pkg>`; commit `pyproject.toml` and `uv.lock` together.

---

## Where else to look

- **CLAUDE.md** — durable guidance for AI assistants working in this repo. Architectural rules + implementation guardrails + commit flow. Read it; the rules in CLAUDE.md and CONTRIBUTING.md are the same rules.
- **`docs/project-board.md`** — GitHub Projects v2 board conventions (columns, Priority field, labels, `gh project` CLI). Also jared's config file — the machine-readable header block is parsed on every board operation.
- **CLAUDE.md `## Plan Structure`** — what every implementation plan must contain.
- **`docs/maintainers/release-process.md`** — how releases are cut.
- **`docs/maintainers/generalization.md`** — domain-locked content tracker (this codebase started in tech-careers; the goal is field-agnostic).
