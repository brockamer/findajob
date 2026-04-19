# Native GitHub Issue Dependencies Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate findajob's dependency tracking from body-text `## Depends on` sections to GitHub's native `blockedBy` API, add a "Blocked" Status column to the project board, and update the jared skill (template, sweep script, references) to match.

**Architecture:** Three sequential PRs across two repos. **Phase 1** (findajob, single small PR): snapshot Status field, add Blocked option safely, fix #29 with native dep + label removal, document the new column minimally. **Phase 2A** (findajob, larger PR): bulk-migrate ~15 body-text dependency edges to native, convert body sections to prose, fully rewrite `docs/project-board.md`, retire the `blocked` label. **Phase 2B** (claude-skills, dependent on 2A): update the issue-body template, sweep.py blocked-hygiene check, and three reference docs.

**Tech Stack:** `gh` CLI + GraphQL (Projects v2 API, native issue dependencies API), Python 3 for migration scripts (one-shot, lives in `/tmp`), bash for snapshot/restore safety net.

---

## Spec reference

This plan implements `docs/superpowers/specs/2026-04-19-native-dependencies-migration-design.md` (commit `e8d36e0` on `spec/native-dependencies-migration`). Read the spec first if anything below is ambiguous.

## Branch discipline

Per memory `feedback_git_branch_off_origin`, every branch is created from `origin/main`, never from local `main` (which can drift behind via squash-merge). Each PR's first task fetches from origin and creates a fresh branch.

## API contract — verify before applying mutations

The spec uses these GraphQL names: `Issue.blockedBy { nodes { number } }`, `addBlockedBy(issueId, blockingIssueId)`, `removeBlockedBy(...)`. GitHub's GA mutations may instead be named `addIssueDependency` / `removeIssueDependency` with field `Issue.issueDependencies`. **Before applying any dep mutation in Tasks 6 or 12, run a single introspection query to confirm the actual mutation name and shape, and use that name throughout.** Plan code below shows the spec's name; substitute the actual name once verified.

Verify with:
```bash
gh api graphql -f query='{ __type(name: "Mutation") { fields { name } } }' \
  | python3 -c "import sys, json; print('\n'.join(f['name'] for f in json.load(sys.stdin)['data']['__type']['fields'] if 'block' in f['name'].lower() or 'depend' in f['name'].lower()))"
```

If the printed names differ from `addBlockedBy` / `removeBlockedBy`, use the printed names in every mutation step below.

---

## Phase 1 — Add Blocked column safely; fix #29

**Branch:** `chore/board-blocked-column` off `origin/main`
**Repo:** `brockamer/findajob`
**Scope:** ~5 mutations, one tiny doc edit.

### Task 1: Create the Phase 1 branch off origin/main

**Files:**
- No file changes; branch setup only.

- [ ] **Step 1: Confirm clean working tree on findajob repo**

```bash
cd /home/brockamer/Code/findajob
git status
```
Expected: working tree clean (or only untracked `docs/personal/`).

- [ ] **Step 2: Fetch and branch off origin/main**

```bash
git fetch origin
git checkout -b chore/board-blocked-column origin/main
```
Expected: `Switched to a new branch 'chore/board-blocked-column'` and HEAD matches `origin/main`.

- [ ] **Step 3: Verify branch position**

```bash
git log -1 --oneline
git rev-parse HEAD
git rev-parse origin/main
```
Expected: HEAD == origin/main.

### Task 2: Snapshot all Status field values to /tmp

**Files:**
- Create: `/tmp/status-snapshot.json` (not committed)

- [ ] **Step 1: Query all project items with their Status field values**

```bash
gh api graphql -f query='
  query($projectId: ID!, $cursor: String) {
    node(id: $projectId) {
      ... on ProjectV2 {
        items(first: 100, after: $cursor) {
          pageInfo { hasNextPage endCursor }
          nodes {
            id
            content {
              ... on Issue { number title }
              ... on PullRequest { number title }
            }
            fieldValues(first: 20) {
              nodes {
                ... on ProjectV2ItemFieldSingleSelectValue {
                  field { ... on ProjectV2SingleSelectField { id name } }
                  optionId
                  name
                }
              }
            }
          }
        }
      }
    }
  }' -F projectId=PVT_kwHOAgGulc4BUtxZ > /tmp/status-snapshot-page1.json
```
Expected: JSON with `data.node.items.nodes` array. If `pageInfo.hasNextPage` is true, repeat with `-F cursor=<endCursor>` writing to `page2.json`, etc.

- [ ] **Step 2: Combine pages and filter to Status field values only**

```bash
python3 - <<'EOF'
import json, glob
items = []
for f in sorted(glob.glob("/tmp/status-snapshot-page*.json")):
    data = json.load(open(f))
    items.extend(data["data"]["node"]["items"]["nodes"])
snap = []
for it in items:
    content = it.get("content") or {}
    status = next(
        (fv for fv in it["fieldValues"]["nodes"]
         if fv and fv.get("field", {}).get("name") == "Status"),
        None,
    )
    snap.append({
        "item_id": it["id"],
        "issue_number": content.get("number"),
        "title": content.get("title"),
        "status_option_id": status["optionId"] if status else None,
        "status_name": status["name"] if status else None,
    })
json.dump(snap, open("/tmp/status-snapshot.json", "w"), indent=2)
print(f"Wrote {len(snap)} items to /tmp/status-snapshot.json")
print(f"With Status set: {sum(1 for s in snap if s['status_option_id'])}")
EOF
```
Expected: `Wrote 91 items to /tmp/status-snapshot.json` (or current item count). "With Status set" near the total.

- [ ] **Step 3: Sanity-check the snapshot**

```bash
python3 -c "
import json
snap = json.load(open('/tmp/status-snapshot.json'))
from collections import Counter
print(Counter(s['status_name'] for s in snap))
"
```
Expected: distribution across Backlog / Up Next / In Progress / Done with no surprise values. If anything looks off, stop and investigate.

### Task 3: Query existing Status field options (capture names + colors)

**Files:**
- Create: `/tmp/status-options.json` (not committed)

- [ ] **Step 1: Query the Status field's current single-select options**

```bash
gh api graphql -f query='
  query($projectId: ID!) {
    node(id: $projectId) {
      ... on ProjectV2 {
        field(name: "Status") {
          ... on ProjectV2SingleSelectField {
            id
            name
            options { id name color description }
          }
        }
      }
    }
  }' -F projectId=PVT_kwHOAgGulc4BUtxZ > /tmp/status-options.json

cat /tmp/status-options.json | python3 -m json.tool
```
Expected: JSON listing the four current options (Backlog, Up Next, In Progress, Done) with their `color` and `description` values.

### Task 4: Add Blocked option to the Status field

**Files:**
- No file changes (schema mutation only).

- [ ] **Step 1: Build the option-list payload**

