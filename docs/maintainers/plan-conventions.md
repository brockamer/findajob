# Plan Conventions

Implementation plans live in an operator-private location (`docs/superpowers/plans/` — gitignored; files on disk but not tracked, per #430). They are the bridge between a brainstormed spec and the actual commits. This doc describes what every plan must contain so the work is reviewable, doc updates aren't forgotten, and post-merge verification has a clear acceptance gate. The storage location is operator-private; the content discipline below is unchanged.

## Required sections

Every plan must include the sections below. Skipping one is a smell — push back rather than write a plan that hides scope.

### 1. Goal + scope

One paragraph: what's being built and why. One paragraph: what's intentionally NOT in scope (and links to the issues that cover the deferred work).

### 2. Tasks

Numbered, bite-sized tasks. Each task spells out:

- **Files** to create / modify
- **Steps** as a checklist
- **Verification** commands and their expected outputs
- **Commit message** body

Prescriptive enough that a fresh subagent can execute the task without re-reading the spec.

### 3. Documentation Impact

**This is a mandatory section, even if the answer is "none."**

For each documentation surface that the work touches, name the file and the change:

- `README.md` — does the install path, tech stack, or quick-start need updating?
- `docs/getting-started/*.md` — install + configure guides
- `CLAUDE.md` and `CLAUDE.local.md` — operating context for future sessions
- `CHANGELOG.md` — user-facing release note entry
- Spec doc in `docs/superpowers/specs/` — does this plan amend the original spec? Capture material decisions made during implementation.
- In-code docstrings — modules, public functions, role prompt files

If an item belongs to a follow-up issue rather than this plan, name the issue. If no docs are touched, write "None — no user-visible or developer-facing surface changes." Don't leave the section empty.

### 4. Verification gate

The smoke checks, integration tests, or manual validations that must pass before the PR opens. Distinct from per-task verification — this is the whole-feature acceptance gate.

### 5. Self-review checklist

Spec coverage map (every spec section → tasks that implement it), placeholder scan (no `TBD`/`TODO` left), type/contract consistency across files.

## Why "Documentation Impact" is required

Doc drift is silent. Code reviews catch behavioral bugs but rarely catch a stale README, a CLAUDE.md table that lists the wrong scheduler, or a setup guide that points at a removed file. The Documentation Impact section forces the plan author to enumerate every surface before implementing — turning "doc updates" from an afterthought into part of the plan's task list.

Plans that ship code without their corresponding doc updates create rework: the user (or a future session) finds the divergence months later and has to reconstruct what was supposed to change.

## Plan storage and naming

`docs/superpowers/plans/YYYY-MM-DD-short-feature-slug.md` — one plan per feature, dated for ordering. If a plan needs a mid-implementation handoff, use the `-CHECKPOINT.md` suffix and delete it once the next session resumes.

## Relationship to specs

Specs (`docs/superpowers/specs/`) describe **what** and **why** — they are the design + decision-log artifact from brainstorming. Plans describe **how** — concrete tasks with verifications. A spec without a plan can't be executed; a plan without a spec usually means the design wasn't really thought through.

When a plan reveals a flaw in the spec, fix the spec in the same PR (often via a "Decisions made during implementation" subsection appended to the spec doc). Don't let the plan and spec drift.
