#!/bin/sh
# ops/entrypoint.sh — runtime entry for the findajob container.
#
# On each container start:
#   1. Create a non-root user matching PUID:PGID from env (for bind-mount
#      file ownership parity with the host).
#   2. Seed bundled tracked config from /opt/findajob/bundled-config/ into
#      /app/config/ (the bind-mount root). This overwrites tracked files
#      (roles/, scoring_schema.json, model_pricing.yaml, reference.docx,
#      strip-bookmarks.lua) on every start so image updates propagate on
#      `docker compose up`. Operator-personal files (OAuth creds, sheet_id,
#      prefilter rules, etc.) are left alone because they don't exist in
#      bundled-config.
#   3. Chown bind-mounted writable dirs to findajob:findajob if they're
#      not already owned correctly.
#   4. Drop privileges and exec the CMD (default: supercronic /app/crontab).
#
# Env:
#   PUID, PGID — host UID/GID to run as (default 1000:1000)
#
# Idempotent: safe to run every container start.

set -eu

PUID="${PUID:-1000}"
PGID="${PGID:-1000}"

# --- 1. Create runtime user/group matching host PUID:PGID ------------------
if ! getent group findajob >/dev/null 2>&1; then
    groupadd -g "$PGID" findajob
fi

if ! id findajob >/dev/null 2>&1; then
    useradd -u "$PUID" -g "$PGID" -d /app -s /bin/sh -M findajob
fi

# --- 2. Seed bundled tracked config into the bind-mounted /app/config -----
# The bind-mount at /app/config would otherwise shadow the baked-in tracked
# config. Copy contents (not the directory) so tracked files land alongside
# any operator-personal files that already exist.
mkdir -p /app/config
if [ -d /opt/findajob/bundled-config ]; then
    cp -R /opt/findajob/bundled-config/. /app/config/
fi

# --- 2b. Seed bundled aichat-ng config if missing -------------------------
# models-override.yaml — always seeded if missing (owned by the project, not
# the operator; catalog drift from the bundled version breaks claude:*
# thinking modes silently).
# config.yaml — seeded from config.yaml.example only if the destination is
# absent. Operator customizations (custom model, added clients, REPL prefs)
# survive container restarts; re-seeding would clobber them.
# roles symlink — points at /app/config/roles (seeded by the image via
# bundled-config, see section 2 above). Created only if not already present
# as a symlink or real dir, so operators can override with their own dir.
AICHAT_CFG_DIR="${HOME:-/root}/.config/aichat_ng"
mkdir -p "$AICHAT_CFG_DIR"

if [ -d /opt/findajob/bundled-aichat ]; then
    if [ ! -f "$AICHAT_CFG_DIR/models-override.yaml" ] && [ -f /opt/findajob/bundled-aichat/models-override.yaml ]; then
        cp /opt/findajob/bundled-aichat/models-override.yaml "$AICHAT_CFG_DIR/models-override.yaml"
    fi
    if [ ! -f "$AICHAT_CFG_DIR/config.yaml" ] && [ -f /opt/findajob/bundled-aichat/config.yaml.example ]; then
        cp /opt/findajob/bundled-aichat/config.yaml.example "$AICHAT_CFG_DIR/config.yaml"
    fi
fi

if [ ! -e "$AICHAT_CFG_DIR/roles" ]; then
    ln -s /app/config/roles "$AICHAT_CFG_DIR/roles"
fi

# --- 3. Chown writable dirs if any content doesn't match PUID:PGID --------
# Uses find to detect mismatched files/subdirs inside each dir, not just the
# top-level inode — prevents a root-owned file created by `docker exec` (as
# root) from surviving container restarts uncorrected.
for dir in /app/data /app/logs /app/companies /app/config /app/candidate_context "$AICHAT_CFG_DIR"; do
    if [ -d "$dir" ]; then
        if find "$dir" ! -user "$PUID" -print -quit 2>/dev/null | grep -q .; then
            chown -R "$PUID:$PGID" "$dir" || true
        fi
    fi
done

# --- 3b. Initialize DB schema (idempotent) --------------------------------
# CREATE TABLE IF NOT EXISTS so re-runs on populated DBs are no-ops.
# Runs as $PUID:$PGID so the resulting pipeline.db is owned correctly.
if [ -w /app/data ]; then
    gosu "$PUID:$PGID" python3 /app/scripts/init_db.py >/dev/null
fi

# --- 4. Launch materials viewer (uvicorn) in background -------------------
# Supercronic stays PID 1 for compose restart tracking. Uvicorn runs as a
# child process. If it crashes, supercronic keeps running — /healthz is the
# outside signal. Operator restarts the container if needed.
gosu "$PUID:$PGID" python3 -m uvicorn findajob.web.app:default_app --factory --host 0.0.0.0 --port 8090 --log-level info &
UVICORN_PID=$!

# Forward SIGTERM / SIGINT to uvicorn so docker compose down shuts it down cleanly.
trap 'kill -TERM "$UVICORN_PID" 2>/dev/null; exit 0' TERM INT

# --- 5. Drop privileges and exec the command ------------------------------
exec gosu "$PUID:$PGID" "$@"