```bash
python3 - <<'EOF' > /tmp/status-options-payload.json
import json
data = json.load(open("/tmp/status-options.json"))
opts = data["data"]["node"]["field"]["options"]
# Preserve existing names/colors/descriptions; insert Blocked between In Progress and Done.
new = []
for o in opts:
    new.append({"name": o["name"], "color": o["color"], "description": o["description"] or ""})
    if o["name"] == "In Progress":
        new.append({"name": "Blocked", "color": "RED", "description": "Pulled to In Progress, then hit an unanticipated stoppage. Has a ## Blocked by section naming the unblock owner."})
json.dump(new, open("/tmp/status-options-payload.json", "w"), indent=2)
print(json.dumps(new, indent=2))
EOF
```
Expected: 5 options printed in order: Backlog, Up Next, In Progress, Blocked, Done. If "Blocked" is not between "In Progress" and "Done", stop and inspect the input.

- [ ] **Step 2: Apply the mutation**

```bash
gh api graphql -f query='
  mutation($fieldId: ID!, $options: [ProjectV2SingleSelectFieldOptionInput!]!) {
    updateProjectV2Field(input: {fieldId: $fieldId, singleSelectOptions: $options}) {
      projectV2Field {
        ... on ProjectV2SingleSelectField {
          options { id name color }
        }
      }
    }
  }' -F fieldId=PVTSSF_lAHOAgGulc4BUtxZzhCOoMM \
     -f options="$(cat /tmp/status-options-payload.json)"
```
Expected: response shows 5 options in order. Capture the new "Blocked" option ID from the response — needed if you ever want to move an item there.

**WARNING:** Per memory `feedback_projectv2_field_mutations`, this mutation has historically cleared item values. The next task verifies whether option-name-keyed resolution preserved values; if it didn't, restore from snapshot.

### Task 5: Verify Status values survived; restore if needed

**Files:**
- No file changes.

- [ ] **Step 1: Re-query Status values across all items**

```bash
# Re-run the page query from Task 2 Step 1, writing to /tmp/status-snapshot-after-page*.json
# Then combine + filter exactly as in Task 2 Step 2, but write to /tmp/status-snapshot-after.json
```
(Repeat the Task 2 Step 1 + Step 2 commands, substituting `-after` in filenames.)

- [ ] **Step 2: Diff against the pre-mutation snapshot**

```bash
python3 - <<'EOF'
import json
before = {s["item_id"]: s for s in json.load(open("/tmp/status-snapshot.json"))}
after = {s["item_id"]: s for s in json.load(open("/tmp/status-snapshot-after.json"))}
cleared = []
for iid, b in before.items():
    if not b["status_option_id"]:
        continue
    a = after.get(iid)
    if not a or a["status_option_id"] != b["status_option_id"]:
        cleared.append((b["issue_number"], b["status_name"], a["status_name"] if a else None))
print(f"Items with cleared/changed Status: {len(cleared)}")
for n, was, now in cleared[:20]:
    print(f"  #{n}: {was} -> {now}")
EOF
```
Expected: `Items with cleared/changed Status: 0`. If non-zero, do **not** proceed — go to Step 3.

- [ ] **Step 3 (only if Step 2 reported cleared values): Restore from snapshot**

```bash
python3 - <<'EOF'
import json, subprocess
PROJECT_ID = "PVT_kwHOAgGulc4BUtxZ"
STATUS_FIELD_ID = "PVTSSF_lAHOAgGulc4BUtxZzhCOoMM"
before = json.load(open("/tmp/status-snapshot.json"))
after = {s["item_id"]: s for s in json.load(open("/tmp/status-snapshot-after.json"))}
for b in before:
    iid = b["item_id"]
    if not b["status_option_id"]:
        continue
    a = after.get(iid)
    if a and a["status_option_id"] == b["status_option_id"]:
        continue
    print(f"Restoring #{b['issue_number']} -> {b['status_name']}")
    subprocess.run([
        "gh", "api", "graphql", "-f",
        "query=mutation($p:ID!,$i:ID!,$f:ID!,$o:String!){updateProjectV2ItemFieldValue(input:{projectId:$p,itemId:$i,fieldId:$f,value:{singleSelectOptionId:$o}}){clientMutationId}}",
        "-F", f"p={PROJECT_ID}", "-F", f"i={iid}",
        "-F", f"f={STATUS_FIELD_ID}", "-F", f"o={b['status_option_id']}",
    ], check=True)
print("Restore complete.")
EOF
```
Expected: prints one "Restoring" line per cleared item, no errors. Then re-run Step 2 to confirm zero diff.

- [ ] **Step 4: Commit a marker recording which path was taken**

This is a checkpoint — no file changes yet. Just verify the board state in the GitHub UI: open https://github.com/users/brockamer/projects/1 and confirm Backlog / Up Next / In Progress / Blocked / Done columns appear in that order, and items are still in their original columns.

### Task 6: Add native blockedBy edge for #29 → #59

**Files:**
- No file changes.

- [ ] **Step 1: Verify the actual mutation name (see "API contract" section above)**

```bash
gh api graphql -f query='{ __type(name: "Mutation") { fields { name } } }' \
  | python3 -c "import sys, json; print('\n'.join(f['name'] for f in json.load(sys.stdin)['data']['__type']['fields'] if 'block' in f['name'].lower() or 'depend' in f['name'].lower()))"
```
Expected: prints the canonical mutation names. Use whatever appears (e.g., `addBlockedBy` or `addIssueDependency`) in Step 3 below.

- [ ] **Step 2: Get issue node IDs for #29 and #59**

```bash
ID29=$(gh issue view 29 --repo brockamer/findajob --json id --jq '.id')
ID59=$(gh issue view 59 --repo brockamer/findajob --json id --jq '.id')
echo "#29 = $ID29"
echo "#59 = $ID59"
```
Expected: two non-empty `I_*` node IDs.

