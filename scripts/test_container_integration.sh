#!/bin/bash
# scripts/test_container_integration.sh
#
# v0.1.1+: Fresh-install smoke test for the findajob container image.
#
# Spins up a throwaway stack with EMPTY bind mounts, provides the minimum
# realistic input (live API keys + one fixture candidate profile), runs the
# full triage-to-pipeline_complete cycle, and asserts a scored job lands in
# the DB. Proves a user can go from "clone + configure" to a working pipeline.
#
# Recommended pre-tag check. Run from any docker-equipped host before
# cutting a release tag. See docs/maintainers/release-process.md.
#
# Prereqs:
# - docker + docker compose v2
# - findajob image available locally as ${FINDAJOB_TEST_IMAGE:-findajob:local}
#   (build with `docker build -t findajob:local .` from the repo root first)
# - A real data/.env with live API keys — either at
#   $HOME/.findajob/state/data/.env or ./data/.env
#
# Usage:
#   FINDAJOB_TEST_IMAGE=findajob:local scripts/test_container_integration.sh
#
# Expected runtime: 2–5 minutes (dominated by ~20 LLM scoring calls over
# the real network). API budget: ≤$0.10 per run.

set -euo pipefail

# ────────────────────────────────────────────────────────────────────────────
# 0. PUID/PGID guard — refuse to run as root
# ────────────────────────────────────────────────────────────────────────────
#
# The compose snippet below shells out $(id -u):$(id -g) at script-run time.
# Under `sudo` (or `sudo -E`), those resolve to 0:0, the container entrypoint
# tries `groupadd -g 0 lad`, collides with the existing root GID, and
# supercronic never starts (manifests as a 60s startup timeout downstream).
# Fail fast here with a clear diagnostic instead.

if [ "$(id -u)" -eq 0 ] || [ "$(id -g)" -eq 0 ]; then
    cat >&2 <<'EOM'
ERROR: this smoke script must NOT be run as root (uid=0 / gid=0).

The compose file embeds $(id -u):$(id -g) as PUID/PGID. Running under sudo
collapses both to 0, which collides with the container's root GID and
prevents the entrypoint from creating the unprivileged 'lad' user.

Re-invoke as your normal docker-group user, without sudo:

    scripts/test_container_integration.sh

If your account is not in the 'docker' group, add it once with
`sudo usermod -aG docker $USER` and re-login — don't sudo this script.
EOM
    exit 2
fi

# ────────────────────────────────────────────────────────────────────────────
# 1. Resolve repo root + fixture paths
# ────────────────────────────────────────────────────────────────────────────

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FIXTURES="$REPO_ROOT/tests/fixtures"

for f in smoke_profile.md smoke_jsearch_queries.txt smoke_prefilter_rules.yaml \
         smoke_in_domain_patterns.yaml smoke_target_companies.md; do
    if [ ! -f "$FIXTURES/$f" ]; then
        echo "ERROR: missing fixture $FIXTURES/$f" >&2
        exit 2
    fi
done

# ────────────────────────────────────────────────────────────────────────────
# 2. Resolve env + config inputs
# ────────────────────────────────────────────────────────────────────────────

IMAGE="${FINDAJOB_TEST_IMAGE:-findajob:local}"

# Source .env either from the standard install location or from the
# working copy — same fallback the old script used.
SRC_ENV=""
if [ -f "$HOME/.findajob/state/data/.env" ]; then
    SRC_ENV="$HOME/.findajob/state/data/.env"
elif [ -f "$REPO_ROOT/data/.env" ]; then
    SRC_ENV="$REPO_ROOT/data/.env"
else
    echo "ERROR: need data/.env with live API keys — either $HOME/.findajob/state/data/.env or $REPO_ROOT/data/.env" >&2
    exit 2
fi

# ────────────────────────────────────────────────────────────────────────────
# 3. Lay down scratch stack dir with empty bind mounts
# ────────────────────────────────────────────────────────────────────────────

SCRATCH="$(mktemp -d -t findajob-smoke-XXXXXX)"
cleanup() {
    echo "[cleanup] tearing down stack at $SCRATCH"
    if [ -d "$SCRATCH" ] && [ -f "$SCRATCH/compose.yaml" ]; then
        (cd "$SCRATCH" && docker compose down -v 2>/dev/null || true)
    fi
    # Roles files seeded by the container may be owned by root; re-own before rm.
    sudo chown -R "$(id -u):$(id -g)" "$SCRATCH" 2>/dev/null || true
    rm -rf "$SCRATCH"
}
trap cleanup EXIT

echo "[setup] scratch dir: $SCRATCH  image: $IMAGE"

mkdir -p "$SCRATCH/state"/{data,config,candidate_context,companies,logs}

# ────────────────────────────────────────────────────────────────────────────
# 4. Seed inputs into the bind mounts
# ────────────────────────────────────────────────────────────────────────────

