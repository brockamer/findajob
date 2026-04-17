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

# --- 3. Chown writable dirs if ownership doesn't already match -----------
for dir in /app/data /app/logs /app/companies /app/config /app/candidate_context /root/.config/aichat_ng; do
    if [ -d "$dir" ]; then
        current_owner="$(stat -c %u "$dir" 2>/dev/null || echo 0)"
        if [ "$current_owner" != "$PUID" ]; then
            chown -R "$PUID:$PGID" "$dir" || true
        fi
    fi
done

# --- 4. Drop privileges and exec the command ------------------------------
exec gosu "$PUID:$PGID" "$@"