- [ ] **Step 3: Apply the dependency edge (#29 is blocked by #59)**

Substitute the actual mutation name from Step 1 if different from `addBlockedBy`.

```bash
gh api graphql -f query='
  mutation($issueId: ID!, $blockingIssueId: ID!) {
    addBlockedBy(input: {issueId: $issueId, blockingIssueId: $blockingIssueId}) {
      issue { number }
    }
  }' -F issueId="$ID29" -F blockingIssueId="$ID59"
```
Expected: response with `issue.number == 29`.

- [ ] **Step 4: Verify the edge is visible**

```bash
gh api graphql -f query='
  query($owner: String!, $repo: String!, $number: Int!) {
    repository(owner: $owner, name: $repo) {
      issue(number: $number) {
        blockedBy(first: 10) { nodes { number title } }
      }
    }
  }' -F owner=brockamer -F repo=findajob -F number=29
```
(If field name differs per Step 1, substitute — e.g., `issueDependencies` instead of `blockedBy`.)
Expected: response lists #59 in the `blockedBy.nodes` array.

### Task 7: Remove the `blocked` label from #29

**Files:**
- No file changes.

- [ ] **Step 1: Confirm #29 has the label**

```bash
gh issue view 29 --repo brockamer/findajob --json labels --jq '.labels[].name'
```
Expected: list including `blocked`.

- [ ] **Step 2: Remove it**

```bash
gh issue edit 29 --repo brockamer/findajob --remove-label blocked
```
Expected: confirmation message; re-run Step 1 and verify `blocked` is gone.

### Task 8: Add minimal Blocked-column note to docs/project-board.md

**Files:**
- Modify: `docs/project-board.md` (Columns table + a short rule note; full rewrite is Phase 2A Task 14)

- [ ] **Step 1: Update the Columns table to add Blocked**

Edit the table at `docs/project-board.md:7-22`. Change the heading from "Four columns" to "Five columns", add a new row between In Progress and Done:

Replace:
```
Four columns, left to right. An issue moves rightward as it progresses.

| Column | Meaning | Expected count |
|---|---|---|
| **Backlog** | Captured but not yet scheduled. Triaged (has Priority + Work Stream) but not actively planned this cycle. | Unbounded |
| **Up Next** | Scheduled to be picked up next. The on-deck queue. When In Progress frees up, the top of Up Next moves over. | 1–3 items |
| **In Progress** | Actively being worked on right now. | 1–3 items |
| **Done** | Closed issues. Auto-populated when an issue closes. | Growing |
```

With:
```
Five columns, left to right. An issue moves rightward as it progresses.

| Column | Meaning | Expected count |
|---|---|---|
| **Backlog** | Captured but not yet scheduled. Triaged (has Priority + Work Stream) but not actively planned this cycle. | Unbounded |
| **Up Next** | Scheduled to be picked up next. The on-deck queue. When In Progress frees up, the top of Up Next moves over. | 1–3 items |
| **In Progress** | Actively being worked on right now. | 1–3 items |
| **Blocked** | Pulled to In Progress and then hit an unanticipated stoppage. Has a `## Blocked by` body section naming the unblock owner. Returns to In Progress when unblocked, or Backlog if punted. Full convention rewrite lands in a follow-up PR. | 0–2 items |
| **Done** | Closed issues. Auto-populated when an issue closes. | Growing |
```

- [ ] **Step 2: Note that "Backlog with unmet deps" is normal, not blocked**

Append this sentence to the existing **Rules:** block (after line 22):

```
- An issue with unmet `blockedBy` dependencies is **not** "Blocked" — it's just queued. Items move to Blocked only after being pulled to In Progress and hitting a stoppage.
```

- [ ] **Step 3: Verify the diff is small**

```bash
git diff docs/project-board.md
```
Expected: only the table row addition, the heading word change, and the new Rules bullet. No unrelated changes.

### Task 9: Commit and open Phase 1 PR

**Files:**
- Commit: `docs/project-board.md`

- [ ] **Step 1: Commit**

```bash
git add docs/project-board.md
git commit -m "$(cat <<'EOF'
Add Blocked Status column to project board

Phase 1 of the native dependencies migration:
- Adds "Blocked" between In Progress and Done in the Status field
- Fixes #29 with native blockedBy edge to #59 (board mutation)
- Removes obsolete `blocked` label from #29 (board mutation)
- Documents the new column minimally; full convention rewrite is Phase 2

Snapshot/restore safety net was applied around the destructive
updateProjectV2Field mutation; option-name-keyed values survived intact.

See docs/superpowers/specs/2026-04-19-native-dependencies-migration-design.md.
EOF
)"
```
Expected: one commit on `chore/board-blocked-column`.

- [ ] **Step 2: Push and open PR**

```bash
git push -u origin chore/board-blocked-column
gh pr create --repo brockamer/findajob --base main --head chore/board-blocked-column \
  --title "Add Blocked Status column to project board (Phase 1)" \
  --body "$(cat <<'EOF'
## Summary
- Adds **Blocked** Status column between In Progress and Done.
- Fixes #29: native \`blockedBy\` edge to #59, removes obsolete \`blocked\` label.
- Minimal doc note in \`docs/project-board.md\`; full rewrite lands in Phase 2A.

## Test plan
- [ ] Verify board shows 5 columns in correct order: Backlog → Up Next → In Progress → Blocked → Done
- [ ] Verify all items still in their pre-mutation columns (snapshot diff was zero)
- [ ] Verify #29 shows #59 in its native blockedBy panel
- [ ] Verify #29 no longer has the \`blocked\` label

See \`docs/superpowers/plans/2026-04-19-native-dependencies-migration.md\` Phase 1.
EOF
)"
```
Expected: PR URL printed.

- [ ] **Step 3: Wait for review and merge before starting Phase 2A.**

Phase 2A's branch must be cut from `origin/main` *after* Phase 1 merges, so that 2A's diff doesn't include 2A's own column-add commit.

---

## Phase 2A — findajob: bulk migration + convention rewrite

**Branch:** `chore/native-dependencies-findajob` off `origin/main` **after Phase 1 has merged**
**Repo:** `brockamer/findajob`
**Scope:** ~15 native dep edges, ~13 issue body rewrites, full doc rewrite, label deletion.

### Task 10: Create the Phase 2A branch off the post-Phase-1 origin/main

**Files:**
- No file changes; branch setup.

- [ ] **Step 1: Confirm Phase 1 PR has merged**

```bash
gh pr view <phase1-pr-number> --repo brockamer/findajob --json state,mergedAt
```
Expected: `state: MERGED`, `mergedAt` populated.

- [ ] **Step 2: Fetch and branch**

```bash
cd /home/brockamer/Code/findajob
git fetch origin
git checkout -b chore/native-dependencies-findajob origin/main
```
Expected: HEAD == origin/main, which now includes the Phase 1 commit.

### Task 11: Build the dependency-mapping script

**Files:**
- Create: `/tmp/migrate-deps.py` (one-shot, not committed)

- [ ] **Step 1: Write the migration script**

```bash
cat > /tmp/migrate-deps.py <<'PYEOF'
#!/usr/bin/env python3
"""
One-shot migration: parse `## Depends on` and `## Blocks` from open findajob issues,
build a list of (dependent_number, blocker_number) edges, and print the proposed
addBlockedBy mutations for human approval. Apply only on --apply.
"""
import argparse, json, re, subprocess, sys

REPO = "brockamer/findajob"

def fetch_open_issues():
    out = subprocess.check_output([
        "gh", "issue", "list", "--repo", REPO,
        "--state", "open", "--limit", "500",
        "--json", "number,title,body,state",
    ])
    return json.loads(out)

def parse_section(body: str, heading: str) -> list[int]:
    """Return list of issue numbers in the named '## <heading>' section."""
    m = re.search(rf"^## {re.escape(heading)}\s*$(.*?)(?=^## |\Z)",
                  body or "", re.MULTILINE | re.DOTALL)
    if not m:
        return []
    section = m.group(1)
    if re.search(r"\(\s*none\s*\)", section, re.IGNORECASE):
        return []
    nums = re.findall(r"#(\d+)", section)
    return [int(n) for n in nums]

def build_edges(issues: list[dict]) -> list[tuple[int, int]]:
    """Return (dependent, blocker) edges from both '## Depends on' (direct) and '## Blocks' (inverse)."""
    open_nums = {i["number"] for i in issues}
    edges = set()
    for i in issues:
        n = i["number"]
        for blocker in parse_section(i["body"] or "", "Depends on"):
            if blocker in open_nums and blocker != n:
                edges.add((n, blocker))
        for dep in parse_section(i["body"] or "", "Blocks"):
            if dep in open_nums and dep != n:
                edges.add((dep, n))
    return sorted(edges)

def get_node_id(num: int) -> str:
    out = subprocess.check_output([
        "gh", "issue", "view", str(num), "--repo", REPO, "--json", "id"
    ])
    return json.loads(out)["id"]

def fetch_existing_blockedBy(num: int, field_name: str = "blockedBy") -> set[int]:
    """Return set of issue numbers already in this issue's native blockedBy."""
    q = (f'query($o:String!,$r:String!,$n:Int!){{repository(owner:$o,name:$r){{'
         f'issue(number:$n){{{field_name}(first:50){{nodes{{number}}}}}}}}}}')
    out = subprocess.check_output([
        "gh", "api", "graphql", "-f", f"query={q}",
        "-F", "o=brockamer", "-F", "r=findajob", "-F", f"n={num}",
    ])
    data = json.loads(out)["data"]["repository"]["issue"][field_name]["nodes"]
    return {n["number"] for n in data}