# API keys → state/data/.env (copied from live install)
cp "$SRC_ENV" "$SCRATCH/state/data/.env"
chmod 600 "$SCRATCH/state/data/.env"

# Strip perimeter-auth env vars — the throwaway stack must be reachable
# without HTTP Basic Auth for the /materials/ assertion below. Real stacks
# gate /materials/ behind FINDAJOB_AUTH_USER/PASS; the smoke doesn't run
# `verify_auth` (that's a per-stack post-deploy step), so it's safe to
# drop these here. Idempotent — no-op if the source .env didn't have them.
sed -i '/^FINDAJOB_AUTH_USER=/d; /^FINDAJOB_AUTH_PASS=/d' "$SCRATCH/state/data/.env"

# Candidate profile → state/candidate_context/profile.md
cp "$FIXTURES/smoke_profile.md" "$SCRATCH/state/candidate_context/profile.md"

# Scorer config files → state/config/
cp "$FIXTURES/smoke_jsearch_queries.txt"       "$SCRATCH/state/config/jsearch_queries.txt"
cp "$FIXTURES/smoke_prefilter_rules.yaml"      "$SCRATCH/state/config/prefilter_rules.yaml"
cp "$FIXTURES/smoke_in_domain_patterns.yaml"   "$SCRATCH/state/config/in_domain_patterns.yaml"
cp "$FIXTURES/smoke_target_companies.md"      "$SCRATCH/state/config/target_companies.md"

# Empty feed_urls.txt — smoke test drives jobs via RapidAPI queries only
: > "$SCRATCH/state/config/feed_urls.txt"

# Active-sources allow-list — must be explicit post-#681. With the sentinel
# pre-marked below, an absent active_sources.txt would resolve to "user picked
# none in onboarding" ([]), not to the 7-adapter default; the smoke would
# score zero jobs and fail. Seed the RapidAPI-driven trio that the smoke's
# .env + jsearch_queries.txt actually exercises (greenhouse/ashby/lever are
# orthogonal because feed_urls.txt is empty above; gmail_linkedin needs IMAP
# config we don't supply).
cat > "$SCRATCH/state/config/active_sources.txt" <<EOF
# Seeded by scripts/test_container_integration.sh — fresh-install smoke.
jobs-api14
jobs-api14-indeed
jsearch
EOF

# Pre-mark onboarding complete (#148) so /board/, /materials/, /stats/ don't
# 307-redirect to /onboarding/. The smoke seeds all seven config files by hand
# above; the interview-driven onboarding path is tested elsewhere.
: > "$SCRATCH/state/data/.onboarding-complete"

# ────────────────────────────────────────────────────────────────────────────
# 5. Write compose.yaml — mirrors ops/compose.yaml.example, overrides image
# ────────────────────────────────────────────────────────────────────────────

cat > "$SCRATCH/compose.yaml" <<EOF
services:
  scheduler:
    image: ${IMAGE}
    env_file: ./state/data/.env
    environment:
      TZ: America/Los_Angeles
      PUID: $(id -u)
      PGID: $(id -g)
      HOME: /app
      JSP_BASE: /app
      FINDAJOB_JOBSYNC_ENABLED: "false"
      FINDAJOB_TRIAGE_TIMEOUT: "7200"
    volumes:
      - ./state/data:/app/data
      - ./state/config:/app/config
      - ./state/candidate_context:/app/candidate_context
      - ./state/companies:/app/companies
      - ./state/logs:/app/logs
    ports:
      - "${TEST_MATERIALS_PORT:-18090}:8090"
EOF

# ────────────────────────────────────────────────────────────────────────────
# 6. Bring up the stack and wait for supercronic to load cleanly
# ────────────────────────────────────────────────────────────────────────────

echo "[start] docker compose up -d"
(cd "$SCRATCH" && docker compose up -d)

EXEC="docker compose -f $SCRATCH/compose.yaml exec -T scheduler"

echo "[wait] supercronic read crontab"
DEADLINE=$(( $(date +%s) + 60 ))
READ_CRONTAB=0
while [ "$(date +%s)" -lt "$DEADLINE" ]; do
    if (cd "$SCRATCH" && docker compose logs scheduler 2>&1) | grep -q "read crontab"; then
        READ_CRONTAB=1
        break
    fi
    sleep 2
done

if [ "$READ_CRONTAB" -ne 1 ]; then
    echo "ERROR: supercronic did not log 'read crontab' within 60s" >&2
    (cd "$SCRATCH" && docker compose logs scheduler | tail -80) >&2
    exit 1
fi

# ────────────────────────────────────────────────────────────────────────────
# 7. Run triage.py in the foreground (tees stdout)
# ────────────────────────────────────────────────────────────────────────────

echo "[run] triage.py"
set +e
$EXEC python3 /app/scripts/triage.py
TRIAGE_EXIT=$?
set -e

if [ "$TRIAGE_EXIT" -ne 0 ]; then
    echo "ERROR: triage.py exited non-zero ($TRIAGE_EXIT)" >&2
    exit 1
