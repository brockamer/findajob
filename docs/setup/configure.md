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
RCLONE=/usr/bin/rclone           # Linux default
```

Linux defaults are already built into `src/findajob/paths.py`. If everything is in the expected locations, you can skip this file.

---

## data/.env

API keys and secrets. See `data/.env.example` for the full list.

```bash
ANTHROPIC_API_KEY=sk-ant-...
OPENROUTER_API_KEY=sk-or-...
GOOGLE_API_KEY=AIza...
PERPLEXITY_API_KEY=pplx-...
RAPIDAPI_KEY=...
NTFY_TOPIC=your-topic-name
```

Protect this file: `chmod 600 data/.env`

---

## aichat-ng config.yaml

Located at `~/.config/aichat_ng/config.yaml`.

Full template:
```yaml
model: gemini:gemini-3-flash-preview

clients:
  - type: gemini
    api_key: ${GOOGLE_API_KEY}

  - type: claude
    api_key: ${ANTHROPIC_API_KEY}

  - type: openrouter
    api_key: ${OPENROUTER_API_KEY}

  - type: perplexity
    api_key: ${PERPLEXITY_API_KEY}

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

**`type: claude` not `type: anthropic`** — aichat-ng uses `claude` as the type identifier.

---

## CLAUDE.local.md

Personal context for Claude Code sessions. Created from `CLAUDE.local.md.example`. Never committed to git.

Include:
- Your name and a brief bio
- Platform-specific tool paths (so Claude Code doesn't give you wrong commands)
- Your ntfy topic and Google Form URL
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

## Pre-commit PII hook (strongly recommended)

This repo is intended to be domain-agnostic and eventually public. A local pre-commit hook
blocks accidental commits of personal identifiers (your name, employer history, ntfy topic,
Google Form URL, etc.).

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
- Personal service handles (ntfy topic, Google Form short URLs)
- systemd unit label prefixes

**Test:** Create a dummy file with one of your patterns, stage it, and attempt a commit.
The hook should block it. Then unstage and delete the file.

**When it fires:** Fix the issue (move personal content to gitignored config files, or
redact) and retry the commit. Do NOT use `--no-verify` to bypass except in emergencies —
it defeats the whole purpose.

See also `docs/GENERALIZATION.md` for the broader tracking of domain-specific content that
should not land in tracked files.