def apply_edge(dependent_id: str, blocker_id: str, mutation: str = "addBlockedBy"):
    q = (f'mutation($i:ID!,$b:ID!){{{mutation}(input:{{issueId:$i,blockingIssueId:$b}})'
         f'{{issue{{number}}}}}}')
    subprocess.run([
        "gh", "api", "graphql", "-f", f"query={q}",
        "-F", f"i={dependent_id}", "-F", f"b={blocker_id}",
    ], check=True)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="Apply mutations (default: dry-run)")
    ap.add_argument("--mutation-name", default="addBlockedBy",
                    help="GraphQL mutation name (verify via introspection first)")
    ap.add_argument("--field-name", default="blockedBy",
                    help="GraphQL field name on Issue for reading existing edges")
    args = ap.parse_args()

    issues = fetch_open_issues()
    edges = build_edges(issues)
    print(f"Found {len(edges)} OPEN→OPEN dependency edges:")
    for dep, blk in edges:
        print(f"  #{dep} blocked by #{blk}")

    if not args.apply:
        print("\nDry run. Re-run with --apply to apply.")
        return

    # Apply, skipping edges that already exist.
    print("\nApplying...")
    id_cache: dict[int, str] = {}
    for dep, blk in edges:
        existing = fetch_existing_blockedBy(dep, field_name=args.field_name)
        if blk in existing:
            print(f"  skip #{dep}<-#{blk} (already linked)")
            continue
        if dep not in id_cache:
            id_cache[dep] = get_node_id(dep)
        if blk not in id_cache:
            id_cache[blk] = get_node_id(blk)
        apply_edge(id_cache[dep], id_cache[blk], mutation=args.mutation_name)
        print(f"  +  #{dep} blocked by #{blk}")
    print("Done.")

if __name__ == "__main__":
    main()
PYEOF
chmod +x /tmp/migrate-deps.py
```
Expected: file created.

- [ ] **Step 2: Dry-run and review the proposed edges**

```bash
/tmp/migrate-deps.py
```
Expected: prints ~15 edges. The spec lists these (not exhaustive — script re-derives at runtime):
- #29 ← #59
- #56 ← #14, #55
- #58 ← #89
- #60 ← #59
- #61 ← #60
- #62 ← #61
- #63 ← #60, #55
- #76 ← #89
- #82 ← #12
- #87 ← #48
- #88 ← #58
- #11 ← #65, #12 ← #65 (from #65's `## Blocks` section)

