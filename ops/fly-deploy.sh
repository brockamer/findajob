#!/usr/bin/env bash
# ops/fly-deploy.sh — idempotent Fly.io deploy wrapper for a single findajob tenant.
#
# Reads ops/fly.toml for app name + region, creates the app + six volumes
# if absent, prompts only for missing secrets, runs `fly deploy`, then
# verifies the basic-auth gate from inside the running machine via
# `fly ssh console --command "python -m findajob.web.verify_auth"`
# (CLAUDE.md "Auth Gate Must Be Verified Post-Deploy").
#
# Re-runs are safe: existing apps, volumes, and secrets are detected and
# skipped. Runbook: docs/operations/fly-deploy.md.

set -euo pipefail

FLY_TOML="${FLY_TOML:-ops/fly.toml}"

# --- Preflight ------------------------------------------------------------

if ! command -v fly >/dev/null 2>&1; then
    echo "ERROR: flyctl ('fly') not found in PATH." >&2
    echo "Install: curl -L https://fly.io/install.sh | sh" >&2
    exit 1
fi

if ! fly auth whoami >/dev/null 2>&1; then
    echo "ERROR: not logged into Fly. Run: fly auth login" >&2
    exit 1
fi

if [ ! -f "$FLY_TOML" ]; then
    echo "ERROR: $FLY_TOML not found." >&2
    echo "Copy the template:  cp ops/fly.toml.example $FLY_TOML  then edit 'app'." >&2
    exit 1
fi

