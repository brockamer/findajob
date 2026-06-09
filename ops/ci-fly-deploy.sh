#!/usr/bin/env bash
# ops/ci-fly-deploy.sh — CI auto-deploy of a tagged findajob image to the
# maintainer's Fly apps, with the mandated post-deploy auth-gate verify per app.
#
# Invoked by the `deploy-fly` job in .github/workflows/build-image.yml on
# `v*.*.*` tag pushes (#914). Environment inputs:
#
#   FLY_DEPLOY_APPS  whitespace/newline-separated Fly app slugs.
#                    UNSET or empty/whitespace-only => clean no-op (exit 0).
#                    This is the fork-safe default and the per-app opt-in.
#   IMAGE            fully-qualified image ref to deploy
#                    (e.g. ghcr.io/<owner>/findajob:v1.2.3). The image is public
#                    on GHCR, so Fly needs no registry credentials to pull it.
#   FLY_API_TOKEN    Fly token used by flyctl (read from the environment).
#                    Must be ORG-scoped: an app-scoped deploy token reaches only
#                    one app and cannot run `fly ssh console`, which the verify
#                    step below requires. Never printed by this script.
#   FLY_VERIFY_SETTLE_SECONDS  seconds to wait after deploy before the ssh-based
#                    verify, covering the ssh-tunnel bring-up window (default 5).
#
# Per app: `fly deploy -a <slug> --image $IMAGE --yes` (deploys the prebuilt
# image against the app's server-side config — no local fly.toml, since per-app
# configs are gitignored), then the CLAUDE.md auth gate
# `fly ssh console -a <slug> --command "python -m findajob.web.verify_auth"`.
#
# NOT fail-fast: every app is attempted even if an earlier one fails. The script
# exits non-zero if ANY app's deploy or verify failed — the red GitHub Actions
# run is the failure signal (no ntfy). Deliberately NO `set -e`: per-app failure
# is handled explicitly so one failure can't abort the remaining apps.

set -uo pipefail

# --- Resolve the app list (unset/empty/whitespace-only => clean no-op) --------

APPS_RAW="${FLY_DEPLOY_APPS:-}"
if [[ -z "${APPS_RAW//[[:space:]]/}" ]]; then
    echo "FLY_DEPLOY_APPS is unset or empty — nothing to deploy (clean no-op)."
    exit 0
fi

# --- Preflight ---------------------------------------------------------------

if ! command -v fly >/dev/null 2>&1; then
    echo "ERROR: flyctl ('fly') not found in PATH." >&2
    exit 1
fi
if [[ -z "${FLY_API_TOKEN:-}" ]]; then
    echo "ERROR: FLY_DEPLOY_APPS is set but FLY_API_TOKEN is empty." >&2
    exit 1
fi
if [[ -z "${IMAGE:-}" ]]; then
    echo "ERROR: IMAGE is empty — nothing to deploy." >&2
    exit 1
fi

SETTLE="${FLY_VERIFY_SETTLE_SECONDS:-5}"

# --- Deploy + verify each app (no fail-fast) ---------------------------------

ok_count=0
declare -a failures=()

# Disable globbing for the loops so a stray glob char in the app list can't
# expand; unquoted word-splitting on IFS (space/tab/newline) yields the slugs.
set -f

# Register every app slug as a GitHub Actions log-mask token BEFORE any output.
# App slugs are deployment topology; this repo is public, so the Actions log is
# world-readable. GitHub masks the whole FLY_DEPLOY_APPS secret as one token,
# which never matches the individual slugs we echo (or that flyctl prints), so
# without this they'd appear in cleartext when the secret is space-separated.
# `::add-mask::<value>` redacts every later occurrence regardless of line format;
# it's an inert line outside GitHub Actions (e.g. under test).
for app in $APPS_RAW; do
    echo "::add-mask::${app}"
done

for app in $APPS_RAW; do
    echo "==> Deploying ${IMAGE} to ${app} ..."
    if ! fly deploy -a "$app" --image "$IMAGE" --yes; then
        echo "ERROR: deploy failed for ${app}." >&2
        failures+=("${app} (deploy)")
        continue
    fi

    echo "==> Verifying auth gate on ${app} ..."
    sleep "$SETTLE"
    if ! fly ssh console -a "$app" --command "python -m findajob.web.verify_auth"; then
        echo "ERROR: auth-gate verify failed for ${app} — deploy is up but UNVERIFIED." >&2
        echo "       If this is a permission error, FLY_API_TOKEN must be an org-scoped" >&2
        echo "       token: app-scoped deploy tokens cannot run 'fly ssh console'." >&2
        failures+=("${app} (verify)")
        continue
    fi

    echo "==> ${app}: deployed and verified."
    ok_count=$((ok_count + 1))
done
set +f

# --- Summary -----------------------------------------------------------------

echo
echo "==> Deploy summary: ${ok_count} ok, ${#failures[@]} failed."
if (( ${#failures[@]} > 0 )); then
    for f in "${failures[@]}"; do
        echo "    FAILED ${f}" >&2
    done
    exit 1
fi
echo "All ${ok_count} app(s) deployed and verified."
