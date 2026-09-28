# Plans and specs

Design notes for public findajob features. Everything in this folder is committed and public.

| Folder | Holds |
|---|---|
| `specs/` | What a feature does and why — the design and its decision log, written while the design is settled. |
| `plans/` | How it is built — numbered tasks with files, verification commands and commit messages. |
| `experiments/` | The setup and results of a measurement or trial. |

Name each file `YYYY-MM-DD-short-feature-slug.md`, dated for ordering. Put the issue number in the slug when there is one. A mid-implementation handoff uses the `-CHECKPOINT.md` suffix and is deleted once the next session resumes.

The required sections of a plan (including the mandatory **Documentation Impact** section) are in [`AGENTS.md` § Plan Structure](../../AGENTS.md#plan-structure).

## What does not go here

The rules in [`AGENTS.md` § PII and Domain-Neutrality](../../AGENTS.md#pii-and-domain-neutrality) apply to every file in this folder:

- no real names, emails, keys, host names, deployment paths or instance names;
- no content that locks the pipeline to one career field;
- nothing about running a specific instance of findajob. That work is tracked in the maintainer's private tracker.

## History

This folder was gitignored from #430 until #1188. Design notes written in that period stayed private, so older code comments and changelog entries that cite them say "maintainer-private design notes".