If any unexpected edge appears (e.g., points at a closed issue, or two issues that shouldn't be linked), stop and surface to operator.

- [ ] **Step 3: Get human approval before applying**

Print the dry-run output for the operator. **Wait for explicit "apply" confirmation.** Do not auto-apply.

### Task 12: Apply the native blockedBy edges

**Files:**
- No file changes.

- [ ] **Step 1: Verify mutation/field names against current schema**

Use the introspection query from the "API contract" section. Note the actual names — they will be passed via `--mutation-name` and `--field-name` if different from the defaults.

- [ ] **Step 2: Apply (operator-approved)**

```bash
/tmp/migrate-deps.py --apply
# If introspection showed different names:
# /tmp/migrate-deps.py --apply --mutation-name=addIssueDependency --field-name=issueDependencies
```
Expected: one `+ #X blocked by #Y` line per edge, no errors. The #29 edge from Phase 1 should print "skip" (already linked).

- [ ] **Step 3: Spot-check three edges in the GitHub UI**

Open issues #29, #56, #61 in the browser. Each should show its `blockedBy` items in the native "Linked issues" / "Dependencies" panel.

### Task 13: Convert `## Depends on` body sections to prose

**Files:**
- Create: `/tmp/convert-deps-to-prose.py` (one-shot, not committed)

- [ ] **Step 1: Write the conversion script**

```bash
cat > /tmp/convert-deps-to-prose.py <<'PYEOF'
#!/usr/bin/env python3
"""
For each open issue with a structured '## Depends on' section, replace the
'- #N' bullet list with a one-line prose paragraph that names the dependencies
inline. Preserve any annotation suffixes ('shipped in PR #64', 'supersedes', etc.).
Print proposed body diff for each issue and apply only on --apply.

Also drops '## Blocks' sections entirely (their data moved to native blockedBy
on the dependents during Task 12).
"""
import argparse, difflib, json, re, subprocess, sys

REPO = "brockamer/findajob"

def fetch_open_issues():
    out = subprocess.check_output([
        "gh", "issue", "list", "--repo", REPO,
        "--state", "open", "--limit", "500",
        "--json", "number,title,body",
    ])
    return json.loads(out)

SECTION_RE = re.compile(r"(^## (?:Depends on|Blocks)\s*$)(.*?)(?=^## |\Z)",
                        re.MULTILINE | re.DOTALL)

def rewrite_body(body: str) -> str:
    """Drop '## Blocks' entirely; convert '## Depends on' bullets to one-line prose."""
    def replace(m):
        heading = m.group(1).strip()
        section = m.group(2)
        if heading == "## Blocks":
            return ""  # remove entirely
        # Depends on: extract bullets, build prose
        if re.search(r"\(\s*none\s*\)", section, re.IGNORECASE):
            return f"{heading}\nNo open dependencies (relationship now tracked natively).\n\n"
        bullets = re.findall(r"^\s*-\s*(#\d+(?:\s*\([^)]*\))?(?:\s*[-—].*)?)\s*$",
                             section, re.MULTILINE)
        if not bullets:
            return m.group(0)  # leave unchanged if we can't parse
        prose = "Depends on " + ", ".join(bullets) + ". (Relationship is also tracked in native blockedBy; this section is human context.)"
        return f"{heading}\n{prose}\n\n"
    return SECTION_RE.sub(replace, body)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--only", type=int, nargs="*", help="Only process these issue numbers")
    args = ap.parse_args()

    issues = fetch_open_issues()
    changed = []
    for i in issues:
        if args.only and i["number"] not in args.only:
            continue
        new = rewrite_body(i["body"] or "")
        if new != (i["body"] or ""):
            changed.append((i["number"], i["title"], i["body"] or "", new))

    print(f"{len(changed)} issues would change:")
    for num, title, old, new in changed:
        print(f"\n=== #{num}: {title} ===")
        diff = difflib.unified_diff(old.splitlines(), new.splitlines(),
                                    lineterm="", fromfile="before", tofile="after")
        print("\n".join(diff))

    if not args.apply:
        print("\nDry run. Re-run with --apply to apply.")
        return

    print("\nApplying...")
    for num, _, _, new in changed:
        subprocess.run([
            "gh", "issue", "edit", str(num), "--repo", REPO, "--body", new,
        ], check=True)
        print(f"  updated #{num}")
    print("Done.")

if __name__ == "__main__":
    main()
PYEOF
chmod +x /tmp/convert-deps-to-prose.py
```
Expected: file created.

- [ ] **Step 2: Dry-run and review**

```bash
/tmp/convert-deps-to-prose.py
```
Expected: ~13 issues change. Diffs replace `- #N` bullet lists with one-line prose. `## Blocks` sections (e.g., on #65) disappear entirely. Verify the diffs look reasonable.

- [ ] **Step 3: Apply on operator approval, in batches**

If the dry run looks good, apply in two batches with operator review between:

```bash
# First batch — three issues to validate the conversion
/tmp/convert-deps-to-prose.py --apply --only 29 56 65
# Review in browser, then apply remainder
/tmp/convert-deps-to-prose.py --apply
```

- [ ] **Step 4: Spot-check #65 specifically**

#65's `## Blocks` should be gone. Its native `blockedBy` is unchanged (it's a blocker, not a dependent). #11 and #12 should now show #65 in their native `blockedBy`.

```bash
gh issue view 11 --repo brockamer/findajob --json body --jq '.body'
gh issue view 65 --repo brockamer/findajob --json body --jq '.body'
```
Expected: #65's body has no `## Blocks` section. #11's `blockedBy` (queryable per Task 6 Step 4 pattern) lists #65.

### Task 14: Rewrite docs/project-board.md (Status section + body sections + dependency relationships)

**Files:**
- Modify: `docs/project-board.md`

- [ ] **Step 1: Rewrite the Columns table to its final form**

Replace the table that Phase 1 Task 8 left (the one with the "full convention rewrite lands in a follow-up PR" caveat) with the clean final form. The Blocked-column row's Meaning becomes:

```
| **Blocked** | Was pulled to In Progress and then hit an unanticipated stoppage. Has a `## Blocked by` body section naming the unblock owner and the specific event being waited on. Returns to In Progress when unblocked or to Backlog if punted. | 0–2 items |
```

Drop the trailing "full convention rewrite lands in a follow-up PR" sentence.

- [ ] **Step 2: Add a "Body sections" subsection after the Columns/Rules block**

Insert this new subsection between the existing "Columns (Status field)" block and the "Priority field" section:

```markdown
## Body sections — three independent dimensions

A given issue answers three independent questions, each with its own home:

| Question | Where it lives |
|---|---|
| Where is this work in the flow? | **Status column** (Backlog / Up Next / In Progress / Blocked / Done) |
| What does this issue depend on? | **Native `blockedBy`** — set via the GitHub dependencies API, visible in the "Linked issues" panel and Projects v2 dependency views |
| Who owns getting an actively-stuck item moving? | **`## Blocked by` body section** — present **only** on items currently in the Blocked Status column. Names the person, the specific event, and the expected-by date. |

The `## Depends on` body section, if present, is **prose context only**. The relationship data lives in native `blockedBy`. Don't parse `- #N` bullets out of body text — query `Issue.blockedBy` instead.

The `## Blocks` body section is retired. Express the inverse direction by adding a `blockedBy` edge on the dependent.
```

- [ ] **Step 3: Update the Labels table — remove `blocked`**

Remove the `blocked` row from the labels table at `docs/project-board.md:60-69`. The row currently reads:
```
| `blocked` | Waiting on a dependency (also note the dependency in the issue body) |
```
Delete this row entirely.

- [ ] **Step 4: Add a "Dependency relationships" subsection**

Insert before the "Triage checklist — new issue" section:

```markdown
## Dependency relationships

Native GitHub `blockedBy` is the canonical store for "issue X depends on issue Y."

**Add an edge:**
\`\`\`bash
ID_DEPENDENT=$(gh issue view <X> --repo brockamer/findajob --json id --jq '.id')
ID_BLOCKER=$(gh issue view <Y> --repo brockamer/findajob --json id --jq '.id')
gh api graphql -f query='
  mutation($i: ID!, $b: ID!) {
    addBlockedBy(input: {issueId: $i, blockingIssueId: $b}) {
      issue { number }
    }
  }' -F i="$ID_DEPENDENT" -F b="$ID_BLOCKER"
\`\`\`

**Read edges:**
\`\`\`bash
gh api graphql -f query='
  query($o: String!, $r: String!, $n: Int!) {
    repository(owner: $o, name: $r) {
      issue(number: $n) { blockedBy(first: 20) { nodes { number title state } } }
    }
  }' -F o=brockamer -F r=findajob -F n=<X>
\`\`\`

(If the GitHub API uses `addIssueDependency` / `issueDependencies` field names instead, substitute — verify with introspection.)

A `## Depends on` body section, when present, is **human prose** explaining *why* the dependency matters. It does not need to be parseable. The authoritative edge is `blockedBy`.

A `blockedBy` edge does **not** automatically move an issue to the Blocked column. Items move to Blocked only when actively stuck during In Progress.
```

- [ ] **Step 5: Verify the diff**

```bash
git diff docs/project-board.md
```
Expected: removals of `blocked` label row and the Phase-1 caveat sentence; additions of the Body Sections subsection, Dependency Relationships subsection, and final-form Blocked column meaning. No unrelated changes.

### Task 15: Remove the `blocked` label from any remaining open issues + delete from repo

**Files:**
- No file changes.

- [ ] **Step 1: List any open issues still carrying `blocked`**

```bash
gh issue list --repo brockamer/findajob --label blocked --state open --limit 100
```
Expected: empty list (Phase 1 already handled #29). If any others appear, remove the label individually:

```bash
gh issue edit <N> --repo brockamer/findajob --remove-label blocked
```

- [ ] **Step 2: Delete the label from the repo**

```bash
gh label delete blocked --repo brockamer/findajob --yes
```
Expected: success message. Re-running `gh label list --repo brockamer/findajob` should show no `blocked` label.

### Task 16: Commit and open Phase 2A PR

**Files:**
- Commit: `docs/project-board.md`

- [ ] **Step 1: Commit**

```bash
git add docs/project-board.md
git commit -m "$(cat <<'EOF'
Migrate to native GitHub issue dependencies; rewrite board conventions

Phase 2A of the native dependencies migration:
- Migrated ~15 OPEN→OPEN body-text dependency edges to native blockedBy
- Converted ## Depends on body sections to one-line prose annotations
- Removed ## Blocks sections (data is on the dependents' blockedBy now)
- Rewrote docs/project-board.md:
  - Final-form Blocked column meaning (no caveat)
  - New "Body sections" subsection documenting the three-dimension model
  - Removed obsolete `blocked` label from labels table
  - New "Dependency relationships" subsection naming native blockedBy as canonical
- Deleted the `blocked` label from the repo (no remaining users)

The jared skill (issue-body template, sweep.py, references) is updated in a
companion PR on claude-skills.

See docs/superpowers/specs/2026-04-19-native-dependencies-migration-design.md.
EOF
)"
```

- [ ] **Step 2: Push and open PR**

```bash
git push -u origin chore/native-dependencies-findajob
gh pr create --repo brockamer/findajob --base main --head chore/native-dependencies-findajob \
  --title "Migrate to native GitHub issue dependencies (Phase 2A)" \
  --body "$(cat <<'EOF'
## Summary
- Migrated all open OPEN→OPEN body-text \`## Depends on\` and \`## Blocks\` edges to native GitHub \`blockedBy\`.
- Body \`## Depends on\` sections converted to one-line prose; \`## Blocks\` sections removed.
- Full rewrite of \`docs/project-board.md\` — Blocked column finalized, three-dimension body-section model documented, \`blocked\` label retired.
- Companion PR on \`claude-skills\` updates the jared skill (template, sweep, references) — must merge AFTER this PR.

## Test plan
- [ ] Spot-check #29, #56, #61, #65 in the UI: native blockedBy populated, prose preserved
- [ ] Verify #65 has no \`## Blocks\` section but #11 and #12 list #65 in their blockedBy
- [ ] Verify \`gh label list --repo brockamer/findajob\` no longer shows \`blocked\`
- [ ] Re-read \`docs/project-board.md\` end-to-end for coherence

See \`docs/superpowers/plans/2026-04-19-native-dependencies-migration.md\` Phase 2A.
EOF
)"
```
Expected: PR URL printed.

- [ ] **Step 3: Wait for review and merge before Phase 2B PR opens.**

The jared skill's sweep depends on this PR's board state. Opening the skill PR before this merges would leave the sweep expecting a model that doesn't exist on the live board.

---

## Phase 2B — claude-skills (jared skill): template, sweep, references

**Branch:** `chore/native-dependencies-jared` off `origin/main` in the claude-skills repo
**Repo:** the one containing `~/.claude/skills/jared/` (per memory `reference_skills_repo`: `github.com/brockamer/claude-skills`)
**Scope:** template (drop `## Depends on` / `## Blocks` scaffolds), `sweep.py` (replace blocked-hygiene check), three reference docs.

### Task 17: Set up the Phase 2B branch

**Files:**
- No file changes; branch setup.

- [ ] **Step 1: Locate the skills repo root**

```bash
cd /home/brockamer/.claude/skills/jared
git rev-parse --show-toplevel
```
Expected: prints repo root path (likely `/home/brockamer/.claude/skills`).

- [ ] **Step 2: cd to repo root, fetch, branch off origin/main**

```bash
cd "$(git -C /home/brockamer/.claude/skills/jared rev-parse --show-toplevel)"
git status
```
If working tree has uncommitted changes (e.g., the deletions noted at the start of session), surface to operator before proceeding — do not stash blindly.

```bash
git fetch origin
git checkout -b chore/native-dependencies-jared origin/main
```
Expected: HEAD == origin/main.

### Task 18: Update assets/issue-body.md.template

**Files:**
- Modify: `jared/assets/issue-body.md.template`

- [ ] **Step 1: Drop `## Depends on` and `## Blocks` scaffolds; keep `## Blocked by`**

Replace the file's contents with:

```markdown
One-sentence summary of what this issue is about and why it matters.

## Current state
Not started.

## Decisions
(none yet)

## Acceptance criteria
<details>
<summary>Expand</summary>

- Criterion 1
- Criterion 2
- Criterion 3

</details>

<!--
Dependencies are tracked natively via GitHub's `blockedBy` API, not in body markup.
Add an edge with: addBlockedBy(issueId, blockingIssueId). See references/dependencies.md.

Add a "## Blocked by" section ONLY when this issue is in the Blocked Status column.
Format:
  ## Blocked by
  Waiting on <person/event>, expected by <date>. <one-line context>
-->

## Planning
(none)
```

- [ ] **Step 2: Verify the diff**

```bash
git diff jared/assets/issue-body.md.template
```
Expected: removed `## Depends on` / `## Blocks` blocks; added the HTML comment block; `## Blocked by` is documented in the comment but not scaffolded as a default section.

### Task 19: Replace the blocked-hygiene check in sweep.py

**Files:**
- Modify: `jared/scripts/sweep.py:240-247` (and any related references in the docstring at top)

- [ ] **Step 1: Read the existing check**

The current `check_blocked_hygiene` function at `jared/scripts/sweep.py:240` walks `issues_by_number`, looks for the `blocked` label, and flags items whose body lacks `## Blocked by`. This is replaced by:
1. A native `blockedBy` query for each open issue.
2. An aging check: items in **Blocked status** for >7 days flag.
3. Drop the `blocked`-label check entirely.

- [ ] **Step 2: Add a helper that queries native blockedBy in one batch**

Add this near the other `gh` wrappers (around `jared/scripts/sweep.py:90`):

```python
def fetch_native_blocked_by(repo: str) -> dict[int, list[dict]]:
    """One GraphQL call to get blockedBy for all open issues. Returns {number: [{number, state}]}.

    Field name may be 'blockedBy' or 'issueDependencies' depending on schema version.
    Tries 'blockedBy' first, falls back to 'issueDependencies' on schema error.
    """
    owner, name = repo.split("/", 1)
    for field in ("blockedBy", "issueDependencies"):
        q = (
            'query($o:String!,$r:String!,$c:String){repository(owner:$o,name:$r){'
            f'issues(first:100,after:$c,states:OPEN){{pageInfo{{hasNextPage endCursor}}'
            f'nodes{{number {field}(first:20){{nodes{{number state}}}}}}}}}}}}'
        )
        result: dict[int, list[dict]] = {}
        cursor = None
        try:
            while True:
                args = ["gh", "api", "graphql", "-f", f"query={q}",
                        "-F", f"o={owner}", "-F", f"r={name}"]
                if cursor:
                    args += ["-F", f"c={cursor}"]
                p = subprocess.run(args, capture_output=True, text=True, check=True)
                data = json.loads(p.stdout)["data"]["repository"]["issues"]
                for node in data["nodes"]:
                    result[node["number"]] = node[field]["nodes"]
                if not data["pageInfo"]["hasNextPage"]:
                    break
                cursor = data["pageInfo"]["endCursor"]
            return result
        except subprocess.CalledProcessError as e:
            if "Field" in e.stderr and "doesn" in e.stderr:
                continue  # try next field name
            raise
    raise RuntimeError("Neither blockedBy nor issueDependencies field is available")
```

- [ ] **Step 3: Replace `check_blocked_hygiene`**

Replace the function at `jared/scripts/sweep.py:240-247` with:

```python
def check_blocked_status_hygiene(
    items: list[dict],
    issues_by_number: dict[int, dict],
    blocked_aging_days: int,
) -> list[str]:
    """For items currently in 'Blocked' Status: must have a '## Blocked by' body section.
    Also flag items in Blocked status for >N days as aging."""
    findings: list[str] = []
    today = dt.date.today()
    for item in items:
        status = (item.get("status") or "").strip()
        if status != "Blocked":
            continue
        content = item.get("content") or {}
        n = content.get("number")
        if not n or n not in issues_by_number:
            continue
        issue = issues_by_number[n]
        body = issue.get("body") or ""
        if "## Blocked by" not in body:
            findings.append(f"#{n}: in Blocked status but body has no `## Blocked by` section")
        # Aging: use updatedAt as a proxy for "time in Blocked".
        # Real time-in-status would require querying ProjectV2 events; out of scope.
        updated = issue.get("updatedAt", "")
        if updated:
            updated_date = dt.datetime.fromisoformat(updated.replace("Z", "+00:00")).date()
            age = (today - updated_date).days
            if age > blocked_aging_days:
                findings.append(f"#{n}: in Blocked status with no activity for {age} days")
    return findings


def check_native_dependencies(
    blocked_by: dict[int, list[dict]],
    issues_by_number: dict[int, dict],
) -> list[str]:
    """Flag native blockedBy edges that point at closed issues — should be removed."""
    findings: list[str] = []
    for n, blockers in blocked_by.items():
        if n not in issues_by_number:
            continue
        for b in blockers:
            if b.get("state") == "CLOSED":
                findings.append(f"#{n}: blockedBy #{b['number']} which is closed — propose removing edge")
    return findings
```

- [ ] **Step 4: Wire the new checks into the runner**

Find the call site at `jared/scripts/sweep.py:464` (`check_blocked_hygiene(issues_by_number)`) and replace with calls to the two new functions. The runner needs to:
1. Call `fetch_native_blocked_by(repo)` once.
2. Call `check_blocked_status_hygiene(items, issues_by_number, args.blocked_aging_days)`.
3. Call `check_native_dependencies(blocked_by, issues_by_number)`.

Also add a CLI flag near the other thresholds:

```python
parser.add_argument("--blocked-aging-days", type=int, default=7,
                    help="Flag Blocked-status items with no activity beyond this (default: 7)")
```

- [ ] **Step 5: Update the docstring at the top**

Edit `jared/scripts/sweep.py:1-29` so the check list reads:

```
  1. Metadata completeness — every open item has Priority
  2. WIP cap — In Progress within limit, flag stalled items
  3. Up Next queue — size and pullable-top check
  4. Aging — High-priority Backlog items >14 days old
  5. Blocked status hygiene — items in Blocked column have `## Blocked by` section; flag Blocked items >7 days
  6. Native dependency hygiene — blockedBy edges pointing at closed issues
  7. Legacy priority labels — should be stripped
  8. Plan/spec drift — active plans citing closed issues, plans without issues
  9. Session-note freshness — In Progress items without recent Session notes
```

(Drop the `Work Stream` reference from check 1 — that field was removed earlier in the project.)

- [ ] **Step 6: Run sweep against findajob to validate**

```bash
cd ~/Code/findajob
~/.claude/skills/jared/scripts/sweep.py
```
Expected: runs without crash. Output includes "Blocked status hygiene" and "Native dependency hygiene" sections; the old "Blocked hygiene" section is gone. If #29 is no longer Blocked-status (it shouldn't be — the spec explicitly says #29 is deferred-by-prereq, not blocked), no findings under the new check.

### Task 20: Update references/dependencies.md

**Files:**
- Modify: `jared/references/dependencies.md`

- [ ] **Step 1: Rewrite the doc to lead with native, demote body-text**

Key edits:
- Drop the "Fallback: body conventions" section (lines 56–73). Body text is no longer a parsed mechanism.
- In its place, a short "Body context (not parsed)" subsection: `## Depends on` may exist as prose for human readers, but it is not authoritative and is not parsed by sweeps. Native `blockedBy` is canonical.
- The `## Blocks` convention is retired. Add an inverse edge to the dependent's `blockedBy` instead.
- Update the GraphQL examples to use `addBlockedBy` / `blockedBy` (with a note that older deployments used `addIssueDependency` / `issueDependencies` and to verify via introspection).
- Update `dependency-graph.py` mention: it now reads native only, not body text. (If `dependency-graph.py` itself needs updating, file a follow-up issue — it's outside this PR's scope.)

- [ ] **Step 2: Verify the diff is bounded**

```bash
git diff jared/references/dependencies.md
```
Expected: removals concentrated in the body-conventions block; additions in the body-context note and the GraphQL example updates.

### Task 21: Update references/operations.md

**Files:**
- Modify: `jared/references/operations.md`

- [ ] **Step 1: Update the placeholder key — remove `<work-stream-field-id>`**

Drop the `<work-stream-field-id>` line from the placeholder list at `jared/references/operations.md:11`.

- [ ] **Step 2: Add a "Status: Blocked" subsection in the moves section**

Document moving an item to the Blocked column the same way other Status moves are documented. The pattern:

```bash
# Move issue #N to Blocked
gh project item-edit \
  --project-id <project-id> \
  --id <item-id> \
  --field-id <status-field-id> \
  --single-select-option-id <blocked-option-id>
```

Note: when moving to Blocked, also add a `## Blocked by` section to the issue body naming the unblock owner. When moving away from Blocked, remove that section.

- [ ] **Step 3: Add a "Native dependency mutations" subsection**

Document `addBlockedBy` / `removeBlockedBy` (with the introspection-fallback note) using the same shape as other operation examples in the file. Cross-reference `references/dependencies.md` for the conceptual treatment.

- [ ] **Step 4: Verify the diff**

```bash
git diff jared/references/operations.md
```
Expected: small additions in two subsections; one removal from the placeholder key.

### Task 22: Update references/board-sweep.md

**Files:**
- Modify: `jared/references/board-sweep.md`

- [ ] **Step 1: Replace check 4 ("Blocked items")**

The current check 4 at `jared/references/board-sweep.md:36-37` reads:

```
### 4. Blocked items

Every `blocked`-labeled item needs a `## Blocked by` section in its body naming the blocker and the owner of unblocking. If the label is on but the section is missing, flag for fix. If the named blocker issue is now closed, propose unblocking.
```

Replace with:

```
### 4. Blocked-status items

Every item currently in the **Blocked Status column** needs a `## Blocked by` section in its body naming the unblock owner and what specifically is being waited on. If a Blocked-status item lacks this section, flag for fix.

Also flag items that have been in Blocked status for more than 7 days — propose unblocking, punting back to Backlog, or breaking the blocker into a smaller issue.

(The old `blocked` label is retired; a `blockedBy` edge alone does not put an issue in the Blocked column. Items move to Blocked only when actively stuck after being pulled to In Progress.)
```

- [ ] **Step 2: Update check 9 ("Dependency hygiene")**

The current check 9 at `jared/references/board-sweep.md:64-71` references "native GitHub issue dependencies or `## Depends on` body section". Update to drop the body-section fallback — body sections are no longer parsed:

```
### 9. Dependency hygiene

For each open issue with native `blockedBy` edges:

- Referenced blocker still exists and is open?
- Dependent's Priority higher than or equal to its blockers' Priority? (Priority inversions are red flags.)
- Any circular dependencies? Fix hard.
- Edges pointing at closed issues — propose removing.

`## Depends on` body sections (if present) are prose context only and are not parsed.
```

- [ ] **Step 3: Verify the diff**

```bash
git diff jared/references/board-sweep.md
```
Expected: changes confined to checks 4 and 9.

### Task 23: Commit and open Phase 2B PR

**Files:**
- Commit: `jared/assets/issue-body.md.template`, `jared/scripts/sweep.py`, `jared/references/dependencies.md`, `jared/references/operations.md`, `jared/references/board-sweep.md`

- [ ] **Step 1: Verify Phase 2A merged before opening this PR**

```bash
gh pr view <phase-2a-pr-number> --repo brockamer/findajob --json state,mergedAt
```
Expected: `state: MERGED`. If not yet merged, wait — opening this PR earlier could leave it referencing a state that doesn't exist on the findajob board.

- [ ] **Step 2: Commit**

```bash
git add jared/assets/issue-body.md.template jared/scripts/sweep.py \
        jared/references/dependencies.md jared/references/operations.md \
        jared/references/board-sweep.md
git commit -m "$(cat <<'EOF'
jared: switch to native GitHub issue dependencies

Phase 2B of the native-dependencies migration (companion to the findajob
PR that retired body-text dependencies).

- assets/issue-body.md.template: drop ## Depends on and ## Blocks
  scaffolds; document via comment that deps are set via addBlockedBy.
- scripts/sweep.py: replace `blocked` label hygiene check with two
  new checks: Blocked-Status hygiene (## Blocked by present + 7-day
  aging) and native dependency hygiene (edges pointing at closed
  issues).
- references/dependencies.md: rewrite to lead with native; demote
  body conventions to "prose context only".
- references/operations.md: add Status:Blocked move pattern and
  native dep mutation patterns; drop work-stream placeholder.
- references/board-sweep.md: align check 4 (Blocked-status not
  blocked-label) and check 9 (drop body-section parsing).

Depends on findajob#<phase-2a-pr> being merged first so the live board
matches the model these checks expect.
EOF
)"
```

- [ ] **Step 3: Push and open PR**

```bash
git push -u origin chore/native-dependencies-jared
gh pr create --base main --head chore/native-dependencies-jared \
  --title "jared: switch to native GitHub issue dependencies (Phase 2B)" \
  --body "$(cat <<'EOF'