APP="$(grep -E '^app[[:space:]]*=' "$FLY_TOML" | head -1 | sed -E 's/^app[[:space:]]*=[[:space:]]*"([^"]+)".*/\1/')"
REGION="$(grep -E '^primary_region[[:space:]]*=' "$FLY_TOML" | head -1 | sed -E 's/^primary_region[[:space:]]*=[[:space:]]*"([^"]+)".*/\1/')"

if [ -z "$APP" ] || [ "$APP" = "REPLACE_WITH_FLY_APP_NAME" ]; then
    echo "ERROR: 'app' in $FLY_TOML is missing or still the placeholder." >&2
    echo "Edit $FLY_TOML and set app = \"findajob-<handle>\"." >&2
    exit 1
fi
if [ -z "$REGION" ]; then
    echo "ERROR: 'primary_region' not found in $FLY_TOML." >&2
    exit 1
fi

echo "==> Fly app: $APP  (region: $REGION)"

# --- 1. App ---------------------------------------------------------------

if fly status --app "$APP" >/dev/null 2>&1; then
    echo "==> App '$APP' exists, skipping create."
else
    echo "==> Creating Fly app '$APP'..."
    fly apps create "$APP"
fi

# --- 2. Volumes (idempotent) ----------------------------------------------

existing_volumes="$(fly volumes list --app "$APP" 2>/dev/null || true)"

create_volume_if_missing() {
    local name="$1"
    local size_gb="$2"
    if printf '%s\n' "$existing_volumes" | grep -qwF "$name"; then
        echo "    skip   $name (exists)"
        return
    fi
    echo "    create $name (${size_gb}gb)"
    fly volumes create "$name" --app "$APP" --region "$REGION" --size "$size_gb" --yes >/dev/null
}

echo "==> Provisioning volume..."
# Fly machines support exactly one volume per machine. The findajob image
# materializes the six state subdirs (data/logs/companies/config/
# candidate_context/.backups) under $JSP_BASE inside this single volume on
# first boot — see ops/entrypoint.sh and docs/operations/fly-deploy.md.
# 8 GB = sum of the per-subdir defaults in the compose layout (1+1+3+1+1+1).
create_volume_if_missing findajob_state 8

# --- 3. Secrets (skip already-set; --stage applies on next deploy) --------

existing_secrets="$(fly secrets list --app "$APP" 2>/dev/null || true)"

# fly secrets list output has a header row plus one row per secret:
#
#     NAME                 │ DIGEST           │ STATUS
#   * FINDAJOB_AUTH_PASS   │ 1c8f32f25a63db4a │ Staged
#     SOMETHING_DEPLOYED   │ abcd1234         │ Set
#
# The leading `*` marks staged-but-not-deployed secrets. The sed strips
# leading whitespace + the optional `* ` marker so awk's first column is
# always the name. tail skips the header without depending on awk's NR.
has_secret() {
    printf '%s\n' "$existing_secrets" \
        | tail -n +2 \
        | sed -E 's/^[[:space:]]*\*?[[:space:]]+//' \
        | awk '{print $1}' \
        | grep -qxF "$1"
}

# prompt_secret <NAME> <prompt-label> [default] [sensitive=1|0] [optional=1|0]
prompt_secret() {
    local name="$1"
    local label="$2"
    local default="${3:-}"
    local sensitive="${4:-0}"
    local optional="${5:-0}"
    if has_secret "$name"; then
        echo "    skip   $name (already set — rotate with: fly secrets set $name=... --app $APP)"
        return
    fi
    local val=""
    local prompt_suffix=""
    if [ -n "$default" ]; then
        prompt_suffix=" [default: $default]"
    elif [ "$optional" = "1" ]; then
        prompt_suffix=" (optional — press Enter to skip)"
    fi
    if [ "$sensitive" = "1" ]; then
        printf "    %s%s: " "$label" "$prompt_suffix" >&2
        read -r -s val
        printf "\n" >&2
    else
        printf "    %s%s: " "$label" "$prompt_suffix" >&2
        read -r val
        [ -t 0 ] || printf "\n" >&2
    fi
    if [ -z "$val" ] && [ -n "$default" ]; then
        val="$default"
    fi
    if [ -z "$val" ]; then
        if [ "$optional" = "1" ]; then
            echo "    skip   $name (not provided)"
            return
        fi
        echo "ERROR: $name is required and was empty." >&2
        exit 1
    fi
    fly secrets set --stage --app "$APP" "$name=$val" >/dev/null
    echo "    set    $name (staged for next deploy)"
}

echo "==> Configuring secrets (already-set values skipped)..."
prompt_secret OPENROUTER_API_KEY "OpenRouter API key"                  ""                          1
prompt_secret RAPIDAPI_KEY       "RapidAPI key"                        ""                          1  1
prompt_secret NTFY_TOPIC         "ntfy topic"                          ""                          0  1
prompt_secret FINDAJOB_AUTH_USER "Basic-auth username"                 ""                          0
prompt_secret FINDAJOB_AUTH_PASS "Basic-auth password (>= 24 chars)"   ""                          1
# FINDAJOB_WEB_URL is auto-derived from FLY_APP_NAME at runtime; only
# prompt when the operator explicitly wants a custom domain.
prompt_secret FINDAJOB_WEB_URL   "Public web URL (auto-derived if skipped)" ""                      0  1

# --- 4. Deploy ------------------------------------------------------------

echo "==> Deploying..."
fly deploy --config "$FLY_TOML"

# --- 5. Verify auth gate from inside the machine --------------------------
# `fly deploy` returns only after the http_service health check passes, so
# uvicorn is bound by the time we get here. Brief sleep covers the ssh-console
# tunnel-bring-up window. Exit codes from verify_auth: 2 = AUTH env vars not
# set; 3 = anonymous probe didn't get 401+Basic; 4 = authed probe didn't get
# 200; 5 = network/exception (rare on Fly post-deploy).

echo "==> Verifying auth gate..."
sleep 5
if ! fly ssh console --app "$APP" --command "python -m findajob.web.verify_auth"; then
    echo >&2
    echo "ERROR: auth gate verification failed. The deploy is up but UNVERIFIED." >&2
    echo "Diagnose with:" >&2
    echo "    fly logs --app $APP" >&2
    echo "    fly status --app $APP" >&2
    echo "    fly ssh console --app $APP" >&2
    exit 1
fi

cat <<MSG

==> Done.
    Public URL:   https://$APP.fly.dev/
    Auth:         FINDAJOB_AUTH_USER / FINDAJOB_AUTH_PASS as set above
    Re-verify:    fly ssh console --app $APP --command "python -m findajob.web.verify_auth"
    Tail logs:    fly logs --app $APP
    Shell in:     fly ssh console --app $APP

MSG
