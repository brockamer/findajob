# Dynamic Company Discoverer — Design Spec

**Date:** 2026-04-26
**Issue:** #284 (`feat(scorer): dynamic competency-driven company discovery — replace static Tier 1 list with reasoned, regenerable set`)
**Status:** Design approved via brainstorming session; ready for implementation-plan pass
**Related work:** #276 (scorer role-shape, blocked on this), #285 (scorer rewire — drops `TIER 1 EXCEPTION`, consumes the discoverer output), #283 (onboarding-driven search-config derivation; Section B blocked on this), #228 (data-driven tuning loop), #150 (future guided tuning UI)

---

## 1. Context

The current scorer leans on a static `## Target Companies / Organizations` section in `candidate_context/profile.md`. That section is hand-curated by the candidate at onboarding and rarely refreshed. It powers the `TIER 1 COMPANY EXCEPTION` block in `config/roles/job_scorer.md`: any in-domain title at a named company gets a floor of 6 regardless of JD content.

This abstraction is breaking under pressure. The hand-curated list is too narrow to cover companies where the candidate's competency stack would transfer well, and too coarse — it conflates two orthogonal signals:

1. **Strategic preference** — companies the candidate would take a job at even if the role isn't a perfect fit.
2. **Competency-domain fit** — does the candidate's competency stack match what the role needs.

Smashing both signals into one knob ("on the list AND in-domain → ≥6") produces both false positives (preference-listed companies whose specific roles are not a competency match) and false negatives (competency-perfect roles at companies the candidate never thought to add to the list).

Hand-expanding the list as a fix is a known dead end. A prior attempt to expand the list to cover an adjacent industry never converged — hand-written lists don't track hiring activity, don't pressure-test transferable-competency fit, and don't scale across operator fields. The list is also a generalization fragility: every operator's static list lives in the same code path the scorer reads, and the scorer's `TIER 1 EXCEPTION` is currently the load-bearing override for everything else.

