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
#   3b. Assert aichat-ng config.yaml is readable by the runtime user — exits
#      with a clear diagnostic if missing (e.g., HOME not set in compose.yaml).
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
        # aichat-ng does not perform ${VAR} substitution at load time — inject keys now.
        # Per #67 (post-OpenRouter cutover) the only direct clients in the
        # template are openrouter + gemini-embed; openai / groq / xai placeholders
        # were retired with their client blocks.
        for _var in OPENROUTER_API_KEY GOOGLE_API_KEY; do
            eval "_val=\"\${${_var}:-}\""
            sed -i "s|\${${_var}}|${_val}|g" "$AICHAT_CFG_DIR/config.yaml"
        done
        unset _var _val
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

# --- 3b. Assert aichat-ng config is readable before scheduler starts ------
# Fails fast if config.yaml is missing or unreadable by the runtime user.
# Most common cause: HOME not set to /app in compose.yaml, so the seeding
# in step 2b wrote to /root/.config/aichat_ng/ instead of the bind-mount.
if ! gosu "$PUID:$PGID" test -r "$AICHAT_CFG_DIR/config.yaml" 2>/dev/null; then
    echo "FATAL: aichat-ng config not found or not readable at $AICHAT_CFG_DIR/config.yaml (UID $PUID)" >&2
    echo "  HOME=$HOME  AICHAT_CFG_DIR=$AICHAT_CFG_DIR" >&2
    echo "  Ensure HOME: /app is set in compose.yaml environment:" >&2
    echo "    environment:" >&2
    echo "      HOME: /app" >&2
    exit 1
fi

# --- 3c. Initialize DB schema (idempotent) --------------------------------
# CREATE TABLE IF NOT EXISTS so re-runs on populated DBs are no-ops.
# Runs as $PUID:$PGID so the resulting pipeline.db is owned correctly.
if [ -w /app/data ]; then
    gosu "$PUID:$PGID" python3 /app/scripts/init_db.py >/dev/null
fi

# --- 3d. Render supercronic crontab from ops/scheduled-jobs.yaml (#344) ---
# YAML at /app/scheduled-jobs.yaml is the source of truth. Per-job env-var
# overrides (FINDAJOB_<JOB>_SCHEDULE / _ENABLED) let multi-tenant hosts
# stagger schedules without forking the YAML. Fail-fast: a malformed YAML
# or unrecognized override exits non-zero so the container restart loop
# surfaces the problem loudly instead of silently falling back.
python3 /app/scripts/render_crontab.py \
    --input /app/scheduled-jobs.yaml \
    --output /app/crontab
chmod 0644 /app/crontab

# --- 4. Launch materials viewer (uvicorn) in background -------------------
# Supercronic stays PID 1 for compose restart tracking. Uvicorn runs as a
# child process. If it crashes, supercronic keeps running — /healthz is the
# outside signal. Operator restarts the container if needed.
gosu "$PUID:$PGID" python3 -m uvicorn findajob.web.app:default_app --factory --host 0.0.0.0 --port 8090 --log-level info --proxy-headers --forwarded-allow-ips='*' &
UVICORN_PID=$!

# Forward SIGTERM / SIGINT to uvicorn so docker compose down shuts it down cleanly.
trap 'kill -TERM "$UVICORN_PID" 2>/dev/null; exit 0' TERM INT

# --- 5. Drop privileges and exec the command ------------------------------
exec gosu "$PUID:$PGID" "$@"
