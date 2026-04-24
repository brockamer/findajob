# OpenRouter Phase 2 Cutover Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Flip 10 pipeline roles to `openrouter:*` routes and upgrade `resume_tailor` + `cover_letter_writer` from Opus 4.6 → 4.7, with a parallel eval gate on the operator stack before merge.

**Architecture:** Pure config. No Python, no schema, no scheduler changes. Updates land in `ops/aichat-ng/models-override.yaml` (catalog), `ops/aichat-ng/config.yaml.example` (default), `config/roles/*.md` (role front-matter), `CLAUDE.md` (context table), `CHANGELOG.md` (migration notice), `docs/setup/configure.md` (OR-key rotation note). Eval uses shadow role files on the operator stack on docker.lan to A/B the 4 quality-critical roles against the same inputs used at original prep time.

**Tech Stack:** `aichat-ng` (0.31.0), YAML config, Markdown front-matter, Docker Compose bind-mount deploys, GitHub PR workflow.

**Spec:** `docs/superpowers/specs/2026-04-24-openrouter-phase-2-cutover-design.md`

**Issue:** #250

---

## Context notes for the implementer

- **Branch off `origin/main`, not local `main`.** Local `main` drifts from `origin/main` via squash-merge. `git fetch origin && git checkout -b feat/250-openrouter-phase-2-cutover origin/main`.
- **This change carries the `migration-required` label.** External operators pulling `:latest` or a new `:vMAJOR.MINOR` must manually edit their bind-mounted `state/aichat_ng/config.yaml` and `state/aichat_ng/models-override.yaml` — the image entrypoint only seeds these files on first install, it never overwrites existing ones.
- **Operator stack (`findajob-<operator>`) runs `:latest` and is the eval target.** Beta-tester stacks (`findajob-<beta>`) pin to `:vMAJOR.MINOR` and pick up this change only when a new minor is tagged.
- **Pre-commit hook blocks personal identifiers.** If the hook fails on a tracked file, use role-based references (`operator`, `beta-tester`) and placeholder paths (`findajob-<operator>`, `findajob-<beta>`) instead of real names. See commit `3078e1a` for examples. The hook lives at `.git/hooks/pre-commit` (untracked, per-clone).
- **No Python test suite changes expected.** Run `uv run pytest tests/ -q` after each commit as a no-regression sanity check, since role front-matter is read by the pipeline at runtime. Expected: all tests pass, no new failures.
- **Aichat-ng is not on the laptop.** All role verification commands in this plan run via `ssh docker.lan`.

---

## File Structure

**Created:**
- `docs/superpowers/plans/2026-04-24-openrouter-phase-2-cutover.md` — this plan (already being written)

**Modified:**
- `ops/aichat-ng/models-override.yaml` — catalog additions under `- provider: openrouter`
- `ops/aichat-ng/config.yaml.example` — top-level `model:` default
- `config/roles/resume_tailor.md` — front-matter `model:`
- `config/roles/cover_letter_writer.md` — front-matter `model:`
- `config/roles/briefing_writer.md` — front-matter `model:`
- `config/roles/outreach_drafter.md` — front-matter `model:`
- `config/roles/company_researcher.md` — front-matter `model:`
- `config/roles/fit_analyst.md` — front-matter `model:`
- `config/roles/resume_change_reviewer.md` — front-matter `model:`
- `config/roles/network_analyst.md` — front-matter `model:`
- `CLAUDE.md` — Pipeline Context Table rows
- `CHANGELOG.md` — new `[Unreleased]` entry
- `docs/setup/configure.md` — add rotating-keys subsection

**Not touched:**
- `config/roles/job_scorer.md` — already on OR
- `config/roles/onboarding_interviewer.md` — inherits default (captured via config.yaml.example default flip)
- Anything under `src/findajob/`, `scripts/`, `tests/`

---

## Task 1: Branch off origin/main

**Files:**
- None (git-only)

- [ ] **Step 1: Fetch latest origin and branch**

Run:

```
git fetch origin && git checkout -b feat/250-openrouter-phase-2-cutover origin/main
```

Expected: `Switched to a new branch 'feat/250-openrouter-phase-2-cutover'` and the new branch is at the same commit as `origin/main`.

- [ ] **Step 2: Verify branch point**

Run:

```
git log --oneline -5 && git status
```

Expected: HEAD matches `origin/main`'s tip; working tree clean (untracked screenshots in the repo root are pre-existing and should be ignored).

---

## Task 2: Add Opus 4.7 + gemini-3-flash-preview to the openrouter catalog

**Files:**
- Modify: `ops/aichat-ng/models-override.yaml` — insert two new entries under the existing `- provider: openrouter` block (which starts around line 1272).

The openrouter block already contains entries for `anthropic/claude-opus-4.6`, `anthropic/claude-sonnet-4.6`, `perplexity/sonar-reasoning-pro`, and `deepseek/deepseek-v3.2`. Two entries are missing and must be added for Phase 2: `anthropic/claude-opus-4.7` and `google/gemini-3-flash-preview`.

- [ ] **Step 1: Confirm missing entries**

Run:

```
grep -n "anthropic/claude-opus-4.7\|google/gemini-3-flash-preview" ops/aichat-ng/models-override.yaml
```

Expected: no output (both strings absent from the file).

- [ ] **Step 2: Add `anthropic/claude-opus-4.7` entry**

Locate the block starting `  - name: anthropic/claude-opus-4.6` (around line 1372). Insert the new entry **immediately before** `anthropic/claude-opus-4.6` so 4.7 comes first:

```yaml
  - name: anthropic/claude-opus-4.7
    type: chat
    max_input_tokens: 200000
    input_price: 5.0
    output_price: 25.0
    max_output_tokens: 8192
    require_max_tokens: true
    supports_vision: true
    supports_function_calling: true
```

Match the indentation of the neighboring entries exactly: `  - name:` has 2 leading spaces.

- [ ] **Step 3: Add `google/gemini-3-flash-preview` entry**

