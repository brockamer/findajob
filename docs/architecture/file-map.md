# findajob — Key File Locations

This is the authoritative file map for the findajob codebase. Pointed at from `CLAUDE.md` § Key File Locations.

When this map drifts from the actual code (renamed file, new route module, retired script), update this file in the same change. CLAUDE.md keeps only the pointer; the inventory lives here.

## Layout

```
# ── Package (uv sync for dev; pip install -e . inside Docker image) ────────
<repo>/src/findajob/paths.py                # central path resolver — from findajob.paths import BASE, AICHAT, PANDOC
<repo>/src/findajob/utils.py                # shared utilities: log_event(), write_audit(), load_env()
<repo>/src/findajob/cleaning.py             # normalize, fingerprint, clean_title, clean_company
<repo>/src/findajob/ingest.py               # ingest_manual_job() — shared entry point for the /ingest/ web form
<repo>/src/findajob/fetchers/                 # Greenhouse, Gmail job fetching; RapidAPI feeds via adapters/
<repo>/src/findajob/fetchers/adapters/      # JobSourceAdapter Protocol + REGISTERED_ADAPTERS + JobsApi14Adapter + JobsApi14IndeedAdapter + JSearchAdapter; curation.py = per-adapter signup metadata loaded by /onboarding/feed-config/
<repo>/src/findajob/scoring.py              # score_job(), _build_feedback_block()
<repo>/src/findajob/scorer_prefilter.py     # deterministic pre-filter (Stage 1 + 2)
<repo>/src/findajob/cost_rollups.py         # SQL helpers backing all calibrated cost surfaces — current_calibration, per_job_cost, per_job_breakdown, weekly_spend, runway_weeks, projected_monthly (#87)
<repo>/src/findajob/web/app.py               # FastAPI app factory (create_app)
<repo>/src/findajob/web/routes/ingest.py     # GET /ingest/ form + POST /ingest/manual handler
<repo>/src/findajob/web/routes/config.py     # GET /config/, GET/POST /config/files/{path} — in-browser config editor
<repo>/src/findajob/web/routes/gmail_config.py # GET/POST /config/gmail/ — IMAP/app-password integration setup (#330)
<repo>/src/findajob/web/routes/tools.py      # GET /tools/ — stub linking to /config/
<repo>/src/findajob/web/routes/docs.py       # GET /docs/ index + GET /docs/{slug} — user docs viewer
<repo>/src/findajob/web/markdown.py          # render_markdown() — shared MD→HTML helper for materials + docs viewers
<repo>/src/findajob/web/config_files.py      # allowlist + resolve_editable() for /config/ editor
<repo>/src/findajob/web/onboarding_guard.py # NUX guard dependency — 307s /board,/materials,/stats to /onboarding when sentinel missing
<repo>/src/findajob/web/routes/onboarding.py # GET /onboarding/, POST /onboarding/keys (Step 1 keys collection)
<repo>/src/findajob/web/routes/onboarding_interview.py # In-app interview routes: /onboarding/interview/start | /turn | /{sid} | /{sid}/finalize. _resolved_chat_key reads tester's OpenRouter key from session credentials; 503 if no key. Step 1 keys mandatory before /start.
<repo>/src/findajob/web/routes/onboarding_feed_config.py # GET/POST /onboarding/feed-config/{sid} — per-adapter signup walkthrough (#408)
<repo>/src/findajob/web/routes/onboarding_gmail_config.py # GET/POST /onboarding/gmail-config/{sid}/{,skip,finish} — universal terminal gate; writes the sentinel after IMAP verify or explicit skip (#407)
<repo>/src/findajob/web/routes/feedback.py    # POST /feedback/ — in-app feedback widget; files GitHub issues. Env: GITHUB_FEEDBACK_PAT, FEEDBACK_STACK_LABEL, FEEDBACK_REPO (#227)
<repo>/src/findajob/web/routes/notifications.py # GET /notifications/, POST /notifications/{id}/read, POST /notifications/mark-all-read, GET /notifications/badge — in-app notification dashboard (#440)
<repo>/src/findajob/onboarding/parser.py    # parse interview emission into files to inject
<repo>/src/findajob/onboarding/injector.py  # atomic write + backup + Tier-1 derivation + sentinel
<repo>/src/findajob/onboarding/session_store.py # onboarding_sessions CRUD (history/captured_blocks/find_active)
<repo>/src/findajob/onboarding/interview_runner.py # Multi-turn OpenRouter chat-completions client (Sonnet 4.6 pinned)
<repo>/src/findajob/discoverer/                # company discovery library — prompt, parser, runner, writer
<repo>/src/findajob/web/routes/admin_stacks.py # GET /admin/stacks/ — operator-only multi-tenant stack health (#333; loaded iff FINDAJOB_OPERATOR_MODE=1)
<repo>/src/findajob/web/routes/healthz.py    # GET /healthz
<repo>/src/findajob/web/routes/materials.py  # GET /materials/ — candidate materials viewer (uses folder_resolver)
<repo>/src/findajob/web/folder_resolver.py   # stage→filesystem resolver with path-traversal guards
<repo>/src/findajob/web/templates/           # Jinja2 templates — base.html + one subdir per route group + shared _*.html partials

# ── Entry point scripts (called by systemd / CLI) ──────────────────────────
<repo>/scripts/triage.py                    # daily ingest → score → DB
<repo>/scripts/watchdog.py                  # resets stuck prep_in_progress jobs > 60 min (every 10 min cron)
<repo>/scripts/prep_application.py          # on-demand LLM material generation
<repo>/scripts/find_contacts.py             # LinkedIn contact matching + outreach drafts
<repo>/scripts/ingest_form.py               # Google Form → DB ingestion (retired; kept for manual drains)
<repo>/scripts/notify.py                    # ntfy push notifications — subcommands: send-raw, scoreboard, health-check, etc.
<repo>/scripts/rename_folders.py            # rename company folders to new format (idempotent)
<repo>/scripts/discover_companies.py            # weekly company discovery cron entry
<repo>/scripts/poll_openrouter_credits.py   # 5-min credits poll — writes cost_calibration row (#87)

# ── Candidate content (all gitignored — fill these in after cloning) ────────
<repo>/candidate_context/profile.md         # candidate profile — injected into scoring, resume, CL, outreach
<repo>/candidate_context/master_resume.md   # master resume — injected into prep; also indexed for REPL RAG
<repo>/candidate_context/voice_samples/     # writing samples for CL voice calibration (REPL RAG only)

# ── Config (pipeline operation — mostly gitignored) ──────────────────────────
<repo>/config/paths.env                     # binary path overrides (gitignored; see paths.env.example)
<repo>/config/roles/                        # role .md files (8 roles)
<repo>/config/scoring_schema.json           # JSON schema for LLM scorer output validation
<repo>/config/rapidapi_feeds.yaml            # operator-curated feed table (gitignored; see rapidapi_feeds.yaml.example)
<repo>/config/active_sources.txt           # per-stack active adapter list (gitignored; interview-emitted via 3h picker)
<repo>/config/jsearch_queries.txt          # LinkedIn/Indeed search queries (gitignored; interview-emitted, conditional on 3g 'a' selection)
<repo>/config/feed_urls.txt                 # Greenhouse / Lever / Ashby career-page feed URLs (gitignored; interview-emitted, conditional on 3g 'b' selection)
<repo>/candidate_context/linkedin-alerts.md # LinkedIn-alerts setup checklist (interview-emitted, conditional on 3g 'c' selection)
<repo>/config/gmail.json                    # Gmail IMAP/app-password config (gitignored, chmod 600)
<repo>/config/gmail_state.json              # Gmail IMAP UID + auth-failure tracker (gitignored)
<repo>/data/.env                            # API keys (chmod 600; gitignored)
<repo>/data/pipeline.db                     # SQLite — source of truth
<repo>/data/connections.csv                 # LinkedIn connections export (gitignored)

# ── Output & logs ───────────────────────────────────────────────────────────
<repo>/companies/                           # prep output folders ({Company}_{AbbrevTitle}_{date}_{time})
<repo>/companies/_applied/                   # applied job folders
<repo>/companies/_waitlisted/                # waitlisted job folders (deferred, not rejected)
<repo>/companies/_rejected/                  # rejected job folders (with marker files)
<repo>/logs/pipeline.jsonl                  # structured event log

# ── Operations ──────────────────────────────────────────────────────────────
<repo>/docs/release-process.md              # Claude's release orchestration runbook — dogfood gate, tag cut, rollback
<repo>/docs/setup/install-docker.md         # external-user Docker install + operations guide

# ── Quality ─────────────────────────────────────────────────────────────────
<repo>/pyproject.toml                       # deps, pytest, ruff, mypy config
<repo>/tests/                               # ~900 unit tests (pytest)
<repo>/.github/workflows/ci.yml            # CI: ruff + mypy + pytest on every push
```
