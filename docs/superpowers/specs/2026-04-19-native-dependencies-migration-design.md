# Native GitHub Issue Dependencies Migration — Design

**Date:** 2026-04-19
**Status:** Draft — pending writing-plans handoff
**Trigger:** Sweep finding on #29 (mislabeled `blocked`); discovery that the codebase has been using body-text `## Depends on` sections while GitHub native `Issue.blockedBy` has been GA the whole time.

---

## Context

The findajob project board uses three signals to express dependency relationships:

1. The `blocked` label
2. A `## Depends on` section in the issue body (structured `- #N` list)
3. A `## Blocks` section in the issue body (rare; #65 is the only open user)

GitHub has GA'd a native dependency model:
- `Issue.blockedBy { nodes { number } }` — query
- `addBlockedBy(issueId, blockingIssueId)` / `removeBlockedBy(...)` — mutation
- Surfaces in the GitHub UI's "Linked issues" panel and in Projects v2 dependency views.

The codebase's adoption of native is currently **0%**. All 33 open issues with dependency content carry it in body text only. The Jared skill's grooming script (`scripts/sweep.py`) regex-parses these sections; the issue-body template scaffolds them.

Additionally, "Blocked" today is expressed as a label rather than a Status column. This conflicts with standard kanban convention (Blocked is a phase of the workflow, not a tag) and dilutes the term — most "blocked" labeled items are in fact deferred-by-prerequisite, not actively stuck.

## Decision

Adopt a three-dimension model:

| Dimension | What it represents | Where it lives |
|---|---|---|
| **Status field** | Where the work is in the flow | Project Status column: `Backlog → Up Next → In Progress → Blocked → Done` |
| **Native `blockedBy`** | The planning DAG: X depends on Y | GitHub native dependencies API |
| **`## Blocked by` body section** | Who owns getting an actively-stuck item unblocked | Free-form body section, present **only** on items in Blocked status |

The `blocked` label is retired. `## Depends on` body sections become free-form prose-only annotations (no structured `- #N` parsing); the relationship data they used to carry moves to native `blockedBy`.

## Architecture

**Three independent dimensions** — separated so each answers a distinct question:

- *Where is this in the flow?* → Status column
- *What does it depend on?* → Native `blockedBy`
- *Who is the human owner of unblocking this right now?* → `## Blocked by` body section

An issue in Backlog can have unmet `blockedBy` dependencies — that's just normal queue state, not "blocked". An issue moves to the Blocked column only when it has been pulled to In Progress and then hit an unanticipated stoppage. From Blocked it returns to In Progress (when unblocked) or to Backlog (if punted).

The `## Blocked by` body section captures the human/organizational aspect that the native field can't: which person, what specific event, by when expected. Only required when an item is in the Blocked status column.

## Phase 1 — Add the Blocked column safely; fix #29

**Lands first.** Phase 2 has nowhere to land until the Blocked column exists.

1. **Snapshot all 91 board items' Status field values** to `/tmp/status-snapshot.json` via GraphQL.
2. **Add "Blocked" option to the Status field** via `updateProjectV2Field`, passing all five options (Backlog, Up Next, In Progress, Blocked, Done) preserving existing names and colors. Place Blocked between In Progress and Done.
3. **Verify the destruction risk.** The Work Stream incident on 2026-04-19 showed `updateProjectV2Field` clears option-keyed values when the option set is rebuilt. Re-query Status values after the mutation. If they survived (option-name-keyed resolution), proceed. If they cleared, restore from snapshot.
4. **Fix #29:**
   - Set native `addBlockedBy(issueId: <#29>, blockingIssueId: <#59>)`.
   - Remove the `blocked` label.
   - Leave the body unchanged (`## Depends on` section remains as prose context).
   - Do **not** move #29 to the Blocked column. It is deferred-by-prerequisite, not actively stuck.
5. **Document the new Status convention** with a minimal note in `docs/project-board.md` — full convention rewrite is Phase 2 scope.

**Branch:** `chore/board-blocked-column`. One small commit. PR opens to `findajob` repo.

## Phase 2 — Full migration; convention rewrite

**Two PRs.** Skill PR shouldn't merge before findajob PR (sweep would expect a model that doesn't yet exist on the live board).

### `findajob` repo PR

