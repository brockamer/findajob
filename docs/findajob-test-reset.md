# Resetting `findajob-test` to factory defaults

`findajob-test` is the operator's clean dogfood instance — it exists specifically to simulate what a fresh user (a stranger who clones the repo and follows the install docs) experiences. To preserve that simulation it must be reset whenever its state diverges from "blank slate."

## When to reset

- **Before any NUX walkthrough** (`#337`-class verification, beta-tester onboarding rehearsal, `#389`-class fresh-install verification).
- **After a release that touches onboarding, schema, config layout, or entrypoint** — anything that could behave differently for a fresh install vs. a stack that already has state. v0.15+ minor releases qualify.
- **Whenever `state/.onboarding-complete` exists.** The presence of that sentinel means the stack is no longer simulating a new tester.
- **Pre-image-cut investigations.** If an external user reports a fresh-install bug, reset and reproduce.

## What the reset preserves vs. wipes

| Path | Reset behavior | Why |
|---|---|---|
| `compose.yaml` | preserved | Stack identity — operator-curated bind mounts, port, network. |
| `.env` (compose-level) | preserved | Stack identity vars: `FINDAJOB_IMAGE_TAG`, `FINDAJOB_MATERIALS_PORT`, optionally `FINDAJOB_OPERATOR_HANDLE` / `FINDAJOB_AUTH_USER`. |
| `state/candidate_context/` | contents wiped | `profile.md`, `master_resume.md`, `voice_samples/` — onboarding emits these. |
| `state/companies/` | contents wiped | Prep folders — none should exist on a fresh install. |
| `state/config/` | contents wiped | All gitignored per-stack config: `prefilter_rules.yaml`, `in_domain_patterns.yaml`, `target_companies.md`, `active_sources.txt`, `gmail.json`, etc. |
| `state/data/` | contents wiped | `pipeline.db`, `.onboarding-complete` sentinel, `.env` (per-stack credentials). |
| `state/logs/` | contents wiped | `pipeline.jsonl` and any sidecar logs. |
| `state/.backups/` | contents wiped | Onboarding backup directories. |
| State directory **structure** | preserved | Bind mounts target these directories — removing them breaks compose. |
| Ownership (`lad:lad` / 1000:1000) | preserved | Container runs as uid 1000; ownership mismatch produces silent permission failures. |

## Procedure

The reset is one `ssh <deployment-host>` command. Run from the operator's dev VM.

```bash
ssh <deployment-host> "set -e
cd /opt/stacks/findajob-test
echo '== compose down =='
sudo docker compose down

echo '== wipe state/* contents =='
for d in candidate_context companies config data logs .backups; do
  if [ -d state/\$d ]; then
    sudo find state/\$d -mindepth 1 -delete
  fi
done

echo '== recreate empty state/data/.env (compose env_file gate) =='
sudo touch state/data/.env
sudo chown lad:lad state/data/.env
sudo chmod 600 state/data/.env

echo '== pull latest + bring up =='
sudo docker compose pull
sudo docker compose up -d
"
```

### The `state/data/.env` placeholder

Compose validates `env_file` paths *before* the entrypoint runs. A truly empty
`state/data/` (no `.env`) makes `docker compose up -d` fail with
`env file ... not found`. The reset script touches an empty `.env` (chmod 600,
owned by `lad:lad`) so compose validation passes; the entrypoint then writes
real credentials when onboarding completes.

This is also the gotcha to mention in any "self-reset" doc for external testers
later — the install-docker.md guide implicitly assumes the user `cp`s
`data/.env.example` to `data/.env` during setup, which has the same effect.

## Verification

After the reset, confirm the stack lands an external user on `/onboarding/`:

```bash
ssh <deployment-host> "curl -sf -m 5 -o /dev/null -w 'GET /             : %{http_code}\n' http://localhost:8096/
curl -sf -m 5 -o /dev/null -w 'GET /board/        : %{http_code}\n' http://localhost:8096/board/dashboard
curl -sf -m 5 -o /dev/null -w 'GET /onboarding/   : %{http_code}\n' http://localhost:8096/onboarding/"
```

Expected:
- `GET /` → `307` (redirect to `/onboarding/`)
- `GET /board/` → `307` (redirect to `/onboarding/`)
- `GET /onboarding/` → `200` (Step 1 renders)

If `/` returns 200 instead of 307, the onboarding sentinel was not wiped — re-check `state/data/`.

If `/healthz` doesn't return 200 within ~10s of `compose up -d`, check container logs:
```bash
ssh <deployment-host> "sudo docker logs findajob-test-scheduler-1 2>&1 | tail -30"
```

## After the reset

`findajob-test` now behaves as a fresh install. To complete the NUX simulation:

1. Visit `http://<deployment-host>:8096/` — should redirect to `/onboarding/`.
2. Step 1: enter API keys (operator's OpenRouter / RapidAPI / Google work for testing — they're funded by the operator's accounts; document the smoke-vs-real-cost trade-off if the test will involve heavy LLM use).
3. Step 2: run the in-app interview to completion. The injector writes ~10 canonical files under `state/candidate_context/` / `state/config/` / `state/data/` and creates the sentinel.
4. Verify post-onboarding state:
   - `state/.onboarding-complete` exists
   - `/board/dashboard` now renders (no longer 307s to onboarding)
   - First triage run can be triggered manually: `sudo docker compose exec scheduler python3 scripts/triage.py`

If the test is single-pass (just verifying onboarding emission shape, not running real triage), reset again immediately after.

## Why not automate this further

A `make reset-test-stack` target was considered and rejected — the reset is destructive and benefits from the operator pasting it consciously. A `Makefile` target makes accidental wipes more likely; the manual ssh command keeps the destructive action explicit. If the cadence ever exceeds once-per-week, revisit.
