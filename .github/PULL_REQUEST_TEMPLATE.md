<!--
Thanks for sending a PR. A few notes before you submit:

- See AGENTS.md for the full contributor guide (dev setup, commit conventions,
  architectural invariants).
- Run `uv run ruff check src/ tests/`, `uv run ruff format --check src/ tests/`,
  and `uv run pytest` before opening the PR.
-->

## Summary

<!-- 1-3 sentences: what does this PR do, and why. -->

## Type of change

<!-- Mark all that apply. -->

- [ ] `feat` — new functionality
- [ ] `fix` — bug fix
- [ ] `docs` — documentation only
- [ ] `test` — test changes only
- [ ] `refactor` — code restructure with no behavior change
- [ ] `chore` — tooling, dependencies, build config

## Migration impact

<!--
If this PR adds/removes a schema column, changes config layout, modifies the
compose template, edits crontab/scheduled-jobs.yaml, or changes bind mounts,
mark below — release notes will surface the PR for external operators.

Typical migration triggers: new/removed database column, changed environment
variable contract, altered docker-compose service/volume, updated cron schedule,
or modified bind mount path.
-->

- [ ] **Migration required** — schema, config, compose, crontab, or mount change
- [ ] No migration impact

If migration required, the CHANGELOG entry **must** include a `### Migration required` line describing the operator action.

## Tests

<!-- Required for: new POST handler, new actions helper, schema change, new
adapter, change to complete()/cost_rollups, dedup/cleaning helpers, or any
known-repeat-bug boundary. Otherwise encouraged but not gated. -->

- [ ] Tests added or updated for the changed behavior
- [ ] `uv run pytest` passes locally
- [ ] `uv run ruff check src/ tests/` and `uv run ruff format --check src/ tests/` are clean

## Acceptance criteria

<!-- If this PR closes an issue, copy its acceptance criteria here and check them off. -->

- [ ] AC #1
- [ ] AC #2

## Linked issues

Closes #

## Notes for reviewer

<!-- Anything specific to call out, alternatives considered, or follow-ups. -->