1. **Migrate body-text deps to native.** Script:
   - Parse `## Depends on` AND `## Blocks` from all open issues. `## Blocks` is the inverse direction — `#65 ## Blocks: #11, #12` becomes `addBlockedBy(issueId: #11, blockingIssueId: #65)` and `addBlockedBy(issueId: #12, blockingIssueId: #65)`.
   - Build proposed `addBlockedBy` mapping (~13 OPEN→OPEN pairs from `## Depends on` plus #65's two outbound edges; total ~15 mutations).
   - Filter "(none)" entries and prereqs that are already closed.
   - Present mapping for human approval.
   - Apply on confirm.
2. **Convert `## Depends on` body sections to prose.** Replace structured `- #N` lists with one-paragraph prose explaining *why* the dependency matters. Preserve shipping-status annotations ("shipped in PR #64", "supersedes this issue"). Open issues only.
3. **Update `docs/project-board.md`:**
   - Status column section gains the Blocked column with entry/exit rules.
   - New "Body sections" subsection documenting the three-dimension model.
   - Labels table: remove `blocked`.
   - Add a "Dependency relationships" subsection naming native `blockedBy` as canonical.
4. **Remove `blocked` label** from any remaining open issues (#29 already done in Phase 1; verify nothing slipped in). Delete the label from the repo.

### `claude-skills` repo / jared skill PR

5. **Update `assets/issue-body.md.template`:**
   - Drop `## Depends on` and `## Blocks` scaffolds.
   - Keep `## Blocked by` (only for Blocked-status items).
   - Add a comment block noting native deps are set via `addBlockedBy` mutation, not body markup.
6. **Update `scripts/sweep.py`:**
   - Replace regex-based `## Depends on` parsing for blocked-hygiene with native `blockedBy` GraphQL queries.
   - Add new check: items in Blocked status > 7 days flag as aging.
   - Drop the legacy `blocked`-label hygiene check entirely.
7. **Update `references/dependencies.md` and `references/operations.md`** to reflect native-first convention. Remove or downgrade the "body-text fallback" prose.
8. **Update `references/board-sweep.md`** to match the new sweep checks.

## Migration scope

Real OPEN→OPEN dependencies extracted from body audit (Phase 2 step 1):

| Issue | Depends on (open) |
|---|---|
| #29 | #59 |
| #56 | #14, #55 |
| #58 | #89 |
| #60 | #59 |
| #61 | #60 |
| #62 | #61 |
| #63 | #60, #55 |
| #76 | #89 |
| #82 | #12 |
| #87 | #48 |
| #88 | #58 |

Approximately 13 unique pairs. Script will re-derive at execution time in case state has changed.

Issues that have body `## Depends on` containing only "(none)" or only references to already-shipped issues are unaffected.

## Risk and mitigations

- **Status field destruction.** `updateProjectV2Field` proved destructive in the Work Stream incident. Mitigation: snapshot first, restore if cleared. Open question whether option-name-keyed values survive renames — Phase 1 step 3 verifies empirically before Phase 2 proceeds.
- **Cross-repo coordination.** Skill PR depends on findajob PR being merged first. Mitigation: explicit ordering in Phase 2 plan; skill PR description references the findajob PR.
- **Body edits at scale.** Step 2 of Phase 2 modifies 33 issue bodies. Mitigation: script-driven with per-batch human approval; never bulk-apply without review.
- **Transient sweep errors during migration.** While Phase 2 is in flight, the sweep script (old version) and the board state (partially migrated) will disagree. Mitigation: pause routine grooming during migration; resume after both PRs land.

## Out of scope

- **Auto-generated body sections from native deps.** Considered (Option C in brainstorm); rejected as overengineering.
- **Cross-repo dependencies.** Native blockedBy supports them but findajob has no current cross-repo deps; not exercising this path.
- **Sub-issues / parent-child decomposition.** Separate native feature; not in this migration.
- **Status field renames or removal of existing options.** Only adding Blocked.
- **Backfilling closed issues.** Migration touches open issues only.

## Documentation Impact

- **`docs/project-board.md`** (findajob): full Status section update, body-sections subsection, labels table edit, dependency-relationships subsection.
- **`assets/issue-body.md.template`** (jared skill): remove `## Depends on` and `## Blocks` scaffolds; keep `## Blocked by`.
- **`scripts/sweep.py`** (jared skill): new check (Blocked-aging), removed checks (blocked-label hygiene, regex-based dependency parsing).
- **`references/dependencies.md`** (jared skill): rewrite to native-first.
- **`references/operations.md`** (jared skill): update dependency commands; demote body-text fallback.
- **`references/board-sweep.md`** (jared skill): align with new sweep checks.
- **CHANGELOG.md** (findajob): note the convention change at v0.1.x for any external contributors.

No code (Python pipeline) changes. No user-facing pipeline changes.
