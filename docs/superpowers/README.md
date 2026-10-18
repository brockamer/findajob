# Plans and specs

Design notes for public findajob features. Everything in this folder is committed and public.

| Folder | Holds |
|---|---|
| `specs/` | What a feature does and why — the design and its decision log, written while the design is settled. |
| `plans/` | How it is built — numbered tasks with files, verification commands and commit messages. |
| `experiments/` | The setup and results of a measurement or trial. |

Name each file `YYYY-MM-DD-short-feature-slug.md`, dated for ordering. Put the issue number in the slug when there is one. A mid-implementation handoff uses the `-CHECKPOINT.md` suffix and is deleted once the next session resumes.

## Plan structure

Implementation plans in `plans/` are the bridge between a brainstormed spec and the actual commits.

### Required sections

Every plan must include the sections below. Skipping one is a smell — push back rather than write a plan that hides scope.

1. **Goal + scope** — One paragraph: what's being built and why. One paragraph: what's intentionally NOT in scope (and links to the issues that cover the deferred work).
2. **Tasks** — Numbered, bite-sized tasks. Each spells out **Files** to create/modify, **Steps** as a checklist, **Verification** commands and their expected outputs, and a **Commit message** body. Prescriptive enough that a fresh subagent can execute the task without re-reading the spec.
3. **Documentation Impact** — **Mandatory, even if the answer is "none."** For each documentation surface the work touches, name the file and the change: `README.md` (install path, tech stack, quick-start), `docs/getting-started/*.md`, `AGENTS.md` / `CLAUDE.local.md`, the reference docs under `docs/architecture/`, `CHANGELOG.md`, the feature's spec (does this plan amend it?), in-code docstrings. If an item belongs to a follow-up issue, name the issue. If no docs are touched, write "None — no user-visible or developer-facing surface changes." Don't leave it empty.
4. **Verification gate** — The smoke checks, integration tests, or manual validations that must pass before the PR opens. Distinct from per-task verification — this is the whole-feature acceptance gate.
5. **Self-review checklist** — Spec coverage map (every spec section → tasks that implement it), placeholder scan (no `TBD`/`TODO` left), type/contract consistency across files.

**Why Documentation Impact is required:** doc drift is silent. Code reviews catch behavioral bugs but rarely a stale README or an agent-instructions table listing the wrong scheduler. Enumerating every surface before implementing turns "doc updates" from an afterthought into part of the task list.

### Relationship to specs

Specs (the `specs/` folder) describe **what** and **why** — the design + decision-log artifact from brainstorming. Plans describe **how** — concrete tasks with verifications. A spec without a plan can't be executed; a plan without a spec usually means the design wasn't really thought through. When a plan reveals a flaw in the spec, fix the spec in the same PR (often via a "Decisions made during implementation" subsection appended to the spec). Don't let plan and spec drift.

## What does not go here

The rules in [`AGENTS.md` § PII and Domain-Neutrality](../../AGENTS.md#pii-and-domain-neutrality) apply to every file in this folder:

- no real names, emails, keys, host names, deployment paths or instance names;
- no content that locks the pipeline to one career field;
- nothing about running a specific instance of findajob. That work is tracked in the maintainer's private tracker.

## History

This folder was gitignored from #430 until #1188. Design notes written in that period stayed private, so older code comments and changelog entries that cite them say "maintainer-private design notes".
