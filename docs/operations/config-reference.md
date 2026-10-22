# Configuration

How to configure the pipeline for your profile and job search.

---

## candidate_context/profile.md

Your candidate profile. Injected directly into scoring, resume tailoring, cover letter, and outreach prompts. Not passed through RAG.

**Key sections to include:**
- Identity (name, location, email, LinkedIn)
- Target role (what kind of jobs you're looking for)
- What makes you unusual (2–3 sentences on your differentiated background)
- Core competencies
- Career summary
- Employer history (most recent first, with 1-line descriptions)
- Target companies (this is what drives the Tier 1 scoring boost)
- What to emphasize
- Things to avoid mentioning

**Tier 1 company list:** Put your target companies in a section starting with "Tier 1 companies:" or "Target companies:". The job_scorer role reads this section to apply score boosts. Make the list explicit and unambiguous.

**Internally-branded team names:** If you have employer-specific programs or teams with ambiguous abbreviations, spell them out completely in the profile. Add an explanation line like:
```
Note: "XYZ Labs" = [full name and what it is] — not a geographic reference.
```

---

## candidate_context/master_resume.md

Your complete, unabridged resume in Markdown. This is the source of truth for the resume tailor — it will never invent experience not present here.

**Include everything:**
- All jobs, all date ranges, all titles
- All metrics you might want to use (even ones you'd normally leave off)
- All skills, certifications, education
- Contact info exactly as you want it to appear in tailored resumes

The resume tailor selects and reorders content from this file — it doesn't add content that isn't here.

Tailored resumes include your Education section whenever this file has one. To leave it off, say so in `profile.md` under `## Employer Formatting Rules` or `## Things to Avoid Mentioning`.

**Material that must never go on a submitted resume** — peer quotes, for example — can stay in this file for the cover letter, outreach and interview prep. Mark its section: put the words "not for submitted resumes" in the section's heading or on the first line under it, for example:

```markdown
## Selected Quotes
*(For interview prep, cover letters, and outreach — not for submitted resumes)*
```

The section runs to the next heading of the same or higher level, so its sub-sections are covered too. The resume tailor and the resume change report never see a marked section; the cover letter, outreach and interview prep still use it. As a second check, a tailored resume that contains six or more consecutive words from a marked section gets a red warning on its materials page and a "Fix before sending" item in its review checklist. A reworded version of marked text is not detected. The words anywhere else in the file mark nothing, and prep logs `master_resume_marker_unrecognized` when it finds them there. That includes a line directly under the file's title: it would cover the whole resume, so it is ignored.

---

## config/jsearch_queries.txt

> **Note:** `config/jsearch_queries.txt`, `config/feed_urls.txt`, and
> `candidate_context/linkedin-alerts.md` are now emitted by the
> onboarding interview (#283) based on your sub-phase 3g selection.
> Manual editing of these files remains supported — re-running
> onboarding overwrites them, but backs up the prior content under
> `.backups/{UTC-stamp}/`.

> **Activating JSearch.** This file's queries only run if `jsearch` is
> listed in `config/active_sources.txt`. The onboarding picker writes
> that for new stacks; to flip JSearch on or off later, tick the
> checkbox at `/settings/active-sources/`. Stacks shipping the default
> `['jobs-api14']` need this step before any queries here are used.

LinkedIn and Indeed search queries. One per line. Blank lines and `#` comments ignored.

**Critical rules:**
- 3–4 word natural phrases only
- Keyword-stuffed strings (5+ words) return zero LinkedIn results
- Test each query manually in LinkedIn before adding
- 8–12 queries is a good ceiling; more is diminishing returns

**Good examples:**
```
hardware infrastructure engineer
data center operations
AI hardware program manager
NPI technical lead
```

**Bad examples (too long, keyword-stuffed):**
```
senior hardware infrastructure program manager NPI
data center AI GPU infrastructure engineer lead
```

---

## config/feed_urls.txt

Greenhouse company slugs — one per line. The pipeline fetches:
`https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true`

To find a company's Greenhouse slug:
1. Go to their jobs page
2. Look for `boards.greenhouse.io/{slug}` or `jobs.lever.co/{slug}` in the URL
3. Test: `curl https://boards-api.greenhouse.io/v1/boards/{slug}/jobs | head -c 200`

Companies that don't use Greenhouse won't have a slug — use LinkedIn/Indeed search for them instead.

---

## config/target_locations.txt

Where the candidate can work — one place per line. Onboarding writes it from the
geography answer; edit it at `/config/`. Template: `config/target_locations.txt.example`.

```
Denver, CO
Boulder, CO
Remote
```

Accepted forms: `Remote`, `City, ST`, `City, Country` (country name in full),
`United States`. Blank lines and `#` comments are ignored.

The file has two uses:

1. **Fetching.** The RapidAPI feeds search these places. With no file, they search
   the whole United States.
2. **Scoring (#1217).** The prefilter's location gate (`findajob.location_gate`)
   scores a posting 2, without an LLM call, when its location names a US state, a
   country or a non-US region (`EMEA`, `APAC`, …) that the file does not cover.
   The board shows Flag reason `location_mismatch`. The scorer prompt also gets the
   list, and the scorer caps an out-of-area role at 3.

The gate compares US states and countries, never cities, and every doubtful reading
passes to the scorer. Because of this:

- A posting in a listed state but another city passes the gate. The scorer decides.
- A posting the gate cannot place passes: a city name alone (`Dublin`), `See posting`,
  blank, or a coarse value (`United States`, `Remote`).
- A world region (`Worldwide`, `Global`, `Anywhere`) passes. A region that covers the
  US (`Americas`, `North America`) passes when the list includes the US.
- A two-letter code is a US state only after a comma (`Austin, TX`) and only when the
  list includes the US. `LA` alone, `SF / LA` and `IN-OFFICE` name no state.
- A posting with several locations passes if any of them is covered.
- A remote posting passes if it is in a listed country. `Remote - India` against a
  US list scores 2.
- **A commute area that crosses a state line needs one line per state.** A metro on
  a state border with one line rejects postings on the other side of the border.
- A list with only `Remote` names no state or country, so the gate is off. Add a
  line for your country. A `United States` line lets any US state through.
- **A line in another form turns the gate off** — a city alone (`Bay Area`), a
  region (`Remote - Europe`), a province (`Vancouver, BC`), or `Anywhere`. The gate
  cannot know the whole area, so it does not guess. Triage logs one
  `location_gate_off` event in `logs/pipeline.jsonl` that names the line.

The gate is off when the file is absent, or when `FINDAJOB_LOCATION_GATE=off` is
set in `data/.env`. With the switch off, the scorer still gets the list and still
caps an out-of-area role at 3. Jobs scored before a change keep their scores until
they are re-scored (`scripts/rescore_all.py` applies the gate); new jobs get it from
the next triage run.

---

## config/target_companies.md

A human-readable target company list.

---

## config/paths.env

Binary path overrides for your platform. Only needed if your binaries are in non-standard locations.

```bash
PANDOC=/usr/bin/pandoc           # Linux default
```

Linux defaults are already built into `src/findajob/paths.py`. If everything is in the expected locations, you can skip this file.

---

## Choosing your job-search service

During onboarding, **Section 3h** of the interview reads `config/rapidapi_feeds.yaml`
(the operator-curated feed table) and recommends the best RapidAPI feed for the
candidate's field. The recommendation is based on the field classification established
in Section 3g.

At the end of the interview, the injector writes `config/active_sources.txt` with the
chosen adapter slug (e.g. `jobs-api14` or `jsearch`). The feed-config form at
`/onboarding/feed-config/{session_id}` then collects the per-feed API key and runs a
live connection test against every query in `config/jsearch_queries.txt`.

**To change your active source after onboarding:** visit `/onboarding/?mode=rerun`.
Section 3h re-runs the picker; the new choice overwrites `config/active_sources.txt`.

See `docs/getting-started/api-keys.md` for per-feed sign-up walkthroughs.

---

## data/.env

API keys and secrets. See `data/.env.example` for the full list.

```bash
OPENROUTER_API_KEY=sk-or-...
RAPIDAPI_KEY=...        # canonical key for all RapidAPI feeds (optional; set by onboarding picker)
# JOBS_API14_KEY=...   # legacy per-adapter fallback — still works; RAPIDAPI_KEY preferred (#414)
# JSEARCH_API_KEY=...  # legacy per-adapter fallback — still works; RAPIDAPI_KEY preferred (#414)
NTFY_TOPIC=your-topic-name
# GEMINI_API_KEY=...   # Google AI API key for podcast generation (optional; enables interview-prep podcasts #870)
# OPENROUTER_CREDIT_AMBER_USD=5   # nav credit chip turns amber below this (default $5)  #665
# OPENROUTER_CREDIT_RED_USD=1     # nav credit chip turns red below this (default $1)    #665
# FINDAJOB_WATCHTOWER_HTTP_URL=http://watchtower:8080  # Watchtower HTTP API base URL (optional; enables the dashboard "Update now" button #1017)
# FINDAJOB_WATCHTOWER_HTTP_TOKEN=...                   # Watchtower HTTP API token (optional; required with FINDAJOB_WATCHTOWER_HTTP_URL)
# FINDAJOB_BRIEFING_READY_WINDOW_DAYS=14               # days a briefing waits for Continue prep / Reject before the job returns to scored (default 14; 0 = never) #1222
# FINDAJOB_LOCATION_GATE=off                           # turns the scoring location gate off and keeps config/target_locations.txt (default on when the file exists) #1217
```

Protect this file: `chmod 600 data/.env`

Design details for the in-app onboarding interview live in operator-private notes.

Every LLM call goes through `findajob.llm.openrouter.complete()`. The
wrapper writes a `cost_log` row per call with `cost_usd` populated directly
from OpenRouter's `response.usage.cost` — no heuristic, no calibration. All
LLM access uses the single `OPENROUTER_API_KEY` credential.

---

## Path differences under Docker

When running from the `ghcr.io/brockamer/findajob` image, user-editable files
live under your Dockge stack's `state/` directory rather than the repo root.
The pipeline itself sees them at `/app/…` inside the container via bind mounts:

| What | Native host path | Docker host path | In-container path |
|---|---|---|---|
| API keys | `data/.env` | `state/data/.env` | `/app/data/.env` |
| Personal config | `config/*.yaml\|.txt\|.json` | `state/config/*` | `/app/config/*` |
| Candidate profile | `candidate_context/profile.md` | `state/candidate_context/profile.md` | `/app/candidate_context/profile.md` |

Where this doc says "edit `data/.env`" or "place file in `config/`," Docker
users should substitute the corresponding `state/…` path on the host. Content
and format are identical.

---

## CLAUDE.local.md

Personal context for Claude Code sessions. Created from `CLAUDE.local.md.example`. Never committed to git.

Include:
- Your name and a brief bio
- Platform-specific tool paths (so Claude Code doesn't give you wrong commands)
- Your ntfy topic
- Any project-specific abbreviations that Claude might misinterpret
- Your personal target company list

Claude Code loads `CLAUDE.md` and then appends `CLAUDE.local.md` (via the `@CLAUDE.local.md` directive at the bottom of CLAUDE.md).

---

## Voice Samples

Place writing samples in `candidate_context/voice_samples/` as plain text `.txt` files. The cover letter writer role uses these for voice calibration.

Good samples:
- Cover letters you've written and sent
- Application essays
- Professional blog posts or emails that sound like you

Add 3–5 minimum. Don't overthink the naming.

---

## data/connections.csv (optional)

A LinkedIn connections export used to match warm contacts at target companies and
generate outreach drafts during prep.

- **Optional.** If the file is absent, `find_contacts()` returns no matches silently
  — no error is logged and prep runs normally without the outreach step.
- To enable, export your LinkedIn connections via **Settings → Data privacy → Get a
  copy of your data → Connections** on LinkedIn, then save the resulting CSV to
  `data/connections.csv`. Onboarding's LinkedIn step walks the same procedure
  inside the in-app flow.

---

## Pre-commit PII hook (strongly recommended)

This repo is intended to be domain-agnostic and eventually public. A local pre-commit hook
blocks accidental commits of personal identifiers (your name, employer history, ntfy topic, etc.).

The hook lives at `.git/hooks/pre-commit` — **not tracked by git**, so each clone of the
repo must install its own.

**Install:**
```bash
cp docs/getting-started/pre-commit-hook.example.sh .git/hooks/pre-commit
chmod +x .git/hooks/pre-commit
```

**Configure:** Open `.git/hooks/pre-commit` and edit the `PATTERNS` array with your real
identifiers. Categories to add:
- Your first/last name and nicknames
- Email username (the part before `@`)
- Phone number (if ever a risk of leaking)
- Employer names from your career history
- Personal service handles (ntfy topic)
- systemd unit label prefixes

**Test:** Create a dummy file with one of your patterns, stage it, and attempt a commit.
The hook should block it. Then unstage and delete the file.

**When it fires:** Fix the issue (move personal content to gitignored config files, or
redact) and retry the commit. Do NOT use `--no-verify` to bypass except in emergencies —
it defeats the whole purpose.

**Diagnostic output:** As of #314, every hook run prints a one-line stderr summary
(`pre-commit: PII scan: N patterns × M added lines`). If a commit lands without that
line appearing — check `git config core.hooksPath`, that the hook is executable, and
whether `--no-verify` was passed. The line is the canary for silent-fail conditions.

### CI-side defense (defense-in-depth, #314)

The local hook can fail silently (wrong git env, `--no-verify` slip, malformed
PATTERNS). The `.github/workflows/pii-scan.yml` workflow scans every PR diff against
the same patterns from a GitHub Secret, so a defect in the local hook doesn't leave
the repo unprotected.

**Install (one-time):** copy your local PATTERNS array into a GitHub Secret named
`PII_PATTERNS_REGEX`. One regex per line, no quotes, no shell escaping:

```bash
# Extract the pattern strings from the PATTERNS array only (comment and blank lines skip):
sed -n '/^PATTERNS=(/,/^)/p' .git/hooks/pre-commit \
  | grep -E '^\s*"' \
  | sed -E 's/^\s*"//;s/"\s*$//;s/\\\\/\\/g' > /tmp/pii-patterns.txt
wc -l < /tmp/pii-patterns.txt   # must equal the number of active entries in PATTERNS
gh secret set PII_PATTERNS_REGEX < /tmp/pii-patterns.txt
rm /tmp/pii-patterns.txt
```

Two details in that recipe matter:

- **Only the PATTERNS block.** The hook's `SECRET_PATHS` array uses the same quoted-line
  format. A plain `grep` over the whole hook also captures those file paths
  (`data/.env`, `config/gmail.json`, …), turns them into patterns, and fails every PR
  that merely mentions one of them.
- **Backslashes are unescaped.** The hook stores each pattern inside bash double quotes,
  where `"docker\\.lan"` means the regex `docker\.lan`. The workflow reads the secret line
  by line and hands each line to `grep -E` unchanged, so a `\\.` left in the secret never
  matches. The last `sed` expression turns `\\` into `\`; a pattern written `\.` in the
  hook passes through unchanged.

**When unset:** on a same-repository pull request the workflow fails — a missing secret
there is a misconfiguration. GitHub passes no Actions secrets to a pull request from a
fork or to a run Dependabot triggers; for those the workflow passes, writes a job summary
saying the scan did not run, and the maintainer scans the diff locally before merging
([`docs/maintainers/fork-pull-requests.md`](../maintainers/fork-pull-requests.md), #1223).

**When set and any pattern matches:** the workflow fails the PR check; the matched
pattern is printed in the run log (the matched line itself is NOT printed to avoid
leaking the PII to public CI logs). Find the line locally, fix, push.

**Updating patterns:** when you add new PII patterns to the local hook, re-run
the install command above to push the updated list to the secret. If you also keep a
Dependabot copy so that Dependabot pull requests are scanned in CI (see
[`fork-pull-requests.md`](../maintainers/fork-pull-requests.md)), update it at the same
time: run the same recipe with `gh secret set PII_PATTERNS_REGEX --app dependabot`.
The two copies are separate secrets, and nothing keeps them in step.

**Private operator patterns (beyond standard PII).** You may keep additional private
patterns operator-side — for example, personal workflow-discipline terms you never want
surfacing in the public tree. Add them to **both** your local `PATTERNS` array and the
`PII_PATTERNS_REGEX` secret, so they're caught per-clone *and* in CI. By convention the
specifics of such patterns stay operator-side and are **not** enumerated in this tracked
doc — the same rule as the `<operator-domain>` / `<deployment-host>` placeholders.

See also `docs/maintainers/generalization.md` for the broader tracking of domain-specific content that
should not land in tracked files.

## Rotating API keys on a deployed stack

With Phase 2 of the OpenRouter cutover, 10 of 11 roles depend on
`OPENROUTER_API_KEY`. Rotating it cleanly on a running stack:

1. Generate a new key in the OpenRouter dashboard and note both the
   old and new values.
2. Edit your stack's env file (`/opt/stacks/findajob-<you>/state/data/.env`
   or wherever you keep credentials — check your compose file's
   `env_file:` directive) and replace the `OPENROUTER_API_KEY=…` line.
3. Recreate the container so the OpenRouter wrapper picks up the new value:
   `docker compose up -d --force-recreate` from the stack directory.
4. Verify with a smoke call:
   `docker compose exec scheduler python3 -c "from findajob.llm.openrouter import complete; print(complete(role='job_scorer', prompt='say hello', max_tokens=8).text)"`.
   If the call succeeds, revoke the old key in the OpenRouter dashboard.

`GOOGLE_API_KEY` was removed in v0.19.0 (#455); RAG infrastructure
(including the Gemini embedding client) was retired. `ANTHROPIC_API_KEY` and
`PERPLEXITY_API_KEY` were retired in v0.4.0 — both providers are
reached through OpenRouter now. Keep rotations staggered — don't
revoke the old key until the new one has served at least one live
pipeline run without error.

---

## Pre-commit hook patterns

Each clone runs its own `.git/hooks/pre-commit` to block accidental PII commits. After cloning, extend the `PATTERNS` array in your local hook to cover Gmail integration files (introduced in #330):

```bash
"\"app_password\":"
"\"last_uid\":"
```

These patterns match the JSON keys in the credential files rather than the filenames, so documentation references to `config/gmail.json` don't trigger false positives. Both files are also covered by `.gitignore` — the hook is defense-in-depth in case someone runs `git add -f`.
