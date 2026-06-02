# Contributing to findajob

If you're trying to *use* findajob, start with [`docs/getting-started/`](docs/getting-started/), not this file.

**New here?** Read [`docs/architecture.md`](docs/architecture.md) first — it walks the system design, the prep pipeline's stage-by-stage data flow, the data model, and the rationale behind the key design choices. It's the fastest way to understand how the whole thing fits together before you touch code.

Architectural invariants, code-style patterns, and implementation guardrails live in [`CLAUDE.md`](CLAUDE.md).
Board conventions and project roadmap live at [`docs/project-board.md`](docs/project-board.md) and the [GitHub project board](https://github.com/users/brockamer/projects/1).

---

## Reporting bugs and proposing features

File an issue on [GitHub Issues](https://github.com/brockamer/findajob/issues). Include what you tried, what you expected, what happened (paste relevant `pipeline.jsonl` lines for runtime errors), and your image tag. Don't paste API keys, real names, or anything from `data/.env` or `candidate_context/` — the repo and issues are public.

---

## Dev setup

```bash
git clone https://github.com/brockamer/findajob.git
cd findajob
uv sync                                          # install findajob + dev deps (editable)
uv run pytest                                    # test suite
uv run ruff check src/ tests/                    # lint
uv run ruff format --check src/ tests/           # format check
uv run mypy src/                                 # type check (advisory)
```

**Pre-commit hook (PII protection).** Install on each clone — the hook is not tracked because patterns include personal identifiers:

```bash
cp docs/getting-started/pre-commit-hook.example.sh .git/hooks/pre-commit
chmod +x .git/hooks/pre-commit
```

CI runs an equivalent scan (`.github/workflows/pii-scan.yml`). Commits that pass the local hook will pass CI.

---

## Commit conventions

[Conventional Commits](https://www.conventionalcommits.org/) prefixes: `feat`, `fix`, `docs`, `test`, `refactor`, `chore`. Scope goes in parentheses: `feat(scorer):`. The body should describe the **why**. Reference issue numbers (`#123`); CHANGELOG entries are drafted in the same PR.

---

## Pull requests

**When to PR vs. commit to main:**

| Change type | Flow |
|---|---|
| Docs, comment edits, board conventions | Commit to `main` |
| Code touching pipeline behavior | Feature branch → PR |
| `migration-required` changes (schema, config, compose, crontab, mounts) | PR — required for release notes |

**Branch off `origin/main`** (local `main` drifts via squash-merge):

```bash
git fetch && git checkout -b feat/<n>-<description> origin/main
```

**Pre-PR checklist:**
- [ ] `uv run pytest` passes.
- [ ] `ruff check` and `ruff format --check` are clean.
- [ ] Docs updated in the same PR if a documented surface changed.
- [ ] `### Migration required` entry in CHANGELOG if applicable.
- [ ] `migration-required` label added to the PR if applicable. See [`docs/maintainers/release-process.md`](docs/maintainers/release-process.md) for criteria.
