# Data Ownership

Audit anchor — classifies persisted state by ownership and recoverability. Update the "Backup-critical?" column when backup work (#426) lands and the nightly tarball's contents are settled.

| Path | Source | Backup-critical? | Rebuildable if lost? |
|---|---|---|---|
| `data/pipeline.db` | Pipeline-generated; operator-curated via stage transitions, notes, score corrections | **Yes** | **No** — fetcher results from past dates aren't retrievable; transitions are operator decisions |
| `candidate_context/profile.md`, `master_resume.md`, `voice_samples/` | Operator-authored | **Yes** | **No** — re-interview loses weeks of hand-tuning |
| `candidate_context/discovered_companies.{md,json}` | Pipeline-generated (weekly cron) | No | **Yes** — next Sunday discoverer run reproduces |
| `config/` (operator-curated subset: `target_companies.md`, `prefilter_rules.yaml`, `excluded_employers.yaml`, `feed_urls.txt`, `jsearch_queries.txt`, `target_locations.txt`, `feedback_weights.yaml`, `gmail.json`, `gsheets_creds.json`, etc.) | Operator-curated (interview-emitted seed + accumulated edits) | **Yes** | **No** — re-interview emits ~half; hand-curation gone |
| `config/gmail_state.json` | Pipeline-generated (IMAP UID checkpoint) | No | **Yes** — re-syncs on next poll |
| `config/roles/`, `config/scoring_schema.json`, `config/model_pricing.yaml`, `config/reference.docx`, `config/strip-bookmarks.lua` | Repo-baked (in image, not bind-mount) | No | **Yes** — `docker compose pull` restores |
| `data/.env` | Operator-curated (API keys, NTFY_TOPIC) | **Yes** | **No** — rotation-grade pain to re-collect |
| `data/.onboarding-complete` | Pipeline-generated sentinel | No | **Yes** — re-emit on next interview |
| `data/connections.csv` | Operator-uploaded (LinkedIn export) | No | **Yes** — re-export from LinkedIn (minutes) |
| `companies/` (active + `_applied/` + `_waitlisted/` + `_rejected/` + `.stale/`) | Pipeline-generated | Selective (skip `.stale/`) | **Partially** — re-runnable per-job, but stale JD URLs no longer reachable |
| `logs/pipeline.jsonl` | Pipeline-generated | No (observability, not state) | **No** — historical observability lost if dropped |
| `logs/{form-ingest,jobsync,poller,triage,notify,ci-check,rescore_backfill}.log` | Legacy / pipeline-generated | No | **Yes** — mostly stale; safe to drop |

The data layer is the only thing `docker compose pull` + a fresh interview can't regenerate. Deep reference: `docs/superpowers/specs/2026-05-03-301-data-model-audit.md` §1 (operator-private).
