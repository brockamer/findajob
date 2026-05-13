#!/bin/sh
# ops/entrypoint.sh — runtime entry for the findajob container.
#
# Supports two deployment shapes via $JSP_BASE:
#   * Compose (default, $JSP_BASE=/app from Dockerfile ENV): six host bind
#     mounts under /app/{data,logs,companies,config,candidate_context,.backups}.
#   * Fly / k8s ($JSP_BASE=/app/state via deploy config): one volume mounted at
#     $JSP_BASE; this script materializes the six state subdirs underneath on
#     first boot. See docs/operations/fly-deploy.md.
#
# On each container start:
#   1. Create a non-root user matching PUID:PGID from env (for bind-mount
#      file ownership parity with the host).
#   2. Materialize the six state subdirs under $JSP_BASE so single-volume
#      deploys boot cleanly (no-op when the bind mounts already populated
#      them).
#   3. Seed bundled tracked config from /opt/findajob/bundled-config/ into
#      $JSP_BASE/config/. This overwrites tracked files (roles/,
#      scoring_schema.json, model_pricing.yaml, reference.docx,
#      strip-bookmarks.lua) on every start so image updates propagate.
#      Operator-personal files (OAuth creds, sheet_id, prefilter rules,
#      etc.) are left alone because they don't exist in bundled-config.
#   4. Chown writable dirs to PUID:PGID if any content doesn't match.
#   5. Drop privileges and exec the CMD (default: supercronic /app/crontab).
#
# Env:
#   PUID, PGID — host UID/GID to run as (default 1000:1000)
#   JSP_BASE   — pipeline state root (default /app from Dockerfile)
#
# Idempotent: safe to run every container start.

set -eu

PUID="${PUID:-1000}"
PGID="${PGID:-1000}"
JSP_BASE="${JSP_BASE:-/app}"

# --- 1. Create runtime user/group matching host PUID:PGID ------------------
if ! getent group findajob >/dev/null 2>&1; then
    groupadd -g "$PGID" findajob
fi

if ! id findajob >/dev/null 2>&1; then
    useradd -u "$PUID" -g "$PGID" -d /app -s /bin/sh -M findajob
fi

# --- 2. Materialize state subdirs under $JSP_BASE -------------------------
# In compose mode (bind mounts), each /app/<subdir> is already populated by
# the host and mkdir -p is a no-op. In single-volume mode, $JSP_BASE points
# at a fresh-formatted volume root and these subdirs need to exist before
# init_db / config-seed / uvicorn run.
mkdir -p \
    "$JSP_BASE/data" \
    "$JSP_BASE/logs" \
    "$JSP_BASE/companies" \
    "$JSP_BASE/config" \
    "$JSP_BASE/candidate_context" \
    "$JSP_BASE/.backups"

# --- 3. Seed bundled tracked config into $JSP_BASE/config -----------------
# Copy contents (not the directory) so tracked files land alongside any
# operator-personal files that already exist.
if [ -d /opt/findajob/bundled-config ]; then
    cp -R /opt/findajob/bundled-config/. "$JSP_BASE/config/"
fi

# --- 4. Chown writable dirs if any content doesn't match PUID:PGID --------
# Uses find to detect mismatched files/subdirs inside each dir, not just the
# top-level inode — prevents a root-owned file created by `docker exec` (as
# root) from surviving container restarts uncorrected.
for dir in \
    "$JSP_BASE/data" \
    "$JSP_BASE/logs" \
    "$JSP_BASE/companies" \
    "$JSP_BASE/config" \
    "$JSP_BASE/candidate_context" \
    "$JSP_BASE/.backups"; do
    if [ -d "$dir" ]; then
        if find "$dir" ! -user "$PUID" -print -quit 2>/dev/null | grep -q .; then
            chown -R "$PUID:$PGID" "$dir" || true
        fi
    fi
done

# --- 5. Initialize DB schema (idempotent) ---------------------------------
# CREATE TABLE IF NOT EXISTS so re-runs on populated DBs are no-ops.
# Runs as $PUID:$PGID so the resulting pipeline.db is owned correctly.
# init_db.py reads findajob.paths.BASE which honors JSP_BASE, so the DB
# lands at $JSP_BASE/data/pipeline.db in both layouts.
if [ -w "$JSP_BASE/data" ]; then
    gosu "$PUID:$PGID" python3 /app/scripts/init_db.py >/dev/null
fi

# --- 6. Seed runtime config from .example variants (#627) -----------------
# Materializes the small set of gitignored config files whose absence
# causes a hard 500 in a code path (currently: rapidapi_feeds.yaml read
# by /onboarding/feed-config/). Idempotent: existing live files are
# never overwritten, so operator edits survive restarts.
gosu "$PUID:$PGID" python3 /app/scripts/seed_runtime_config.py >/dev/null

# --- 7. Render supercronic crontab from ops/scheduled-jobs.yaml (#344) ----
# YAML at /app/scheduled-jobs.yaml is the source of truth. Per-job env-var
# overrides (FINDAJOB_<JOB>_SCHEDULE / _ENABLED) let multi-tenant hosts
# stagger schedules without forking the YAML. Fail-fast: a malformed YAML
# or unrecognized override exits non-zero so the container restart loop
# surfaces the problem loudly instead of silently falling back.
# Crontab is image-internal (not state), so the path stays at /app/crontab
# regardless of JSP_BASE.
python3 /app/scripts/render_crontab.py \
    --input /app/scheduled-jobs.yaml \
    --output /app/crontab
chmod 0644 /app/crontab

# --- 8. Launch materials viewer (uvicorn) in background -------------------
# Supercronic stays PID 1 for compose restart tracking. Uvicorn runs as a
# child process. If it crashes, supercronic keeps running — /healthz is the
# outside signal. Operator restarts the container if needed.
gosu "$PUID:$PGID" python3 -m uvicorn findajob.web.app:default_app --factory --host 0.0.0.0 --port 8090 --log-level info --proxy-headers --forwarded-allow-ips='*' &
UVICORN_PID=$!

# Forward SIGTERM / SIGINT to uvicorn so docker compose down shuts it down cleanly.
trap 'kill -TERM "$UVICORN_PID" 2>/dev/null; exit 0' TERM INT

# --- 9. Drop privileges and exec the command ------------------------------
exec gosu "$PUID:$PGID" "$@"
