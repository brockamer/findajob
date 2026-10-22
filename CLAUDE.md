# Project guide

Opinionated defaults so a coding assistant can work without clarifying questions.

## Purpose
- Primary task context: docs(ops): correct the Docker install and operations reference

## Stack & layout
- Read the top-level README first, then module entrypoints.
- Prefer small, reviewable diffs over drive-by refactors.

## Common commands
- Install / build / test using the repo's documented scripts.
- If `python3 build.py` exists: always run it after changes and attach diagnostics.

## Conventions
- Match existing naming and file layout.
- No secrets in commits; no unrelated dependency bumps.

## Where to start
- Root README → package manifest → the module you are changing.

## Pitfalls
- Do not invent parallel conventions; update this file in the same PR if needed.