## Summary
- Companion to findajob#<phase-2a-pr>, which retired body-text dependencies on the live board.
- Updates the issue-body template, sweep script, and three reference docs to match the native-first model.
- Sweep replaces the \`blocked\`-label hygiene check with Blocked-Status hygiene + native dependency hygiene.

## Test plan
- [ ] Run \`scripts/sweep.py\` against findajob — completes without errors
- [ ] Filing a new issue via \`/jared-file\` produces a body without \`## Depends on\` / \`## Blocks\` scaffolds
- [ ] Sweep flags a manually-Blocked-status item without \`## Blocked by\` (manual QA)

See findajob's \`docs/superpowers/plans/2026-04-19-native-dependencies-migration.md\` Phase 2B.
EOF
)"
```
Expected: PR URL printed.

---

## Documentation Impact

- **`docs/project-board.md`** (findajob): Phase 1 adds the Blocked column row + a Rules note (Task 8). Phase 2A finalizes the column meaning, adds the Body Sections subsection, removes the `blocked` label row, adds the Dependency Relationships subsection (Task 14).
- **`docs/superpowers/specs/2026-04-19-native-dependencies-migration-design.md`** (findajob): the spec itself, already committed on `spec/native-dependencies-migration` (commit e8d36e0). Survives both PRs as historical context.
- **`docs/superpowers/plans/2026-04-19-native-dependencies-migration.md`** (findajob, this file): committed alongside the Phase 1 PR. Archived to `docs/superpowers/plans/archived/2026-04/` after Phase 2B merges (per `references/plan-spec-integration.md`).
- **`jared/assets/issue-body.md.template`** (claude-skills): drop `## Depends on` and `## Blocks` scaffolds; add HTML comment documenting native (Task 18).
- **`jared/scripts/sweep.py`** (claude-skills): replace `check_blocked_hygiene` with `check_blocked_status_hygiene` + `check_native_dependencies`; add `--blocked-aging-days` flag; update top-of-file docstring (Task 19).
- **`jared/references/dependencies.md`** (claude-skills): demote body-text to "prose context only"; native first (Task 20).
- **`jared/references/operations.md`** (claude-skills): add Status:Blocked move + native dep mutations; drop work-stream placeholder (Task 21).
- **`jared/references/board-sweep.md`** (claude-skills): align check 4 (Blocked-status) and check 9 (drop body parsing) (Task 22).
- **CHANGELOG.md** (findajob): Add a v0.1.x note about the convention change for any external contributors. Single line under "Unreleased" or the next version bucket: "Project board: native GitHub issue dependencies replace body-text `## Depends on` / `## Blocks`. Added Blocked Status column. Retired `blocked` label." (Add this in Phase 2A Task 16's commit if a CHANGELOG exists; otherwise skip.)

No code-pipeline (`src/findajob/`, `scripts/triage.py`, etc.) changes. No user-facing pipeline behavior change.

## Whole-feature verification (run after Phase 2B merges)

- [ ] `gh label list --repo brockamer/findajob` does not include `blocked`.
- [ ] `gh issue list --repo brockamer/findajob --label blocked` returns empty.
- [ ] Spot-check 5 issues across the dep map (#29, #56, #61, #65, #82): native `blockedBy` populated correctly; body has prose `## Depends on` (or no section); no `## Blocks` sections anywhere on open issues.
- [ ] GitHub Project board shows 5 Status columns in correct order.
- [ ] `~/.claude/skills/jared/scripts/sweep.py` runs cleanly against findajob and findings reflect the new model.
- [ ] `docs/project-board.md` reads coherently end-to-end with no stale `blocked`-label / body-text-deps references.
- [ ] Filing a fresh test issue via `/jared-file` produces the new body shape (no `## Depends on` / `## Blocks` scaffolds).

## Self-review notes

Spec coverage:
- Phase 1 (spec section "Phase 1") → Tasks 1–9
- Phase 2 findajob (spec section "findajob repo PR") → Tasks 10–16
- Phase 2 jared (spec section "claude-skills repo / jared skill PR") → Tasks 17–23
- Spec migration scope table → re-derived at Task 11 Step 2 from live state
- Spec risk: Status field destruction → snapshot+verify+restore in Tasks 2, 4, 5
- Spec risk: cross-repo coordination → explicit ordering in Tasks 9 (wait), 17 (wait), 23 (wait)
- Spec risk: body edits at scale → batched + per-batch operator approval in Task 13
- Spec risk: transient sweep errors during migration → operator pauses grooming during the migration window (call out at execution time)

Type/name consistency:
- `addBlockedBy` / `Issue.blockedBy` used in Tasks 6, 12; introspection step at top of Phase 1 Task 6 + Phase 2A Task 12 substitutes the actual schema name; sweep.py helper in Task 19 tries both names.
- Status field ID `PVTSSF_lAHOAgGulc4BUtxZzhCOoMM` from `docs/project-board.md` quick-reference; Project ID `PVT_kwHOAgGulc4BUtxZ` likewise.
- New "Blocked" option ID is captured at Task 4 Step 2 and not used again until follow-up moves.