Locate the block starting `  - name: google/gemini-2.5-flash` (around line 1333) inside the openrouter provider. Insert the new entry **immediately before** `google/gemini-2.5-flash`:

```yaml
  - name: google/gemini-3-flash-preview
    type: chat
    max_input_tokens: 1048576
    input_price: 0.3
    output_price: 2.5
    supports_vision: true
    supports_function_calling: true
```

Same indentation convention.

- [ ] **Step 4: YAML sanity check**

Run:

```
python3 -c "import yaml; yaml.safe_load(open('ops/aichat-ng/models-override.yaml'))" && echo "parse ok"
```

Expected: `parse ok`. If PyYAML raises `yaml.YAMLError`, fix the indentation.

- [ ] **Step 5: Verify both entries are discoverable**

Run:

```
grep -n "anthropic/claude-opus-4.7\|google/gemini-3-flash-preview" ops/aichat-ng/models-override.yaml
```

Expected: two matching lines, each inside the openrouter provider block.

- [ ] **Step 6: Run tests**

Run:

```
uv run pytest tests/ -q
```

Expected: all existing tests pass; the catalog file isn't imported by the test suite so no new failures should appear.

- [ ] **Step 7: Commit**

```
git add ops/aichat-ng/models-override.yaml
git commit -m "$(cat <<'EOF'
feat(aichat-ng): add openrouter catalog entries for Phase 2 (#250)

- anthropic/claude-opus-4.7 (same pricing as 4.6; thinking-capable)
- google/gemini-3-flash-preview (now on OR per Phase 1 verdict)

Precursor to the role cutover; entries are inert until a role
front-matter points at them.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Flip the 4 Claude-route role files

**Files:**
- Modify: `config/roles/resume_tailor.md` — front-matter line 2
- Modify: `config/roles/cover_letter_writer.md` — front-matter line 2
- Modify: `config/roles/briefing_writer.md` — front-matter line 2
- Modify: `config/roles/outreach_drafter.md` — front-matter line 2

- [ ] **Step 1: Flip `resume_tailor.md`**

Change the `model:` line from `claude:claude-opus-4-6:thinking` to `openrouter:anthropic/claude-opus-4.7`.

Resulting front-matter:

```yaml
---
model: openrouter:anthropic/claude-opus-4.7
max_tokens: 4096
temperature: 0.4
---
```

Do not change `max_tokens` or `temperature`.

- [ ] **Step 2: Flip `cover_letter_writer.md`**

Change `claude:claude-opus-4-6:thinking` → `openrouter:anthropic/claude-opus-4.7`.

Resulting front-matter:

```yaml
---
model: openrouter:anthropic/claude-opus-4.7
max_tokens: 4096
temperature: 0.6
---
```

- [ ] **Step 3: Flip `briefing_writer.md`**

Change `claude:claude-sonnet-4-6:thinking` → `openrouter:anthropic/claude-sonnet-4.6`.

Resulting front-matter:

```yaml
---
model: openrouter:anthropic/claude-sonnet-4.6
max_tokens: 4096
temperature: 0.3
---
```

- [ ] **Step 4: Flip `outreach_drafter.md`**

Change `claude:claude-sonnet-4-6` → `openrouter:anthropic/claude-sonnet-4.6`.

Resulting front-matter:

```yaml
---
model: openrouter:anthropic/claude-sonnet-4.6
temperature: 0.5
---
```

- [ ] **Step 5: Verify all 4 edits**

Run:

```
grep -H "^model:" config/roles/resume_tailor.md config/roles/cover_letter_writer.md config/roles/briefing_writer.md config/roles/outreach_drafter.md
```

Expected output (exact):

```
config/roles/resume_tailor.md:model: openrouter:anthropic/claude-opus-4.7
config/roles/cover_letter_writer.md:model: openrouter:anthropic/claude-opus-4.7
config/roles/briefing_writer.md:model: openrouter:anthropic/claude-sonnet-4.6
config/roles/outreach_drafter.md:model: openrouter:anthropic/claude-sonnet-4.6
```

- [ ] **Step 6: Run tests**

Run:

```
uv run pytest tests/ -q
```

Expected: all tests pass.

- [ ] **Step 7: Commit**

```
git add config/roles/resume_tailor.md config/roles/cover_letter_writer.md config/roles/briefing_writer.md config/roles/outreach_drafter.md
git commit -m "$(cat <<'EOF'
feat(roles): flip Claude-backed roles to OpenRouter (#250)

- resume_tailor: claude:claude-opus-4-6:thinking -> openrouter:anthropic/claude-opus-4.7
- cover_letter_writer: claude:claude-opus-4-6:thinking -> openrouter:anthropic/claude-opus-4.7
- briefing_writer: claude:claude-sonnet-4-6:thinking -> openrouter:anthropic/claude-sonnet-4.6
- outreach_drafter: claude:claude-sonnet-4-6 -> openrouter:anthropic/claude-sonnet-4.6

Opus 4.7 upgrade per Phase 1 verdict (#22): same pricing as 4.6,
small-to-moderate quality edge on real-pipeline prompts.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Flip the 2 Perplexity-route role files

**Files:**
- Modify: `config/roles/company_researcher.md` — front-matter line 2
- Modify: `config/roles/fit_analyst.md` — front-matter line 2

- [ ] **Step 1: Flip `company_researcher.md`**

Change `model: perplexity:sonar-reasoning-pro` → `model: openrouter:perplexity/sonar-reasoning-pro`.

Resulting front-matter:

```yaml
---
model: openrouter:perplexity/sonar-reasoning-pro
temperature: 0.2
---
```

- [ ] **Step 2: Flip `fit_analyst.md`**

Change `model: perplexity:sonar-reasoning-pro` → `model: openrouter:perplexity/sonar-reasoning-pro`.

Resulting front-matter:

```yaml
---
model: openrouter:perplexity/sonar-reasoning-pro
temperature: 0.2
---
```

- [ ] **Step 3: Verify**

Run:

```
grep -H "^model:" config/roles/company_researcher.md config/roles/fit_analyst.md
```

Expected:

```
config/roles/company_researcher.md:model: openrouter:perplexity/sonar-reasoning-pro
config/roles/fit_analyst.md:model: openrouter:perplexity/sonar-reasoning-pro
```

- [ ] **Step 4: Run tests**

Run:

```
uv run pytest tests/ -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```
git add config/roles/company_researcher.md config/roles/fit_analyst.md
git commit -m "$(cat <<'EOF'
feat(roles): flip Perplexity-backed roles to OpenRouter (#250)

- company_researcher: perplexity:sonar-reasoning-pro -> openrouter:perplexity/sonar-reasoning-pro
- fit_analyst: perplexity:sonar-reasoning-pro -> openrouter:perplexity/sonar-reasoning-pro

Phase 1 verdict: OR's Perplexity path returns structured url_citation
annotations + inline URLs; the direct path strips URLs. Strictly better.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Flip the 2 Gemini-route role files + the default model

**Files:**
- Modify: `config/roles/resume_change_reviewer.md` — front-matter line 2
- Modify: `config/roles/network_analyst.md` — front-matter line 2
- Modify: `ops/aichat-ng/config.yaml.example` — top-level `model:` line (line 23)

The default-model line is captured here because its flip matches the other gemini-route flips and covers `onboarding_interviewer.md` (which has no `model:` front-matter and inherits the default).

- [ ] **Step 1: Flip `resume_change_reviewer.md`**

Change `model: gemini:gemini-3-flash-preview` → `model: openrouter:google/gemini-3-flash-preview`.

Resulting front-matter:

```yaml
---
model: openrouter:google/gemini-3-flash-preview
temperature: 0.1
---
```

- [ ] **Step 2: Flip `network_analyst.md`**

Change `model: gemini:gemini-3-flash-preview` → `model: openrouter:google/gemini-3-flash-preview`.

Resulting front-matter:

```yaml
---
model: openrouter:google/gemini-3-flash-preview
temperature: 0.2
---
```

- [ ] **Step 3: Flip the default model in `ops/aichat-ng/config.yaml.example`**

Locate the top-level `model:` line (near the top of the file, between the header comments and the `temperature: 0` line):

```yaml
model: gemini:gemini-3-flash-preview
```

Change to:

```yaml
model: openrouter:google/gemini-3-flash-preview
```

Leave the surrounding `temperature`, `rag_embedding_model`, and all other lines untouched. The `rag_embedding_model` line — `rag_embedding_model: gemini-embed:gemini-embedding-001` — must remain unchanged; embedding stays on the direct Google client.

- [ ] **Step 4: Verify**

Run:

```
grep -H "^model:" config/roles/resume_change_reviewer.md config/roles/network_analyst.md ops/aichat-ng/config.yaml.example
```

Expected (the three new strings):

```
config/roles/resume_change_reviewer.md:model: openrouter:google/gemini-3-flash-preview
config/roles/network_analyst.md:model: openrouter:google/gemini-3-flash-preview
ops/aichat-ng/config.yaml.example:model: openrouter:google/gemini-3-flash-preview
```

- [ ] **Step 5: Confirm embedding config did not move**

Run:

```
grep -n "rag_embedding_model" ops/aichat-ng/config.yaml.example
```

Expected: `rag_embedding_model: gemini-embed:gemini-embedding-001` unchanged.

- [ ] **Step 6: YAML parse check**

Run:

```
python3 -c "import yaml; yaml.safe_load(open('ops/aichat-ng/config.yaml.example'))" && echo "parse ok"
```

Expected: `parse ok`.

- [ ] **Step 7: Run tests**

Run:

```
uv run pytest tests/ -q
```

Expected: all tests pass.

- [ ] **Step 8: Commit**

```
git add config/roles/resume_change_reviewer.md config/roles/network_analyst.md ops/aichat-ng/config.yaml.example
git commit -m "$(cat <<'EOF'
feat(roles): flip Gemini-backed roles + default to OpenRouter (#250)

- resume_change_reviewer: gemini:gemini-3-flash-preview -> openrouter:google/gemini-3-flash-preview
- network_analyst: gemini:gemini-3-flash-preview -> openrouter:google/gemini-3-flash-preview
- default model in config.yaml.example: same flip
  (covers onboarding_interviewer.md, which inherits the default)

Embedding (rag_embedding_model) stays on the direct gemini-embed client.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Update CLAUDE.md Pipeline Context Table

**Files:**
- Modify: `CLAUDE.md` — Pipeline Context Table rows under `## Pipeline Context Table`, starting line 74.

- [ ] **Step 1: Update the table rows**

Replace each affected row with its Phase 2 target. Keep all non-affected rows unchanged.

Old rows (lines 75, 77–82):

```markdown
| Default model | `gemini:gemini-3-flash-preview` |
| `resume_tailor` / `cover_letter_writer` | `claude:claude-opus-4-6:thinking`, `max_tokens: 4096` |
| `company_researcher` | `perplexity:sonar-reasoning-pro` |
| `briefing_writer` | `claude:claude-sonnet-4-6:thinking` |
| `outreach_drafter` | `claude:claude-sonnet-4-6` — profile injected directly |
| `fit_analyst` | `perplexity:sonar-reasoning-pro` — appended to company briefing |
| `resume_change_reviewer` / `network_analyst` | `gemini:gemini-3-flash-preview` |
```

New rows:

```markdown
| Default model | `openrouter:google/gemini-3-flash-preview` |
| `resume_tailor` / `cover_letter_writer` | `openrouter:anthropic/claude-opus-4.7`, `max_tokens: 4096` |
| `company_researcher` | `openrouter:perplexity/sonar-reasoning-pro` |
| `briefing_writer` | `openrouter:anthropic/claude-sonnet-4.6` |
| `outreach_drafter` | `openrouter:anthropic/claude-sonnet-4.6` — profile injected directly |
| `fit_analyst` | `openrouter:perplexity/sonar-reasoning-pro` — appended to company briefing |
| `resume_change_reviewer` / `network_analyst` | `openrouter:google/gemini-3-flash-preview` |
```

Do not change: `Embedding model`, `job_scorer`, `Job ingestion`, or any row below `resume_change_reviewer`.

- [ ] **Step 2: Verify the updates**

Run:

```
grep -n "^|" CLAUDE.md | sed -n '1,15p'
```

Expected: every updated row shows `openrouter:*`; embedding row still shows `gemini-embed:gemini-embedding-001`; scorer row still shows `openrouter:deepseek/deepseek-v3.2`.

- [ ] **Step 3: Self-check for stale claude/perplexity/gemini route mentions**

Run:

```
grep -n "claude:claude-opus-4-6\|claude:claude-sonnet-4-6\|perplexity:sonar-reasoning-pro\|gemini:gemini-3-flash-preview" CLAUDE.md
```

Expected: no matches. The only `gemini:` mention left should be `gemini-embed:gemini-embedding-001` on the embedding row.

- [ ] **Step 4: Run tests**

Run:

```
uv run pytest tests/ -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```
git add CLAUDE.md
git commit -m "$(cat <<'EOF'
docs(CLAUDE.md): update Pipeline Context Table for Phase 2 routes (#250)

Reflects the 9 role flips to openrouter:* + Opus 4.7 for resume/CL.
Embedding row unchanged (stays on direct gemini-embed client).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Add CHANGELOG [Unreleased] entry

**Files:**
- Modify: `CHANGELOG.md` — insert subsections under the `## [Unreleased]` line (currently empty, around line 11).

- [ ] **Step 1: Insert the entry**

Under the `## [Unreleased]` heading, insert:

```markdown
## [Unreleased]

### Changed

- **OpenRouter Phase 2 cutover (#250).** Ten of eleven pipeline roles now route via OpenRouter as a single gateway: `resume_tailor` and `cover_letter_writer` upgraded to **Opus 4.7** (same pricing as 4.6 per OR catalog, small-to-moderate quality edge on real-pipeline prompts per Phase 1 verdict #22); `briefing_writer` and `outreach_drafter` to `openrouter:anthropic/claude-sonnet-4.6`; `company_researcher` and `fit_analyst` to `openrouter:perplexity/sonar-reasoning-pro` (OR's Perplexity path returns structured URL citations, direct path strips them); `resume_change_reviewer`, `network_analyst`, and the default model to `openrouter:google/gemini-3-flash-preview`. Embedding (`gemini-embed:gemini-embedding-001`) stays on the direct Google client — OR has zero embedding endpoints. `job_scorer` unchanged (already on OR).

### Migration required

- **Edit `state/aichat_ng/config.yaml` on each deployed stack** to change the top-level `model:` line from `gemini:gemini-3-flash-preview` to `openrouter:google/gemini-3-flash-preview`. The image's `ops/aichat-ng/config.yaml.example` template seeds this file only on first install; existing installs keep their pre-upgrade default otherwise.
- **Diff `state/aichat_ng/models-override.yaml` against `ops/aichat-ng/models-override.yaml` in this release** and append the two new openrouter catalog entries if absent: `anthropic/claude-opus-4.7` and `google/gemini-3-flash-preview`. Without these, the role files will reference models aichat-ng does not know about.
- **Ensure `OPENROUTER_API_KEY` is set** in `state/data/.env` (or equivalent). Ten of eleven roles now depend on it.
```

Place this block immediately after `## [Unreleased]` and immediately before `## [0.3.3] — 2026-04-24`.

- [ ] **Step 2: Verify structure**

Run:

```
sed -n '11,35p' CHANGELOG.md
```

Expected: `## [Unreleased]` heading followed by `### Changed` and `### Migration required` subsections, followed by `## [0.3.3] — 2026-04-24`.

- [ ] **Step 3: Run tests**

Run:

```
uv run pytest tests/ -q
```

Expected: all tests pass.

- [ ] **Step 4: Commit**

```
git add CHANGELOG.md
git commit -m "$(cat <<'EOF'
docs(changelog): record OpenRouter Phase 2 cutover under [Unreleased] (#250)

Includes the Migration required subsection so the release-notes workflow
surfaces the two bind-mount edits required on every deployed stack.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: Add OR-key rotation subsection to docs/setup/configure.md

**Files:**
- Modify: `docs/setup/configure.md` — append a new subsection at the end covering how to rotate `OPENROUTER_API_KEY` on a deployed stack.

- [ ] **Step 1: Locate insertion point**

Run:

```
tail -5 docs/setup/configure.md
```

Read the last lines. The insertion point is the end of the file, as a new `##`-level subsection.

- [ ] **Step 2: Append the subsection**

Append to `docs/setup/configure.md`:

```markdown

## Rotating API keys on a deployed stack

With Phase 2 of the OpenRouter cutover, 10 of 11 roles depend on
`OPENROUTER_API_KEY`. Rotating it cleanly on a running stack:

1. Generate a new key in the OpenRouter dashboard and note both the
   old and new values.
2. Edit your stack's env file (`/opt/stacks/findajob-<you>/state/data/.env`
   or wherever you keep credentials — check your compose file's
   `env_file:` directive) and replace the `OPENROUTER_API_KEY=…` line.
3. Recreate the container so aichat-ng picks up the new value:
   `docker compose up -d --force-recreate` from the stack directory.
4. Verify with a smoke call: `docker compose exec scheduler aichat-ng --model openrouter:google/gemini-3-flash-preview "say hello"`.
   If the call succeeds, revoke the old key in the OpenRouter dashboard.

`GOOGLE_API_KEY` remains live after Phase 2 — it still powers the
Gemini embedding client (`gemini-embed:gemini-embedding-001`) that
the RAG index uses. Rotate it the same way. `ANTHROPIC_API_KEY` and
`PERPLEXITY_API_KEY` are still declared in the aichat-ng config but
no live role routes to them after the cutover; they are retirement
candidates rather than fallbacks. Keep rotations staggered — don't
revoke the old key until the new one has served at least one live
pipeline run without error.
```

- [ ] **Step 3: Verify**

Run:

```
tail -20 docs/setup/configure.md
```

Expected: the new `## Rotating API keys on a deployed stack` subsection at the bottom.

- [ ] **Step 4: Run tests**

Run:

```
uv run pytest tests/ -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```
git add docs/setup/configure.md
git commit -m "$(cat <<'EOF'
docs(setup): add OR-key rotation subsection to configure.md (#250)

Phase 2 puts 10/11 roles behind OPENROUTER_API_KEY; operators need a
known-good rotation procedure. Same pattern applies to the other keys
that remain (Anthropic, Google, Perplexity).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: Parallel eval gate on the operator stack (pre-merge)

**Files:**
- Ephemeral on docker.lan: shadow role files `/tmp/phase2-eval/resume_tailor_phase2.md` etc. (cleaned up after)
- No repo changes

The eval is the **gate** before opening the PR. If any of the 4 quality-critical roles regresses, return to the relevant task (3 for Claude routes) and back out the affected role's flip on this branch before proceeding.

- [ ] **Step 1: Verify stack pins and operator availability**

Run:

```
ssh docker.lan 'grep FINDAJOB_IMAGE_TAG /opt/stacks/findajob-*/.env'
```

Expected: operator stack on `:latest`; beta stack(s) on a `:vMAJOR.MINOR` alias. Note values for later.

- [ ] **Step 2: Identify eligible jobs (prepped in last 3–4 days)**

Run:

```
ssh docker.lan "sudo -u lad find /opt/stacks/findajob-<operator>/state/companies /opt/stacks/findajob-<operator>/state/companies/_applied -maxdepth 1 -type d -mtime -4 -printf '%T@ %p\n' | sort -n | tail -20"
```

Expected: 5–10 or more directory paths. Pick 5 distinct jobs spanning different companies for variety. Note their folder paths.

If fewer than 5 recent jobs exist, either (a) wait for the next daily triage + a few prep runs to build up eligible candidates, or (b) relax to within the last 7 days — the 3–4 day cutoff matters to avoid role-drift confounds; it's tighter than strictly necessary.

- [ ] **Step 3: Create shadow role files on docker.lan**

For each of the 4 quality-critical roles, create a `*_phase2.md` shadow on docker.lan. From the local laptop:

```
mkdir -p /tmp/phase2-shadow-roles
cp config/roles/resume_tailor.md /tmp/phase2-shadow-roles/resume_tailor_phase2.md
cp config/roles/cover_letter_writer.md /tmp/phase2-shadow-roles/cover_letter_writer_phase2.md
cp config/roles/briefing_writer.md /tmp/phase2-shadow-roles/briefing_writer_phase2.md
cp config/roles/outreach_drafter.md /tmp/phase2-shadow-roles/outreach_drafter_phase2.md
```

The local copies already have the Phase 2 `model:` lines from Task 3. Copy them onto the operator stack:

```
scp -q /tmp/phase2-shadow-roles/*.md docker.lan:/tmp/phase2-shadow-roles/
ssh docker.lan "sudo -u lad mkdir -p /opt/stacks/findajob-<operator>/state/phase2-eval && sudo -u lad cp /tmp/phase2-shadow-roles/*.md /opt/stacks/findajob-<operator>/state/phase2-eval/"
```

(Use `-q` to avoid scp progress bar breakage.)

- [ ] **Step 4: Pick the first job and invoke the shadow `resume_tailor`**

Pick the first of the 5 selected jobs (call its folder `$JOB1`). Inside that folder, the file `job.txt` (or equivalent per the prep convention) has the JD, and `resume.md` has the original resume output.

Run, substituting the job folder path:

```
ssh docker.lan "sudo -u lad docker exec -i findajob-<operator>-scheduler-1 aichat-ng \
  --role /app/state/phase2-eval/resume_tailor_phase2 \
  --file /app/companies/<JOB1>/job.txt \
  --file /app/candidate_context/master_resume.md \
  --file /app/candidate_context/profile.md \
  'Tailor the resume for this job.' > /tmp/phase2-shadow-roles/<JOB1>_resume_phase2.md"
```

(Exact flag names for aichat-ng may differ slightly; if `--role` + `--file` doesn't match the actual CLI, check `docker exec … aichat-ng --help` and adjust. The intent is: load the shadow role, load the same JD + master_resume + profile the original prep used, capture output.)

- [ ] **Step 5: Diff Phase 2 output against the original**

Pull the original resume and the new one to the laptop and diff in your editor. Example:

```
scp -q docker.lan:/opt/stacks/findajob-<operator>/state/companies/<JOB1>/resume.md /tmp/phase2-shadow-roles/<JOB1>_resume_original.md
scp -q docker.lan:/tmp/phase2-shadow-roles/<JOB1>_resume_phase2.md /tmp/phase2-shadow-roles/
diff -u /tmp/phase2-shadow-roles/<JOB1>_resume_original.md /tmp/phase2-shadow-roles/<JOB1>_resume_phase2.md | less
```

Hand-evaluate:
- FORMAT LAW: role blocks look like `### Employer · Title` then `City, State | Start – End` then bullets. No deviation.
- Bullets pull from master resume content, tightened — nothing invented.
- Targeting feels at least as crisp as the original toward the JD's language.

Record a one-line verdict per job: `parity`, `better`, or `regression: <reason>`.

- [ ] **Step 6: Repeat Step 4–5 for the remaining 4 jobs + for the other 3 quality-critical roles**

Total: 5 jobs × 4 roles = 20 runs. Budget ~45 min of human review + ~$1–2 in API spend.

For `cover_letter_writer`, invoke the `_phase2` variant with the same inputs plus the JD; diff against `cover_letter.md` in the job folder.

For `briefing_writer`, invoke with the JD + company name; diff against `briefing.md`.

For `outreach_drafter`, invoke with the JD + profile + (if present) a target LinkedIn contact summary; diff against `outreach.md`.

- [ ] **Step 7: Apply the decision rule**

For each role, tally the 5 verdicts. Decision rule:

- **Pass:** ≥4/5 jobs show `parity` or `better`, and no `regression` with a label worse than "minor stylistic".
- **Fail:** Any job with a hard regression (FORMAT LAW violation, hallucinated content, voice mismatch worse than current, or factual errors the original didn't have), or ≥2/5 jobs with any-level regression.

If **Fail** on any role: return to the task that committed that role (Task 3 for `resume_tailor` / `cover_letter_writer` / `briefing_writer` / `outreach_drafter`; Task 4 for `company_researcher` / `fit_analyst`; Task 5 for `resume_change_reviewer` / `network_analyst`) and back out the failing role's flip with a new commit that edits the affected role file's front-matter back to its pre-Phase-2 `model:` value (see the table in the spec §Scope for exact originals). Do **not** amend the original provider commit — leave the flipped history intact and let the revert show as a separate commit. Append a **"Regressions excluded from cutover"** section to the spec at `docs/superpowers/specs/2026-04-24-openrouter-phase-2-cutover-design.md` describing which role was held back and the observed regression pattern. Update the Task 7 CHANGELOG entry with an additional commit calling out the excluded role in the Migration-required section.

- [ ] **Step 8: Clean up shadow role files**

Regardless of pass/fail, clean up the ephemeral files on docker.lan and the laptop:

```
ssh docker.lan "sudo -u lad rm -rf /opt/stacks/findajob-<operator>/state/phase2-eval /tmp/phase2-shadow-roles"
rm -rf /tmp/phase2-shadow-roles
```

Shadow role files are eval-only; they should never enter production role lookup paths.

- [ ] **Step 9: Record eval verdict in an issue comment on #250**

Run:

```
gh issue comment 250 --repo brockamer/findajob --body "$(cat <<'EOF'
## Session YYYY-MM-DD — Parallel eval verdict

**Method:** shadow role files on the operator stack, 5 jobs prepped in the last 3-4 days, hand-diff vs original outputs.

**Per-role tally:**
- resume_tailor: <N>/5 parity-or-better (verdicts: <jobX: parity>, <jobY: better>, ...)
- cover_letter_writer: <N>/5 parity-or-better (...)
- briefing_writer: <N>/5 parity-or-better (...)
- outreach_drafter: <N>/5 parity-or-better (...)

**Decision:** <PASS / FAIL on role(s): ...>

**Proceeding with:** <all 4 flipped / 3 flipped, <role> deferred>

Cost: ~$<N> API spend. Time: ~<N> min review.
EOF
)"
```

Substitute the actual verdicts and date. This note becomes the Session log on the issue.

---

## Task 10: Open PR with migration-required label

**Files:**
- None (git + GitHub only)

- [ ] **Step 1: Push the branch**

Run:

```
git push -u origin feat/250-openrouter-phase-2-cutover
```

Expected: branch created on origin, tracking set.

- [ ] **Step 2: Open PR**

Run:

```
gh pr create \
  --title "OpenRouter Phase 2 cutover — 10 roles + Opus 4.7 (#250)" \
  --body "$(cat <<'EOF'
Closes #250. Parent #240.

## Summary

- Flips 10 of 11 pipeline roles to `openrouter:*` routes. Embedding stays direct.
- Upgrades `resume_tailor` and `cover_letter_writer` from Opus 4.6 to **4.7** (same pricing, small-to-moderate quality edge per Phase 1 verdict #22).
- Adds the two missing openrouter catalog entries (`anthropic/claude-opus-4.7`, `google/gemini-3-flash-preview`).
- Pure config — no code, no schema, no scheduler changes.

## Spec / plan

- Spec: `docs/superpowers/specs/2026-04-24-openrouter-phase-2-cutover-design.md`
- Plan: `docs/superpowers/plans/2026-04-24-openrouter-phase-2-cutover.md`

## Parallel eval gate

Pre-merge shadow-role eval passed on <N> jobs across 4 quality-critical roles (see the session comment on #250).

## Migration required

- Edit `state/aichat_ng/config.yaml` top-level `model:` on each deployed stack.
- Diff + append to `state/aichat_ng/models-override.yaml`.
- Ensure `OPENROUTER_API_KEY` is set.

Full migration text is in the CHANGELOG [Unreleased] entry.

## Test plan

- [ ] Existing `uv run pytest tests/ -q` passes locally (ran after each commit)
- [ ] CI pipeline green (ruff, mypy, pytest)
- [ ] Eval gate passed pre-merge (session comment on #250)
- [ ] Post-merge: operator stack runs one triage + one prep cleanly, no new log_event error classes in 24h
- [ ] Post-merge: pin-advanced beta-tester stack(s) verify same

EOF
)" \
  --label "migration-required,enhancement,pipeline-quality"
```

Expected: PR URL returned. Record it — subsequent tasks reference it.

- [ ] **Step 3: Verify CI is running and the label is applied**

Run:

```
gh pr view --json number,title,labels,statusCheckRollup --jq '{number, title, labels: [.labels[].name], checks: [.statusCheckRollup[].name]}'
```

Expected: `migration-required` is in the labels array; checks include `ci` (or whichever names `.github/workflows/ci.yml` produces) in `PENDING` or `IN_PROGRESS` state.

- [ ] **Step 4: Wait for CI to pass**

Monitor:

```
gh pr checks --watch
```

Expected: all checks green. If a check fails, read the log, fix locally, push, wait again. Do not merge until green.

---

## Task 11: Merge PR, deploy to operator stack

**Files:**
- None (runbook)

Do this only after CI is green and the user says go.

- [ ] **Step 1: Merge the PR**

Run:

```
gh pr merge --squash --delete-branch
```

Expected: PR is merged into `main` (squash), feature branch is deleted both locally and on origin.

- [ ] **Step 2: Deploy to operator stack — image pull**

Run:

```
ssh docker.lan 'cd /opt/stacks/findajob-<operator> && docker compose pull && docker compose up -d'
```

Expected: image pull succeeds; containers recreate. The new `config/roles/*.md` files (baked into the image) are now live.

- [ ] **Step 3: Edit the operator stack's `state/aichat_ng/config.yaml`**

Run:

```
ssh docker.lan "sudo -u lad sed -i 's|^model: gemini:gemini-3-flash-preview$|model: openrouter:google/gemini-3-flash-preview|' /opt/stacks/findajob-<operator>/state/aichat_ng/config.yaml"
```

Expected: one line changed. Verify:

```
ssh docker.lan "grep '^model:' /opt/stacks/findajob-<operator>/state/aichat_ng/config.yaml"
```

Expected:

```
model: openrouter:google/gemini-3-flash-preview
```

If the sed didn't match (output is still the old value), the file may have been customized post-seed — edit it by hand via `ssh docker.lan 'sudo -u lad vi …'` or equivalent.

- [ ] **Step 4: Append the two missing catalog entries to `state/aichat_ng/models-override.yaml`**

Diff the live file against the repo's version:

```
ssh docker.lan "sudo -u lad grep -c 'anthropic/claude-opus-4.7\|google/gemini-3-flash-preview' /opt/stacks/findajob-<operator>/state/aichat_ng/models-override.yaml"
```

Expected: `2` if both entries already present (entrypoint may re-seed on some paths), `0` or `1` otherwise. If not 2, hand-edit the file to insert the two entries in the same positions as Task 2 used in the repo.

- [ ] **Step 5: Restart aichat-ng consumers**

Run:

```
ssh docker.lan 'cd /opt/stacks/findajob-<operator> && docker compose restart scheduler'
```

Expected: container restarts cleanly; supercronic re-reads its env.

- [ ] **Step 6: Smoke-test one aichat-ng call**

Run:

```
ssh docker.lan "sudo -u lad docker exec findajob-<operator>-scheduler-1 aichat-ng --model openrouter:anthropic/claude-opus-4.7 'Say the word stronghold.'"
```

Expected: any non-empty response from Opus 4.7 containing "stronghold". If it errors with `model not found`, the catalog entry isn't being loaded — re-check Task 11 Step 4.

---

## Task 12: Observe operator stack for 24h

**Files:**
- None (runbook)

- [ ] **Step 1: Verify the next daily triage completes cleanly**

The triage timer runs at 00:00 PT. After it has run, check:

```
ssh docker.lan "sudo -u lad sqlite3 /opt/stacks/findajob-<operator>/state/data/pipeline.db 'SELECT ts, event, severity FROM pipeline_events WHERE ts > datetime(\"now\", \"-1 day\") AND severity IN (\"error\", \"warn\") ORDER BY ts DESC LIMIT 40;'"
```

Expected: no new error classes relative to the baseline (compare against the previous day's runs). Warnings that pre-existed before this cutover are fine.

Note: all times in the DB are UTC. "Yesterday's triage" from PT perspective is roughly 07:00 UTC the previous day onward.

- [ ] **Step 2: Run one on-demand prep**

Pick a fresh scored job on the Dashboard with score ≥ 7. Flag it for prep via the web UI at `/board/dashboard`. Wait for it to complete (watch `stage` move `scored → prep_in_progress → materials_drafted`).

- [ ] **Step 3: Inspect prep output**

Visit the prep folder on the operator stack and review:
- `resume.md` — FORMAT LAW compliance, appropriate bullet pulls from master resume
- `cover_letter.md` — tone matches voice samples, no hallucinated claims
- `briefing.md` — useful, cites sources
- `outreach.md` — authentic voice

If any look worse than baseline: **rollback** — see Rollback section of the spec. Otherwise: proceed.

- [ ] **Step 4: Post verification note to #250**

Run:

```
gh issue comment 250 --repo brockamer/findajob --body "$(cat <<'EOF'
## Session YYYY-MM-DD — Operator stack post-deploy verification

- Daily triage <YYYY-MM-DD UTC>: completed, no new error classes in `pipeline_events`.
- On-demand prep run on <job-fingerprint>: materials look correct (resume FORMAT LAW holds, CL voice matches, briefing has sources, outreach tone authentic).

Operator stack verified. Beta-tester stack advancement pending next release tag.
EOF
)"
```

---

## Task 13: Advance beta-tester stack pin (after next release tag)

**Files:**
- Beta-tester stack's `.env` on docker.lan (pin advancement)
- Beta stack's `state/aichat_ng/config.yaml` (default model edit)
- Beta stack's `state/aichat_ng/models-override.yaml` (catalog additions)

Do this only after:
1. Task 12 24h observation passed on the operator stack.
2. A new release tag has been cut (the Phase 2 cutover is on `main`; beta stacks pin to `:vMAJOR.MINOR` and will not pick up the role files until a new minor is tagged). Follow `docs/release-process.md` for the tag cut.

- [ ] **Step 1: Confirm the new release tag exists and has been built**

Run:

```
gh release list --limit 5
```

Expected: a new release after `v0.3.3` (current latest). Phase 2 is a material feature addition; convention is minor bump (e.g., `v0.4.0`). Confirm with the user which tag they cut.

- [ ] **Step 2: Advance the beta pin**

Substituting the actual new minor for `<NEW-MINOR>`:

```
ssh docker.lan "sudo -u lad sed -i 's|^FINDAJOB_IMAGE_TAG=v0\\.[0-9]\\+$|FINDAJOB_IMAGE_TAG=v<NEW-MINOR>|' /opt/stacks/findajob-<beta>/.env"
ssh docker.lan "grep FINDAJOB_IMAGE_TAG /opt/stacks/findajob-<beta>/.env"
```

Expected: tag now shows `v<NEW-MINOR>`.

- [ ] **Step 3: Pull the new image and restart**

```
ssh docker.lan 'cd /opt/stacks/findajob-<beta> && docker compose pull && docker compose up -d'
```

Expected: image pull completes; containers recreate with the new image.

- [ ] **Step 4: Apply the same bind-mount edits as on the operator stack**

Repeat Task 11 Steps 3–5 on the beta stack (substitute `findajob-<beta>` for `findajob-<operator>`).

- [ ] **Step 5: Smoke-test**

Repeat Task 11 Step 6 against the beta stack.

- [ ] **Step 6: Notify the beta tester via the existing beta channel**

Tell Alice Doe (the current beta tester) that the cutover has landed on her stack: 10 of 11 roles now run through OpenRouter, resume/cover-letter quality should match or exceed previous output, and if she notices anything off in the next few preps please report in the usual channel.

---

## Task 14: Observe beta stack for 24h + close out

**Files:**
- None (runbook)

- [ ] **Step 1: Verify beta stack's next triage and any prep runs**

Repeat Task 12 Steps 1–3 on the beta stack.

- [ ] **Step 2: Close #250**

Run:

```
gh issue comment 250 --repo brockamer/findajob --body "$(cat <<'EOF'
## Session YYYY-MM-DD — Phase 2 complete

Both stacks verified post-deploy:
- Operator stack: <date of Task 12 verification>
- Beta-tester stack: <date of this verification>

All acceptance criteria met. Closing.
EOF
)"

/home/brockamer/Code/jared/skills/jared/scripts/jared close 250
```

Expected: issue closes; project board auto-moves to Done.

- [ ] **Step 3: Archive the plan**

Move this plan to the archived subfolder:

```
git checkout -b chore/archive-250-plan origin/main
mkdir -p docs/superpowers/plans/archived/2026-04
git mv docs/superpowers/plans/2026-04-24-openrouter-phase-2-cutover.md docs/superpowers/plans/archived/2026-04/
git commit -m "docs(plans): archive 2026-04-24 OpenRouter Phase 2 cutover plan (#250 closed)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
git push -u origin chore/archive-250-plan
gh pr create --title "docs(plans): archive OpenRouter Phase 2 plan (#250)" --body "Archive-only; no behavior change." --label "documentation"
```

Merge the archival PR without ceremony once CI passes.

- [ ] **Step 4: Announce follow-ups**

If the eval surfaced any quality concerns that didn't block merge but are worth a follow-up PR (e.g., "the `briefing_writer` output was parity but lost the structured source citations that the direct perplexity path sometimes produced"), file as a new issue under the #240 epic.

Also: file the reasoning body patch follow-up if reasoning-trace visibility is desired:

```
/home/brockamer/Code/jared/skills/jared/scripts/jared file "Aichat-ng reasoning body patch for openrouter client" \
  --body "Proposed body patch in spec 2026-04-24-openrouter-phase-2-cutover-design.md §Deferred. Adds visible reasoning traces for Claude routes via OR; does not affect output quality. Test whether aichat-ng 0.31.0 applies patch.chat_completions to openai-compatible clients." \
  --priority Low \
  --labels enhancement,pipeline-quality
```

Confirm the jared CLI argument order matches your local jared.

---

## Documentation Impact

- `CLAUDE.md` Pipeline Context Table — 7 row updates (Task 6).
- `CHANGELOG.md` `[Unreleased]` entry with migration subsection (Task 7).
- `ops/aichat-ng/config.yaml.example` — default `model:` line (Task 5).
- `ops/aichat-ng/models-override.yaml` — two catalog entries (Task 2).
- `docs/setup/configure.md` — OR-key rotation subsection (Task 8).
- `docs/superpowers/specs/2026-04-24-openrouter-phase-2-cutover-design.md` — spec (already committed in `3078e1a`).
- `docs/superpowers/plans/2026-04-24-openrouter-phase-2-cutover.md` — this plan (Task 14 archives it post-completion).
- No changes to `docs/usage.md`, `docs/troubleshooting.md`, `docs/release-process.md`, `README.md`, `docs/project-board.md`.

---

## Whole-feature verification gate

Before declaring the plan complete (Task 14 Step 2), confirm all of the following:

- [ ] CI green on the merged PR (per Task 10 Step 4)
- [ ] Operator stack: one full triage + one prep completed cleanly post-deploy (Task 12)
- [ ] Operator stack: `pipeline_events` shows no new error classes in the 24h post-deploy window (Task 12 Step 1)
- [ ] Beta-tester stack: pin advanced to the new minor, bind-mount edits applied, 24h observation passed (Tasks 13 + 14)
- [ ] `CHANGELOG.md` `[Unreleased]` entry is correct and will render cleanly when promoted to the next release
- [ ] Parallel eval verdict comment is on #250
- [ ] Post-deploy verification comments are on #250 for both stacks
- [ ] No "Regressions excluded from cutover" addendum — or, if one was appended to the spec, the CHANGELOG Migration-required section mirrors the exclusion and a follow-up issue is filed

If any check fails, do **not** close #250; work down the failure and re-check.

---

## Self-review checklist — spec → plan mapping

- Spec §Goal → Tasks 2–5 (catalog + routes) and Tasks 11+ (deploy).
- Spec §Scope (model string flips) → Tasks 3, 4, 5 (role front-matters by provider).
- Spec §Scope (catalog additions) → Task 2.
- Spec §Parallel eval gate → Task 9.
- Spec §Files changed → Tasks 2–8 (all tracked files covered).
- Spec §Deploy plan → Tasks 11–13 (runbook).
- Spec §Rollback → referenced in Task 9 (pre-merge) and Task 12 Step 3 (post-deploy).
- Spec §Success criteria → whole-feature verification gate above.
- Spec §Deferred (reasoning patch) → Task 14 Step 4 (follow-up issue filing).
- Spec §Documentation impact → Documentation Impact section above + Task 14 (plan archival).
