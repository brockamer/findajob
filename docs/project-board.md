# Project Board — How It Works

The GitHub Projects v2 board at [findajob Pipeline](https://github.com/users/brockamer/projects/1) is the **single source of truth for what is being worked on and why**. No markdown tracking files, no separate backlog lists, no TODO.md. If it isn't on the board, it isn't on the roadmap.

This document describes the conventions so anyone (human or Claude session) can triage, prioritize, and move work consistently.

## Columns (Status field)

Five columns, left to right. An issue moves rightward as it progresses.

| Column | Meaning | Expected count |
|---|---|---|
| **Backlog** | Captured but not yet scheduled. Triaged (has Priority) but not actively planned this cycle. | Unbounded |
| **Up Next** | Scheduled to be picked up next. The on-deck queue. When In Progress frees up, the top of Up Next moves over. | 1–3 items |
| **In Progress** | Actively being worked on right now. | 1–3 items |
| **Blocked** | Was pulled to In Progress and then hit an unanticipated stoppage. Has a `## Blocked by` body section naming the unblock owner and the specific event being waited on. Returns to In Progress when unblocked or to Backlog if punted. | 0–2 items |
| **Done** | Closed issues. Auto-populated when an issue closes. | Growing |

**Rules:**
- In Progress should stay small. More than ~3 items means focus is scattered.
- Up Next should be ordered — top item is what gets worked next. Priority field breaks ties within the column.
- Nothing in In Progress without Priority set.
- When an issue closes, it moves to Done automatically.
- An issue with unmet `blockedBy` dependencies is **not** "Blocked" — it's just queued. Items move to Blocked only after being pulled to In Progress and hitting a stoppage.

## Body sections — three independent dimensions

A given issue answers three independent questions, each with its own home:

| Question | Where it lives |
|---|---|
| Where is this work in the flow? | **Status column** (Backlog / Up Next / In Progress / Blocked / Done) |
| What does this issue depend on? | **Native `blockedBy`** — set via the GitHub dependencies API, visible in the "Linked issues" panel and Projects v2 dependency views |
| Who owns getting an actively-stuck item moving? | **`## Blocked by` body section** — present **only** on items currently in the Blocked Status column. Names the person, the specific event, and the expected-by date. |

The `## Depends on` body section, if present, is **prose context only**. The relationship data lives in native `blockedBy`. Don't parse `- #N` bullets out of body text — query `Issue.blockedBy` instead.

The `## Blocks` body section is retired. Express the inverse direction by adding a `blockedBy` edge on the dependent.

## Priority field

Three values. This is the canonical priority signal — **not** the legacy `priority: high / med / low` labels.

| Value | Meaning |
|---|---|
| **High** | Directly advances the current strategic goal (getting a job). Should be addressed before Medium work. Stakes are high, timeline is now. |
| **Medium** | Quality, efficiency, or reliability improvement. Important but not blocking the strategic goal. |
| **Low** | Nice-to-have, future-facing, or optional. Safe to defer indefinitely. |

**Rules:**
- Every open issue on the board must have a Priority set.
- High priority is scarce by design — if everything is High, nothing is.
- Two High items in In Progress at once should be rare and deliberate (e.g., one blocking work and one small bug fix alongside it).
- If the user says "prioritize X," the intent is *X is the top of the queue*, not *X is another High item among many*.

## Labels

Labels describe **what kind of issue it is**, not where it lives on the board. Status and priority come from board fields, not labels.

Active labels:

| Label | Meaning |
|---|---|
| `bug` | Something isn't working |
| `enhancement` | New capability |
| `job-search` | Directly impacts job search results |
| `pipeline-quality` | Reliability, testing, ops |
| `data-hygiene` | Cleanup of stale or inconsistent data |
| `open-source` | Generalization and adoption |
| `documentation` | Docs-only change |
| `big-idea` | Speculative far-horizon concept; not on the active roadmap. Always pair with Priority: Low. |

Legacy labels (being phased out — Priority field is canonical):
- `priority: high`, `priority: med`, `priority: low`

If you see an issue with a `priority:` label but no Priority field value, reconcile by setting the field to match the label and either removing the label or leaving it for later cleanup.

## Dependency relationships

Native GitHub `blockedBy` is the canonical store for "issue X depends on issue Y."

**Add an edge:**
```bash
ID_DEPENDENT=$(gh issue view <X> --repo brockamer/findajob --json id --jq '.id')
ID_BLOCKER=$(gh issue view <Y> --repo brockamer/findajob --json id --jq '.id')
gh api graphql -f query='
  mutation($i: ID!, $b: ID!) {
    addBlockedBy(input: {issueId: $i, blockingIssueId: $b}) {
      issue { number }
    }
  }' -F i="$ID_DEPENDENT" -F b="$ID_BLOCKER"
```

**Read edges:**
```bash
gh api graphql -f query='
  query($o: String!, $r: String!, $n: Int!) {
    repository(owner: $o, name: $r) {
      issue(number: $n) { blockedBy(first: 20) { nodes { number title state } } }
    }
  }' -F o=brockamer -F r=findajob -F n=<X>
```

A `## Depends on` body section, when present, is **human prose** explaining *why* the dependency matters. It does not need to be parseable. The authoritative edge is `blockedBy`.

A `blockedBy` edge does **not** automatically move an issue to the Blocked column. Items move to Blocked only when actively stuck during In Progress.

## Triage checklist — new issue

When a new issue is filed (by the user, by Claude, or by auto-add):

1. **Auto-add to board.** `gh issue create` does not auto-add; use `gh project item-add 1 --owner brockamer --url <issue_url>`. Verify after — occasional race conditions require a retry.
2. **Set Priority** — High / Medium / Low per the definitions above.
3. **Leave Status as Backlog** unless explicitly scheduling.
4. **Apply labels** for issue type (`bug`, `enhancement`, etc.), scope (`job-search`, `pipeline-quality`, etc.), and `big-idea` if it's a speculative parking-lot item.

An issue without Priority will sort into the "no-field" bucket at the bottom of the board — effectively invisible.

## Moving work — status transitions

- **Backlog → Up Next** — when scheduling the next item to work on.
- **Up Next → In Progress** — when starting work. Only one claiming per person/session.
- **In Progress → Done** — happens automatically when the issue closes (via `gh issue close` or PR merge referencing the issue).
- **Any column → Done without closing** — avoid. If the work is done, close the issue; if it's obsolete, close with a comment explaining why.

## Apply gate

Per `CLAUDE.md`: **no new features or elective improvements until the user applies to at least one job that day.** Bug fixes are exempt. This means:

- When In Progress is empty and the apply gate hasn't cleared, don't pull new Medium/Low work in. Clear the gate first.
- High-priority bug fixes and blockers to applying (e.g., damaged prep materials) are always fair game.

## Common inconsistencies to watch for

1. **Label says `priority: high` but Priority field is empty** — reconcile by setting the field.
2. **Status is In Progress but no Priority** — actively-worked items must be fully triaged.
3. **Issue on board but closed** — should auto-move to Done; if not, set status manually.
4. **High-priority backlog items older than two weeks** — either promote to Up Next, downgrade to Medium, or close if no longer relevant.
5. **More than 3 items in In Progress** — focus is scattered; pause and decide which to finish first.

## Fields quick reference (for gh project CLI)

```
Project ID:          PVT_kwHOAgGulc4BUtxZ
Status field ID:     PVTSSF_lAHOAgGulc4BUtxZzhCOoMM
  Backlog:           59bd4809
  Up Next:           f94b6c8d
  In Progress:       87411b49
  Blocked:           e0fccf99
  Done:              1b523c26
Priority field ID:   PVTSSF_lAHOAgGulc4BUtxZzhCWZ08
  High:              f0a4404c
  Medium:            4e8ef0ac
  Low:               79925e2f
```

Example: move an item to Up Next:
```bash
gh project item-edit \
  --project-id PVT_kwHOAgGulc4BUtxZ \
  --id <ITEM_ID> \
  --field-id PVTSSF_lAHOAgGulc4BUtxZzhCOoMM \
  --single-select-option-id f94b6c8d
```