The fix is to introduce a **reasoned, regenerable, field-agnostic discovered set** as a parallel signal — augmenting (not replacing) the static list. The static list stays as a strategic-preference signal; the discovered set carries the competency-fit signal. Downstream consumers (#285's scorer rewire, #283's Greenhouse-slug derivation) read both as inputs without either acting as a hard floor.

## 2. Objectives

| Role | Metric | Treatment |
|---|---|---|
| **Primary objective** | Operator's applies-from-discovered-companies in 30 days post-deploy | ≥1 application to a company appearing only on the discovered list (not in the static `## Target Companies`) |
| **Generalization gate** | Same role prompt produces sensibly different outputs | Eyeballed PR-time smoke against the operator's real profile; expected output reads as field-appropriate |
| **Cost ceiling (soft)** | Per-run reported cost on `openrouter:perplexity/sonar-reasoning-pro` | ntfy warning if any single run reports >$10; does not block run. **Empirical (smoke #5):** ~$0.10/run, far under threshold; envelope (~$260/year/stack) is overstated by ~50× and can be revisited in a follow-up. |
| **Failure semantics** | Network/parse/garbage failure | Last-good output preserved; ntfy alert sent; cron exit non-zero |
| **Onboarding latency** | Time added to fresh-stack onboarding completion | <60s budgeted for the post-injection LLM call; soft-fail if exceeded |
| **Atomicity** | Disk-state during failed write | Either both `.md` and `.json` sidecar update, or neither; previous good output preserved |

The primary objective is the headline. The generalization gate replaces traditional snapshot/lint testing with a one-time human-eyeball review at PR time, then trusts code review for upstream contributions (per Q4 brainstorm reflection — no permanent fixture, no permanent CI gate).

## 3. Scope

### 3.1 In scope

- A new role file `config/roles/company_discoverer.md` with `model: openrouter:perplexity/sonar-reasoning-pro`, field-agnostic prompt body that reads the candidate's profile and emits clustered, cited markdown.
- A new library module `src/findajob/discoverer/` with `prompt.py`, `parser.py`, `runner.py`, `writer.py` — separable units, fully unit-testable.
- A new entry-point script `scripts/discover_companies.py` — thin CLI wrapper around `findajob.discoverer.run()`.
- A weekly cron entry in `ops/crontab` (Sunday 02:00 container-local TZ).
- A post-commit hook in `findajob.onboarding.injector` that calls `findajob.discoverer.run()` after the seven-file atomic commit + sentinel.
- The output file pair `candidate_context/discovered_companies.md` (human-readable) + `candidate_context/discovered_companies.json` (machine-readable sidecar).
- An entry in `findajob.web.config_files.EDITABLE_CATEGORIES` so the operator can hand-edit `discovered_companies.md` from `/config/`.
- A "Generating initial discovery (~30s)..." progress note in the onboarding completion UI.
- Cost soft-guardrail: ntfy warning when reported per-run cost exceeds the configured threshold.
- Documentation updates per §11.

### 3.2 Out of scope

- **Replacing the static `## Target Companies` section.** It stays in `profile.md` as a strategic-preference signal, orthogonal to the discovered competency-fit signal. (#285 will rewire how the scorer reads both, separately.)
- **Per-role discovery** (e.g., narrowing on a specific job title within a competency cluster). Cluster-by-competency-adjacency is the right altitude for v1.
- **Hiring-activity scraping beyond what `sonar-reasoning-pro`'s web search returns.** No LinkedIn API, no proprietary data feeds.
- **Permanent CI gates for field-agnosticism.** No grep-lint on the role file for forbidden tokens, no committed Alice fixture, no snapshot-diff test. The only verification is the one-time PR-time real-API smoke against the operator's profile, plus trust in code review for contributions.
- **A `/tools/` re-trigger button.** Operators can run `python3 scripts/discover_companies.py` manually if desired; a UI button is #150-tuning territory.
- **Quarterly Deep Research mode** (`openrouter:perplexity/sonar-deep-research`). Mentioned in the issue body but deferred — only Reasoning Pro on the weekly cron is implemented in v1; Deep Research can be added later as an opt-in flag without touching the core pipeline.

## 4. Architecture

```
src/findajob/discoverer/
├── __init__.py           # re-exports run() from runner
├── prompt.py             # build_prompt(profile_text) -> str  (pure)
├── parser.py             # parse_markdown(md_text) -> ParseResult  (pure)
├── runner.py             # run(base_root, profile_path) -> RunResult  (orchestration)
└── writer.py             # commit_atomically(base_root, md, json) -> Path  (atomic temp+replace)

scripts/discover_companies.py        # 20-line CLI: argparse + findajob.discoverer.run()
config/roles/company_discoverer.md   # role file, sonar-reasoning-pro
src/findajob/onboarding/injector.py  # add post-commit hook calling discoverer.run()
src/findajob/web/config_files.py     # add candidate_context/discovered_companies.md to allowlist
ops/crontab                          # weekly entry
```

### 4.1 Component contracts

**`prompt.py`** — pure prompt builder.

- Input: profile text (the contents of `candidate_context/profile.md`).
- Output: a single string suitable to pass to `aichat-ng -S <prompt>` (the role file's frontmatter sets the model and temperature).
- Constraint: emits no operator-specific identifiers, no field-locked vocabulary; reads the profile sections and references them by *section name*, not by paraphrasing their content into the prompt body. Sections referenced: `## Core Competencies`, `## Career Summary`, `## Target Roles` (or `## Target Role`), `## Target Companies / Organizations` (as the seed).
- ~50 lines, no I/O, fully unit-testable.

**`parser.py`** — pure markdown→structured converter.

- Input: raw markdown returned by the LLM (after `<think>` blocks are stripped by the runner).
- Output: a `ParseResult(markdown_clean: str, companies: list[CompanyEntry])` where `CompanyEntry` is a frozen dataclass with fields `name`, `cluster`, `channel`, `reasoning`, `citations`.
- Validation rules:
  - At least 3 companies total
  - At least 2 of the three clusters present (`direct`, `adjacency`, `cross_industry`)
  - Every entry has non-empty `name`, `cluster` ∈ {direct, adjacency, cross_industry}, `channel` ∈ {greenhouse, ashby, lever, workday, in_house, unknown}, `reasoning` non-empty
  - Footer `## References` `[N]` indices resolve to URL strings; per-row inline `[N]` markers are translated to per-row URL lists in the JSON output
- Failure mode: raises `DiscoveryParseError` with a descriptive message naming the validation gate that failed.
- ~80 lines, fully unit-testable with golden markdown fixtures.

**`runner.py`** — orchestration.

- Public entry: `run(base_root: Path, profile_path: Path | None = None, ntfy_enabled: bool = True) -> RunResult`. If `profile_path` is `None`, defaults to `base_root / "candidate_context/profile.md"`.
- Returns a `RunResult` namedtuple: `(success: bool, count: int, error: str | None, cost_usd: float | None)`.
- Steps:
  1. Read profile.
  2. `prompt.build_prompt(profile_text)`.
  3. Subprocess to `aichat-ng --role company_discoverer -S <prompt>` (mirrors `prep_application.py:40-52` `aichat()` helper).
  4. Strip `<think>...</think>` blocks (LLM-output hygiene).
  5. `parser.parse_markdown(raw_md)`.
  6. `writer.commit_atomically(base_root, md_clean, json_payload)`.
  7. Log `discovery_complete` event with count to `pipeline.jsonl`.
  8. If reported cost > soft-threshold, ntfy a warning (does not block).
  9. On any exception in steps 1–6: log a `discovery_failed` event, ntfy an alert (if `ntfy_enabled`), return `RunResult(success=False, ...)`.
- ~100 lines.

**`writer.py`** — atomic temp+replace.

- Public: `commit_atomically(base_root: Path, markdown: str, json_payload: dict) -> Path` — returns the markdown file path.
- Stages both files to `<dest>.tmp.<pid>.<ts>` in the same directory as the final destination, then `os.replace`s each into place. If either stage fails, deletes any temp file already created and raises.
- If a previous good `discovered_companies.md` + `.json` exist, they are *not* renamed beforehand — `os.replace` is itself atomic. A rolling backup to `<base_root>/.backups/{stamp}/` is written *before* staging, mirroring the existing `findajob.onboarding.injector.backup_existing()` pattern.
- ~40 lines.

### 4.2 Entry points

**Weekly cron** — single line in `ops/crontab`:

```
0    2   *  *  0   timeout 600 python3 /app/scripts/discover_companies.py
```

`scripts/discover_companies.py` is a thin CLI:
- `--profile <path>` — defaults to `BASE/candidate_context/profile.md`
- `--ntfy/--no-ntfy` — defaults to `--ntfy` for cron use
- Exits 0 on success, 1 on any failure (cron picks this up).

**Onboarding post-injection hook** — added to `findajob.onboarding.injector.inject()` *after* the existing atomic seven-file commit + sentinel:

```python
# After mark_complete() — sentinel already written, onboarding has succeeded.
# This call is best-effort: failure does not roll back the onboarding.
try:
    from findajob.discoverer import run as run_discovery
    discovery_result = run_discovery(base_root, ntfy_enabled=False)
except Exception as e:
    discovery_result = RunResult(success=False, count=0, error=str(e), cost_usd=None)
return InjectResult(backup_dir=backup_dir, discovery=discovery_result)
```

The injector's existing return type is widened to a structured result so the route handler can render either "Discovery generated N companies" or "Discovery deferred to weekly cron — error: <msg>" on the completion page.

## 5. Output schemas

### 5.1 Markdown (`candidate_context/discovered_companies.md`)

```markdown
# Discovered Companies — generated 2026-04-26

Generated by findajob `company_discoverer` (model: openrouter:perplexity/sonar-reasoning-pro).
This file augments — does not replace — the `## Target Companies / Organizations`
section in `profile.md`.

## Cluster: Direct domain match

- **Example Company A** — channel=greenhouse. Reasoning: <one-line LLM justification>. Citations: [1], [2].
- **Example Company B** — channel=ashby. Reasoning: <...>. Citations: [3].

## Cluster: Transferable-competency adjacency

- **Example Company C** — channel=in_house. Reasoning: <...>. Citations: [4], [5].

## Cluster: Cross-industry application

- **Example Company D** — channel=workday. Reasoning: <...>. Citations: [6].

## References

[1] https://example.com/careers
[2] https://example.com/news
[3] https://example.com/about
...
```

### 5.2 JSON sidecar (`candidate_context/discovered_companies.json`)

```json
{
  "generated_at": "2026-04-26",
  "model": "openrouter:perplexity/sonar-reasoning-pro",
  "companies": [
    {
      "name": "Example Company A",
      "cluster": "direct",
      "channel": "greenhouse",
      "reasoning": "Operator's competency stack matches their advertised role types.",
      "citations": ["https://example.com/careers", "https://example.com/news"]
    }
  ]
}
```

The JSON is the canonical source for downstream consumers (#285, #283). The markdown is the canonical source for the operator. They are written together by the same atomic commit; a parse failure prevents either from being written.

## 6. Data flow

### 6.1 Weekly cron path

```
ops/crontab
  → scripts/discover_companies.py
    → findajob.discoverer.run(base_root)
      → reads candidate_context/profile.md
      → prompt.build_prompt(profile_text)
      → subprocess: aichat-ng --role company_discoverer -S <prompt>
      → parser.parse_markdown(raw_md)         # validates ≥3 companies, ≥2 clusters
      → writer.commit_atomically(base_root, md, json)   # atomic .md + .json
      → log_event("discovery_complete", count=N, cost_usd=X)
      → if cost > threshold: notify.send_raw(...)
```

### 6.2 Onboarding path

```
POST /onboarding/inject
  → findajob.onboarding.injector.inject(base_root, found, ...)
    → atomic 7-file commit (existing)
    → mark_complete() (existing)
    → [NEW] discoverer.run(base_root, ntfy_enabled=False)
      → reads the just-written profile.md
      → same flow as cron (prompt → subprocess → parse → atomic commit)
      → returns RunResult
    → returns InjectResult(backup_dir, discovery)
  → onboarding completion UI renders:
      - "Discovery generated {N} companies" on success, or
      - "Discovery deferred to weekly cron — {error}" on failure
```

The atomic seven-file commit + sentinel write is **untouched** — discovery is post-commit, soft-fail, never raises out of `inject()`. Existing onboarding tests continue to pass without modification.

## 7. Error handling

| Failure | Cron behavior | Onboarding behavior |
|---|---|---|
| API/network error (timeout, 5xx) | Last-good preserved; ntfy alert; exit 1 | Soft-fail; "weekly cron will produce one" rendered in completion UI; sentinel intact |
| Parse failure (`DiscoveryParseError`) | Last-good preserved; ntfy alert with first 200 chars of bad output; exit 1 | Same — soft-fail, log `discovery_failed` event with bad output for debugging |
| Cost > soft-threshold ($10/run by default) | Run completes + writes; ntfy warning sent (does not block) | Same |
| Empty/garbage content (<3 companies or <2 clusters) | Treated as parse failure (`DiscoveryParseError`) | Same |
| Profile.md missing | Exit 1 with explicit "profile not found" | Defensive log+skip — should never happen because sentinel proves profile exists |
| Disk full / write error in `writer` | Temp files cleaned up; original last-good untouched; raises | Soft-fail, surfaced in completion UI |

The `last-good preserved` invariant is the architectural guarantee: at no point during a failure can the operator be left without the prior `discovered_companies.md` if one existed. The atomic temp+replace ensures this on the disk side; the rollback in `writer` ensures it on the staging side.

## 8. Cost guardrail

`sonar-reasoning-pro` reports per-request token counts and cost in the response metadata. The runner extracts the cost field and compares against a configurable threshold:

- **Default threshold:** $10 per run (configurable via `DISCOVERY_COST_THRESHOLD_USD` env var).
- **Behavior on threshold breach:** ntfy warning of the form `discovery: run cost $X.XX exceeds threshold $Y.YY (still wrote N companies)`. The run still writes its output; the warning is informational.
- **Annual budget envelope** (per stack): 52 runs × ~$5/run = ~$260/year operator-side, ~$260/year on the Alice stack. Combined ~$520/year for both stacks. Within acceptable limits.
- **Hard cap:** none. OpenRouter's per-key budget controls are the safety net of last resort.

## 9. Testing

### 9.1 Unit (CI, no API)

- **`parser.py`** — golden markdown fixtures in `tests/fixtures/discoverer/`:
  - `valid_three_clusters.md` — happy path, all three clusters, footer references
  - `valid_two_clusters.md` — boundary case: only `direct` + `adjacency` populated
  - `invalid_one_cluster.md` — fails ≥2 clusters gate
  - `invalid_two_companies.md` — fails ≥3 companies gate
  - `invalid_missing_channel.md` — fails per-entry validation
  - `valid_with_extra_whitespace.md` — robust to footer-reference whitespace edge cases
  - `valid_unknown_channel.md` — `channel=unknown` is a valid value, not a parse failure
- **`writer.py`** — atomic commit, backup behavior, parent-directory creation, rollback on staging failure (mock `os.replace` to raise on the second file).
- **`prompt.py`** — given a fixture profile, the prompt is non-empty, references the expected section names, and contains no operator-specific or field-locked tokens (parametric assertion: build with two different fixture profiles, prompts differ in profile-derived content but share field-agnostic scaffolding).

### 9.2 Integration (CI, mocked aichat)

- **`runner.py`** — mock `subprocess.run` returning canned LLM output, verify:
  - Happy path writes both `.md` and `.json` to disk in tmpdir
  - `discovery_complete` event logged with correct count
  - Parse failure path leaves disk untouched and emits `discovery_failed`
  - `<think>...</think>` blocks are stripped before parser sees the content
  - Cost-threshold breach emits ntfy warning but does not block write

### 9.3 Manual smoke (PR-time, real API, ~$0.10)

Run the discoverer against the operator's real `candidate_context/profile.md` once during implementation review. Eyeball the three clusters: do they read as field-appropriate? Are the recommended companies NOVEL (not on the candidate's static `## Target Companies` list)? Does the prompt's reasoning-per-row reference the operator's competencies plausibly?

Document the result in the PR description (one paragraph, the count + cluster headers + a representative reasoning line). No permanent fixture, no committed Alice fixture, no permanent CI gate. Per the Q4 brainstorm reflection, generalization is a code-review concern for upstream contributions — not a CI contract.

**Empirical findings (smoke iterations during PR-time validation):**

The PR-time smoke caught two high-value design defects the brainstorm/spec phase did not anticipate:

1. **Perplexity's search architecture is single-query-per-call.** The role-file system prompt is ignored by the search component (per docs.perplexity.ai/guides/prompt-guide). Salient noun phrases in the user prompt's opening sentence drive the auto-generated search query. "See below" structures, generic openers ("identify companies hiring people for the following roles"), and literal section names ("Target Companies / Organizations") all anchored the search incorrectly — produced refusals or off-topic hits. **Fix:** `prompt.py` extracts the candidate's first target-role bullet's headline + descriptor and inlines them in the opener so the search query is field-grounded.

2. **The "Target Companies" list as a "seed" produced regurgitation, not discovery.** With the original spec wording, the model treated the candidate's static list as inclusion guidance and recommended ~80% companies the candidate already knew. **Fix:** `## Target Companies / Organizations` is now treated as the **EXCLUSION list** — the role file's load-bearing instruction is novelty (find companies the candidate has NOT named). The opener biases toward "emerging or less-prominent" organizations.

3. **Strict per-row citation requirement blocked novel discoveries.** Perplexity's search returns prominent hits (= excluded hyperscalers); the model knew the right emerging companies from training data but couldn't cite them under the strict-URL constraint. The model would refuse rather than recommend without a URL. **Fix:** citations are now OPTIONAL per row — when a verifiable URL is in search results, include it; otherwise omit the `Citations: [N]` clause entirely. Operator hand-verifies via the `/config/` editor (already allowlisted). Parser updated: `_ROW_RE` makes the citations clause optional; `tests/fixtures/discoverer/valid_no_citations.md` covers the new shape.

Final smoke (smoke #5) against the operator's profile produced 11 NOVEL companies across all three clusters with reasoning lines tying each to specific operator competencies (NPI, EVT→DVT→PVT lifecycle, technician enablement, field deployment). Zero overlap with the operator's Tier 1 / Tier 2 seed list. Cost: ~$0.10.

## 10. Generalization safety

The role file `config/roles/company_discoverer.md` is intentionally field-agnostic. It instructs the LLM to read the candidate profile and reason about competency-stack adjacencies. It does **not**:

- Enumerate any industry or field by name
- Name any specific company
- Reference any role title
- Assume a particular field's conventions

A top-of-file comment in the role file says:

> This prompt is intentionally field-agnostic. It reads the candidate's profile and reasons about competency-stack adjacencies in their field, whatever that field is. If you fork this project to tune the discoverer for your own field, that is expected; if you contribute back upstream, please preserve field-agnosticism so other operators in unrelated fields continue to benefit from improvements.

A note is added to `docs/GENERALIZATION.md` confirming that the discoverer replaces the hand-curated Tier 1 list as the primary mechanism for competency-fit discovery, and that the static list now serves only as a strategic-preference seed.

The pre-commit PII hook continues to guard against leaks of operator identifiers into tracked files; that gate is independent of and orthogonal to field-agnosticism.

## 11. Documentation impact

Mandatory updates landed in the same PR as the implementation:

- **`CLAUDE.md`** — Pipeline Context Table: add row for `company_discoverer` (model, output paths, frequency).
- **`CLAUDE.md`** — Container Context: add row for `discovered_companies.md` + `.json` (gitignored, lives under `candidate_context/`).
- **`CLAUDE.md`** — Architecture section: brief paragraph naming the discoverer + its consumer contract (#285, #283).
- **`docs/GENERALIZATION.md`** — note discoverer as the field-agnostic mechanism that supersedes the static Tier 1 list for competency-fit purposes; the static list remains for strategic preference.
- **`candidate_context/profile.md.example`** — annotation that `## Target Companies / Organizations` is a seed for the discoverer, not the universe.
- **`config/roles/company_discoverer.md`** — top-of-file comment explaining field-agnostic intent (per §10).
- **`CHANGELOG.md`** — Unreleased entry: "feat(scorer): add company_discoverer role and weekly discovery cron — augments static Tier 1 list with reasoned, regenerable, field-agnostic competency-fit signal (#284)."
- **No `migration-required` label** — output files are gitignored, no schema changes, no breaking changes to existing config files.

## 12. Self-review — spec section ↔ implementing task

Each section of this spec should map to one or more tasks in the forthcoming plan:

| Spec section | Tasks expected |
|---|---|
| §3.1 In scope: role file | Write `config/roles/company_discoverer.md` |
| §3.1 In scope: library module | Create `src/findajob/discoverer/{prompt,parser,runner,writer}.py` |
| §3.1 In scope: CLI | Create `scripts/discover_companies.py` |
| §3.1 In scope: cron | Add line to `ops/crontab` |
| §3.1 In scope: onboarding hook | Modify `findajob.onboarding.injector.inject()` + completion route |
| §3.1 In scope: output files | Implement writer + verify gitignore patterns cover `candidate_context/discovered_*` |
| §3.1 In scope: editable allowlist | Add `discovered_companies.md` to `EDITABLE_CATEGORIES` |
| §3.1 In scope: progress UI | Update onboarding completion template |
| §3.1 In scope: cost guardrail | Implement threshold check + ntfy in runner |
| §3.1 In scope: docs | Each item under §11 is its own diff |
| §9.1–§9.2 Tests | Each fixture / unit suite is its own task |
| §9.3 Manual smoke | PR-time gate, documented in PR description |

The plan must enumerate every row above as a numbered, verifiable task. A plan that omits any row is incomplete.

---

## Decisions log (from brainstorm)

- **Cluster taxonomy:** three axes — `direct`, `adjacency`, `cross_industry`. (Q1: A.)
- **Output schema:** markdown for humans + JSON sidecar for machines, both written by the same atomic commit. Parse failure prevents either being written. (Q2: D.)
- **Citation handling:** inline `[N]` markers + footer `## References` in markdown; resolved per-row URL arrays in JSON. (Q3: A.)
- **Field-agnostic verification:** one-time PR-time real-API smoke against operator profile, documented in PR description. No permanent fixture, no permanent CI gate. (Q4: simplified after reflection — committed fixture would be an antipattern.)
- **Onboarding wiring:** synchronous post-commit hook in `injector.inject()`. Soft-fails to "weekly cron will produce one" without rolling back the seven-file commit. (Q5: B.)
- **Cron cadence:** weekly, Sunday 02:00 container-local TZ. (Q6.)
- **Cost guardrail:** soft warning (ntfy) when reported per-run cost exceeds threshold; does not block. (Q7: B.)
- **Failure handling:** atomic temp+replace; last-good preserved; loud signals (log + ntfy) on any failure. (Q8.)
- **Consumer contract:** JSON sidecar with `name`, `cluster`, `channel`, `reasoning`, `citations` per row, plus `generated_at` + `model` at the document level. (Q9.)
