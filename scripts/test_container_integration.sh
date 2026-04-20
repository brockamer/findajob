#!/bin/bash
# scripts/test_container_integration.sh
#
# v0.1.1+: Fresh-install smoke test for the findajob container image.
#
# Spins up a throwaway stack with EMPTY bind mounts, provides the minimum
# realistic input (live API keys, a Google Sheet ID the service account can
# write to, one fixture candidate profile), runs the full triage-to-
# pipeline_complete cycle, and asserts a scored job lands in the DB. Proves
# an external tester can go from "clone + configure" to working Dashboard
# with no operator intervention.
#
# This is the pre-tag release gate. Claude runs it from a docker-equipped
# host before proposing any v0.1.x tag cut. See docs/release-process.md
# §"Pre-tag smoke check".
#
# Prereqs:
# - docker + docker compose v2
# - findajob image available locally as ${FINDAJOB_TEST_IMAGE:-findajob:local}
#   (build with `docker build -t findajob:local .` from the repo root first)
# - A real data/.env with live API keys — either at
#   $HOME/.findajob/state/data/.env or ./data/.env
# - FINDAJOB_SMOKE_SHEET_ID env var — a Google Sheet the service account
#   can write to (reuse the operator's test sheet or a dedicated smoke sheet)
#
# Usage:
#   FINDAJOB_SMOKE_SHEET_ID=<sheet-id> FINDAJOB_TEST_IMAGE=findajob:local \
#     scripts/test_container_integration.sh
#
# Expected runtime: 2–5 minutes (dominated by ~20 LLM scoring calls over
# the real network). API budget: ≤$0.10 per run.

set -euo pipefail

# ────────────────────────────────────────────────────────────────────────────
# 1. Resolve repo root + fixture paths
# ────────────────────────────────────────────────────────────────────────────

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FIXTURES="$REPO_ROOT/tests/fixtures"

for f in smoke_profile.md smoke_jsearch_queries.txt smoke_prefilter_rules.yaml \
         smoke_in_domain_patterns.yaml smoke_companies_of_interest.txt; do
    if [ ! -f "$FIXTURES/$f" ]; then
        echo "ERROR: missing fixture $FIXTURES/$f" >&2
        exit 2
    fi
done

# ────────────────────────────────────────────────────────────────────────────
# 2. Resolve env + config inputs
# ────────────────────────────────────────────────────────────────────────────

IMAGE="${FINDAJOB_TEST_IMAGE:-findajob:local}"

if [ -z "${FINDAJOB_SMOKE_SHEET_ID:-}" ]; then
    cat >&2 <<EOF
ERROR: FINDAJOB_SMOKE_SHEET_ID env var is required.

Set it to the Google Sheet ID the service account can write to — either
the operator's existing test sheet or a dedicated smoke-test sheet.
Example:

  export FINDAJOB_SMOKE_SHEET_ID=1AbCdEfGhIjKlMnOpQrStUvWxYz
  scripts/test_container_integration.sh
EOF
    exit 2
fi

# Source .env either from the operator's standard location or from the
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
    rm -rf "$SCRATCH"
}
trap cleanup EXIT

echo "[setup] scratch dir: $SCRATCH  image: $IMAGE"

mkdir -p "$SCRATCH/state"/{data,config,candidate_context,companies,logs,aichat_ng,rclone}

# ────────────────────────────────────────────────────────────────────────────
# 4. Seed inputs into the bind mounts
# ────────────────────────────────────────────────────────────────────────────

# API keys → state/data/.env (copied from live operator install)
cp "$SRC_ENV" "$SCRATCH/state/data/.env"
chmod 600 "$SCRATCH/state/data/.env"

# Google Sheet ID → state/config/sheet_id.txt
echo "$FINDAJOB_SMOKE_SHEET_ID" > "$SCRATCH/state/config/sheet_id.txt"

# Candidate profile → state/candidate_context/profile.md
cp "$FIXTURES/smoke_profile.md" "$SCRATCH/state/candidate_context/profile.md"

# Scorer config files → state/config/
cp "$FIXTURES/smoke_jsearch_queries.txt"       "$SCRATCH/state/config/jsearch_queries.txt"
cp "$FIXTURES/smoke_prefilter_rules.yaml"      "$SCRATCH/state/config/prefilter_rules.yaml"
cp "$FIXTURES/smoke_in_domain_patterns.yaml"   "$SCRATCH/state/config/in_domain_patterns.yaml"
cp "$FIXTURES/smoke_companies_of_interest.txt" "$SCRATCH/state/config/companies_of_interest.txt"

# Empty feed_urls.txt — smoke test drives jobs via RapidAPI queries only
: > "$SCRATCH/state/config/feed_urls.txt"

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
      - ./state/aichat_ng:/app/.config/aichat_ng
      - ./state/rclone:/app/.config/rclone
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
# 10. Assert cost_log has rows (confirms #117 schema fold)
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
# 11. Assert aichat-ng config.yaml present (confirms #118 seed)
# ────────────────────────────────────────────────────────────────────────────

echo "[assert] /app/.config/aichat_ng/config.yaml present (aichat seed)"
if ! $EXEC test -f /app/.config/aichat_ng/config.yaml; then
    echo "ERROR: /app/.config/aichat_ng/config.yaml missing — entrypoint seed did not run (#118)" >&2
    exit 1
fi
echo "  aichat-ng config.yaml: OK"

# ────────────────────────────────────────────────────────────────────────────
# 12. Done — cleanup runs on EXIT
# ────────────────────────────────────────────────────────────────────────────

echo
echo "✅  Fresh-install smoke passed."
echo "    Stack dir (auto-cleaned): $SCRATCH"
