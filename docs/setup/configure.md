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

---

## config/jsearch_queries.txt

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

## config/target_companies.md

A human-readable target company list. If you want it available in REPL context, also copy it to `candidate_context/` — the RAG index covers that directory.

---

## config/paths.env

Binary path overrides for your platform. Only needed if your binaries are in non-standard locations.

```bash
AICHAT_NG=/usr/local/bin/aichat-ng
PANDOC=/usr/bin/pandoc           # Linux default
```

Linux defaults are already built into `src/findajob/paths.py`. If everything is in the expected locations, you can skip this file.

---

## data/.env

API keys and secrets. See `data/.env.example` for the full list.

```bash
OPENROUTER_API_KEY=sk-or-...
GOOGLE_API_KEY=AIza...
RAPIDAPI_KEY=...
NTFY_TOPIC=your-topic-name
```

Protect this file: `chmod 600 data/.env`

---

## aichat-ng config.yaml

Located at `~/.config/aichat_ng/config.yaml`.

Full template:
```yaml
model: openrouter:google/gemini-3-flash-preview

clients:
  - type: gemini
    api_key: ${GOOGLE_API_KEY}

  - type: openrouter
    api_key: ${OPENROUTER_API_KEY}

  # Dedicated embedding client — name must match what triage.py passes to --rag
  # Do NOT include this client in --sync-models runs
  - type: gemini
    name: gemini-embed
    api_key: ${GOOGLE_API_KEY}
    models:
      - name: gemini-embedding-001
        max_input_tokens: 2048

roles_dir: ~/findajob/config/roles

# RAG configuration
rag_embedding_model: gemini-embed:gemini-embedding-001
rag_reranker_model: ~
```

**Critical:** API keys MUST use `${VAR_NAME}` syntax, not literal values. The variables must be in your environment when you run aichat-ng. The pipeline's scripts load `data/.env` at startup, but REPL usage needs the env vars set in your shell profile.

Anthropic and Perplexity model access routes through OpenRouter — there are no direct `claude` or `perplexity` clients in the config since v0.4.0. If your pre-v0.4.0 stack still has those blocks in `state/aichat_ng/config.yaml`, they are inert and safe to remove.

---

## Path differences under Docker

When running from the `ghcr.io/brockamer/findajob` image, user-editable files
live under your Dockge stack's `state/` directory rather than the repo root.
The pipeline itself sees them at `/app/…` inside the container via bind mounts:

| What | Native host path | Docker host path | In-container path |
|---|---|---|---|
| API keys | `data/.env` | `state/data/.env` | `/app/data/.env` |
| aichat-ng config | `~/.config/aichat_ng/config.yaml` | `state/aichat_ng/config.yaml` | `/root/.config/aichat_ng/config.yaml` |
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
- Your ntfy topic and Google Form URL (legacy — Form retired in #62, URL kept for drain compatibility)
- Any project-specific abbreviations that Claude might misinterpret
- Your personal target company list

Claude Code loads `CLAUDE.md` and then appends `CLAUDE.local.md` (via the `@CLAUDE.local.md` directive at the bottom of CLAUDE.md).

---

## Voice Samples

Place writing samples in `candidate_context/voice_samples/` as plain text `.txt` files. The cover letter writer role uses these for voice calibration (available via RAG in REPL mode).

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
- To enable, follow the export steps in
  [`prerequisites.md`](prerequisites.md#linkedin-connections-export-optional-but-recommended)
  and save the file to `data/connections.csv`.

---

## Pre-commit PII hook (strongly recommended)

This repo is intended to be domain-agnostic and eventually public. A local pre-commit hook
blocks accidental commits of personal identifiers (your name, employer history, ntfy topic,
Google Form URL (legacy — retired in #62), etc.).

The hook lives at `.git/hooks/pre-commit` — **not tracked by git**, so each clone of the
repo must install its own.

**Install:**
```bash
cp docs/setup/pre-commit-hook.example.sh .git/hooks/pre-commit
chmod +x .git/hooks/pre-commit
```

**Configure:** Open `.git/hooks/pre-commit` and edit the `PATTERNS` array with your real
identifiers. Categories to add:
- Your first/last name and nicknames
- Email username (the part before `@`)
- Phone number (if ever a risk of leaking)
- Employer names from your career history
- Personal service handles (ntfy topic, Google Form short URLs (legacy))
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
# Extract just the pattern strings from your local hook (skip blank/comment lines):
grep -E '^\s*"' .git/hooks/pre-commit | sed -E 's/^\s*"//;s/"\s*$//' > /tmp/pii-patterns.txt
gh secret set PII_PATTERNS_REGEX < /tmp/pii-patterns.txt
rm /tmp/pii-patterns.txt
```

**When unset:** the workflow logs a warning and passes (so external/fork PRs that
can't access secrets aren't blocked — they shouldn't have operator PII anyway).

**When set and any pattern matches:** the workflow fails the PR check; the matched
pattern is printed in the run log (the matched line itself is NOT printed to avoid
leaking the PII to public CI logs). Find the line locally, fix, push.

**Updating patterns:** when you add a new beta tester or a new personal service
handle to the local hook, re-run the install command above to push the updated
list to the secret.

See also `docs/GENERALIZATION.md` for the broader tracking of domain-specific content that
should not land in tracked files.

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
`PERPLEXITY_API_KEY` were retired in v0.4.0 — both providers are
reached through OpenRouter now. Keep rotations staggered — don't
revoke the old key until the new one has served at least one live
pipeline run without error.
