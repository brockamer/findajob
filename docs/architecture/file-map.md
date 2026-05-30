# findajob — Key File Locations

This is the authoritative file map for the findajob codebase. Pointed at from `CLAUDE.md` § Key File Locations.

When this map drifts from the actual code (renamed file, new route module, retired script), update this file in the same change. CLAUDE.md keeps only the pointer; the inventory lives here.

## Layout

```
# ── Package (uv sync for dev; pip install -e . inside Docker image) ────────
<repo>/src/findajob/paths.py                # central path resolver — from findajob.paths import BASE, AICHAT, PANDOC
<repo>/src/findajob/utils.py                # shared utilities: log_event(), write_audit(), load_env()
<repo>/src/findajob/cleaning.py             # normalize, fingerprint, clean_title, clean_company
<repo>/src/findajob/config_seed.py          # seed_runtime_config() — entrypoint-invoked .example→live materialization for configs with hard 500-on-missing paths (#627)
<repo>/src/findajob/ingest.py               # ingest_manual_job() — shared entry point for the /ingest/ web form
<repo>/src/findajob/fetchers/                 # Greenhouse, Gmail job fetching; RapidAPI feeds via adapters/
<repo>/src/findajob/fetchers/adapters/      # JobSourceAdapter Protocol + REGISTERED_ADAPTERS + per-source adapter classes (jobs_api14, jobs_api14_indeed, jobs_api14_bing, jsearch, greenhouse, ashby, lever, gmail, workday_cxs, gem); curation.py = per-adapter signup metadata loaded by /onboarding/feed-config/
<repo>/src/findajob/scoring.py              # score_job(), _build_feedback_block() — calls findajob.llm.openrouter (#470)
<repo>/src/findajob/scorer_prefilter.py     # deterministic pre-filter (Stage 1 + 2)
<repo>/src/findajob/llm/openrouter.py       # canonical OpenRouter HTTP wrapper (#470) — complete(), CompletionResult, OpenRouterError; cache_control on cached_prefix + cache_system axes
<repo>/src/findajob/llm/tts.py              # Gemini TTS wrapper (#870) — generate_audio(), pcm_to_mp3(), generate_podcast(); MultiSpeakerVoiceConfig two-speaker rendering via Google AI API
<repo>/src/findajob/cost_rollups.py         # SQL helpers backing all cost surfaces — per_job_cost, per_job_breakdown, weekly_spend, projected_monthly, spend_this_month
<repo>/src/findajob/web/app.py               # FastAPI app factory (create_app)
<repo>/src/findajob/web/middleware/disconnect_state.py # ASGI middleware wrapping receive() to record http.disconnect into scope["findajob.client_disconnected"] (#743) — passive observation, no race with Starlette's listen_for_disconnect; SSE route reads the flag via is_cancelled closure
<repo>/src/findajob/web/routes/ingest.py     # GET /ingest/ form + POST /ingest/manual handler
<repo>/src/findajob/web/routes/config.py     # GET /config/, GET/POST /config/files/{path} — in-browser config editor
<repo>/src/findajob/web/routes/settings_excluded_employers.py # GET/POST /settings/excluded-employers/ — structured editor for config/excluded_employers.yaml; per-section exact + regex with validation. /config/ raw editor remains as fallback. (#729)
<repo>/src/findajob/web/routes/gmail_config.py # GET/POST /config/gmail/ — IMAP/app-password integration setup (#330)
<repo>/src/findajob/web/routes/tools.py      # GET /tools/ — guided LLM prompts + config/onboarding links (#150)
<repo>/src/findajob/web/tools_registry.py   # tile data, prompt loader, claude.ai/new?q= URL builder (#150)
<repo>/config/tool_prompts/*.md             # prompt source files loaded by the tools registry (#150)
<repo>/src/findajob/web/routes/docs.py       # GET /docs/ index + GET /docs/{slug} — user docs viewer
<repo>/src/findajob/web/markdown.py          # render_markdown() — shared MD→HTML helper for materials + docs viewers
<repo>/src/findajob/web/config_files.py      # allowlist + resolve_editable() for /config/ editor
<repo>/src/findajob/web/onboarding_guard.py # NUX guard dependency — 307s /board,/materials,/stats to /onboarding when sentinel missing
<repo>/src/findajob/web/routes/onboarding.py # GET /onboarding/, POST /onboarding/keys (Step 1 keys collection)
<repo>/src/findajob/web/routes/onboarding_interview.py # In-app interview routes: /onboarding/interview/start | /turn | /{sid} | /{sid}/finalize. _resolved_chat_key reads user's OpenRouter key from session credentials; 503 if no key. Step 1 keys mandatory before /start.
<repo>/src/findajob/web/routes/onboarding_feed_config.py # GET/POST /onboarding/feed-config/{sid} — per-adapter signup walkthrough (#408)
<repo>/src/findajob/web/routes/onboarding_gmail_config.py # GET/POST /onboarding/gmail-config/{sid}/{,skip,finish} — Gmail IMAP gate; /finish blocks until IMAP verify (#407 invariant) then hands off to the connections gate (#571); no longer writes the sentinel
<repo>/src/findajob/web/routes/onboarding_connections.py # GET/POST /onboarding/connections/{sid}/{,upload,skip} — terminal gate; validates LinkedIn Connections.csv header + atomic-writes to data/connections.csv, OR explicit skip; writes the sentinel on either path (#571)
<repo>/src/findajob/web/routes/onboarding_restore.py # GET/POST /onboarding/restore/{,upload} — restore from backup tarball as alternative to chat-interview onboarding (#841); confirm-overwrite gate on already-onboarded stacks
<repo>/src/findajob/web/connections_upload.py # Shared validator + atomic-write for LinkedIn connections.csv — header/size/UTF-8 checks, REQUIRED_COLUMNS, MAX_BYTES, atomic os.replace; consumed by onboarding_connections (#571) AND settings_connections (#614) so the two upload paths can't drift
<repo>/src/findajob/web/backup.py            # Streaming tarball creation with sqlite3 .backup API — produces state/ contract tarball for /settings/backup/ (#841)
<repo>/src/findajob/web/restore.py           # Tarball validation + atomic extraction with rollback — consumed by /onboarding/restore/ (#841)
<repo>/src/findajob/web/routes/settings_backup.py # GET /settings/backup/, POST /settings/backup/download — one-click backup tarball download (#841)
<repo>/src/findajob/web/routes/notifications.py # GET /notifications/, POST /notifications/{id}/read, POST /notifications/mark-all-read, GET /notifications/badge — in-app notification dashboard (#440)
<repo>/src/findajob/onboarding/parser.py    # parse interview emission into files to inject
<repo>/src/findajob/onboarding/injector.py  # atomic write + backup + Tier-1 derivation + sentinel
<repo>/src/findajob/onboarding/session_store.py # onboarding_sessions CRUD (history/captured_blocks/find_active)
<repo>/src/findajob/onboarding/interview_runner.py # thin shim around `findajob.llm.openrouter`; preserves InterviewRunnerError.user_message contract for chat-UI verbatim render (Sonnet 4.6 pinned, #471)
<repo>/src/findajob/discoverer/                # company discovery library — prompt, parser, runner, writer
<repo>/src/findajob/web/routes/healthz.py    # GET /healthz
<repo>/src/findajob/web/routes/materials.py  # GET /materials/ — candidate materials viewer; POST /materials/{fp}/files/{name} — in-browser .md editor w/ .docx auto-regen (#210); uses folder_resolver
<repo>/src/findajob/web/folder_resolver.py   # stage→filesystem resolver with path-traversal guards
<repo>/src/findajob/web/templates/           # Jinja2 templates — base.html + one subdir per route group + shared _*.html partials
<repo>/src/findajob/prep/orchestrator.py     # prep_application implementation (called by scripts/prep_application.py shim)
<repo>/src/findajob/find_contacts.py         # find_contacts implementation (called by scripts/find_contacts.py shim)
<repo>/src/findajob/critique_aggregator/      # recruiter_critic aggregator (#265) — parse, anchor (fuzzy source-line), cluster, corpus, analyze, report, pipeline

# ── Entry point scripts (called by systemd / CLI) ──────────────────────────
<repo>/scripts/triage.py                    # daily ingest → score → DB
<repo>/scripts/watchdog.py                  # resets stuck prep_in_progress jobs > 60 min (every 10 min cron)
<repo>/scripts/prep_application.py          # entry-point shim → findajob.prep.orchestrator
<repo>/scripts/find_contacts.py             # entry-point shim → findajob.find_contacts
<repo>/scripts/ingest_form.py               # Google Form → DB ingestion (retired; kept for manual drains)
<repo>/scripts/notify.py                    # ntfy push notifications — subcommands: daily-stats, health-check, apply-reminder, feedback-review, send-raw
<repo>/scripts/rename_folders.py            # rename company folders to new format (idempotent)
<repo>/scripts/discover_companies.py            # weekly company discovery cron entry
<repo>/scripts/critique_review.py            # entry-point shim → findajob.critique_aggregator.pipeline (#265); manual, writes gitignored report
<repo>/scripts/seed_runtime_config.py       # entrypoint-invoked at every container start; thin shim over findajob.config_seed.seed_runtime_config (#627)

# ── Candidate content (all gitignored — fill these in after cloning) ────────
<repo>/candidate_context/profile.md         # candidate profile — injected into scoring, resume, CL, outreach
<repo>/candidate_context/master_resume.md   # master resume — injected into prep; also indexed for REPL RAG
<repo>/candidate_context/voice_samples/     # writing samples for CL voice calibration (REPL RAG only)

# ── Config (pipeline operation — mostly gitignored) ──────────────────────────
<repo>/config/paths.env                     # binary path overrides (gitignored; see paths.env.example)
<repo>/config/roles/                        # role .md files (9 roles; podcast_scriptwriter added #870)
<repo>/config/scoring_schema.json           # JSON schema for LLM scorer output validation
<repo>/config/rapidapi_feeds.yaml            # operator-curated feed table (gitignored; see rapidapi_feeds.yaml.example)
<repo>/config/active_sources.txt           # per-stack active adapter list (gitignored; interview-emitted via 3h picker)
<repo>/config/jsearch_queries.txt          # LinkedIn/Indeed/Bing search queries (gitignored; interview-emitted, conditional on 3g 'a' selection)
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
<repo>/docs/operations/install-docker.md               # external-user Docker install + operations guide

# ── Quality ─────────────────────────────────────────────────────────────────
<repo>/pyproject.toml                       # deps, pytest, ruff, mypy config
<repo>/tests/                               # ~900 unit tests (pytest)
<repo>/.github/workflows/ci.yml            # CI: ruff + mypy + pytest on every push
```