fi

# ────────────────────────────────────────────────────────────────────────────
# 8. Assert pipeline_complete event with scored > 0 in pipeline.jsonl
# ────────────────────────────────────────────────────────────────────────────

echo "[assert] pipeline_complete event with scored > 0"
JSONL_SCORED=$($EXEC python3 -c "
import json, sys
scored = 0
found = False
try:
    with open('/app/logs/pipeline.jsonl') as fh:
        for line in fh:
            try:
                ev = json.loads(line)
            except Exception:
                continue
            if ev.get('event') == 'pipeline_complete':
                found = True
                scored = int(ev.get('scored', 0))
except FileNotFoundError:
    print('ERROR: /app/logs/pipeline.jsonl not found', file=sys.stderr)
    sys.exit(1)
if not found:
    print('ERROR: no pipeline_complete event in pipeline.jsonl', file=sys.stderr)
    sys.exit(1)
print(scored)
") || { echo "ERROR: pipeline.jsonl check failed" >&2; exit 1; }

if [ "$JSONL_SCORED" -lt 1 ]; then
    echo "ERROR: pipeline_complete event had scored=$JSONL_SCORED (expected >= 1)" >&2
    exit 1
fi
echo "  pipeline_complete.scored = $JSONL_SCORED"

# ────────────────────────────────────────────────────────────────────────────
# 9. Assert jobs table has rows in stage scored or manual_review
# ────────────────────────────────────────────────────────────────────────────

echo "[assert] jobs table has scored/manual_review rows"
JOB_COUNT=$($EXEC sqlite3 /app/data/pipeline.db \
    "SELECT COUNT(*) FROM jobs WHERE stage IN ('scored','manual_review');" | tr -d '[:space:]')

if [ -z "$JOB_COUNT" ] || [ "$JOB_COUNT" -lt 1 ]; then
    echo "ERROR: jobs table has 0 scored/manual_review rows (expected >= 1)" >&2
    exit 1
fi
echo "  jobs(scored|manual_review) = $JOB_COUNT"

# ────────────────────────────────────────────────────────────────────────────
# 12. Assert cost_log has rows (confirms #117 schema fold)
# ────────────────────────────────────────────────────────────────────────────

echo "[assert] cost_log has rows (schema fold)"
COST_COUNT=$($EXEC sqlite3 /app/data/pipeline.db \
    "SELECT COUNT(*) FROM cost_log;" | tr -d '[:space:]')

if [ -z "$COST_COUNT" ] || [ "$COST_COUNT" -lt 1 ]; then
    echo "ERROR: cost_log has 0 rows (expected >= 1 — #117 fold not working)" >&2
    exit 1
fi
echo "  cost_log rows = $COST_COUNT"

# ────────────────────────────────────────────────────────────────────────────
# 14. Materials viewer smoke
# ────────────────────────────────────────────────────────────────────────────

echo "[assert] materials viewer smoke"
VIEWER_PORT="${TEST_MATERIALS_PORT:-18090}"

HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "http://localhost:${VIEWER_PORT}/healthz" || echo "FAIL")
if [ "$HTTP_CODE" != "200" ]; then
    echo "ERROR: /healthz returned $HTTP_CODE (expected 200)" >&2
    exit 1
fi
echo "  /healthz: 200 OK"

BODY=$(curl -s "http://localhost:${VIEWER_PORT}/materials/" || echo "FAIL")
if ! echo "$BODY" | grep -q "In flight"; then
    echo "ERROR: /materials/ did not contain 'In flight'" >&2
    exit 1
fi
echo "  index renders with expected sections"

# /docs/ must be reachable — regression guard for #224: docs/ was not baked
# into the image in v0.3.1, so the slug routes 404'd post-deploy.
# Slug list mirrors a representative subset of findajob.web.routes.docs._PAGES;
# `setup/*` was renamed to `getting-started/*` in the May-8 docs cleanup
# (#499–#503) and this list followed in v0.22.
for slug in "" usage tuning troubleshooting getting-started operations/install-docker getting-started/install-fly getting-started/start-here-fly getting-started/cost; do
    HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "http://localhost:${VIEWER_PORT}/docs/${slug}" || echo "FAIL")
    if [ "$HTTP_CODE" != "200" ]; then
        echo "ERROR: /docs/${slug} returned $HTTP_CODE (expected 200)" >&2
        exit 1
    fi
done
echo "  /docs/ + slug routes: 200 OK"

if (cd "$SCRATCH" && docker compose exec -T scheduler which rclone >/dev/null 2>&1); then
    echo "ERROR: rclone is still in the image" >&2
    exit 1
fi
echo "  rclone absent from image"

# ────────────────────────────────────────────────────────────────────────────
# 15. Done — cleanup runs on EXIT
# ────────────────────────────────────────────────────────────────────────────

echo
echo "✅  Fresh-install smoke passed."
echo "    Stack dir (auto-cleaned): $SCRATCH"
