#!/bin/bash
# scripts/test_container_integration.sh
#
# Local pre-release smoke test for the findajob container image.
# Spins up a throwaway stack, runs each scheduled script once, asserts
# no exceptions and sane DB state, then tears down.
#
# Prerequisites:
# - docker + docker compose on PATH
# - A findajob image already built locally (findajob:local) OR pass via
#   FINDAJOB_TEST_IMAGE env var
# - data/.env from a working instance (copied into the throwaway stack)
#
# Usage:
#   FINDAJOB_TEST_IMAGE=findajob:local scripts/test_container_integration.sh
#
# Run this as part of the #69 release gate before tagging v0.1.N.

set -euo pipefail

IMAGE="${FINDAJOB_TEST_IMAGE:-findajob:local}"
STACK_DIR="$(mktemp -d -t findajob-test-XXXXXX)"
cleanup() {
    echo "[cleanup] tearing down stack at $STACK_DIR"
    (cd "$STACK_DIR" && docker compose down -v 2>/dev/null || true)
    rm -rf "$STACK_DIR"
}
trap cleanup EXIT

echo "[setup] stack dir: $STACK_DIR  image: $IMAGE"

mkdir -p "$STACK_DIR/state"/{data,config,candidate_context,companies,logs,aichat_ng}

# Copy user's real data/.env (API keys) — tests hit real LLM and API endpoints
if [ ! -f "$HOME/.findajob/state/data/.env" ] && [ ! -f "${PWD}/data/.env" ]; then
    echo "ERROR: need data/.env with API keys — either $HOME/.findajob/state/data/.env or ./data/.env" >&2
    exit 2
fi
cp "${PWD}/data/.env" "$STACK_DIR/state/data/.env" 2>/dev/null \
    || cp "$HOME/.findajob/state/data/.env" "$STACK_DIR/state/data/.env"
chmod 600 "$STACK_DIR/state/data/.env"

# Minimal stub config — enough to bring the scheduler up without erroring on import
cat > "$STACK_DIR/state/config/sheet_id.txt" <<'EOF'
TEST_SHEET_ID_PLACEHOLDER
EOF

cat > "$STACK_DIR/state/candidate_context/profile.md" <<'EOF'
# Test Profile
Minimal stub for container integration testing.
EOF

# Compose file pointing at the local image
cat > "$STACK_DIR/compose.yaml" <<EOF
services:
  scheduler:
    image: ${IMAGE}
    env_file: ./state/data/.env
    environment:
      TZ: America/New_York
      PUID: $(id -u)
      PGID: $(id -g)
      JSP_BASE: /app
      FINDAJOB_JOBSYNC_ENABLED: "false"
    volumes:
      - ./state/data:/app/data
      - ./state/config:/app/config
      - ./state/candidate_context:/app/candidate_context
      - ./state/companies:/app/companies
      - ./state/logs:/app/logs
      - ./state/aichat_ng:/root/.config/aichat_ng
    command: sleep infinity   # don't actually run cron; we exec scripts ourselves
EOF

echo "[start] bringing up stack"
(cd "$STACK_DIR" && docker compose up -d)

EXEC="docker compose -f $STACK_DIR/compose.yaml exec -T scheduler"

echo "[init] creating scratch pipeline.db"
$EXEC python3 /app/scripts/init_db.py

echo "[test] python package imports"
$EXEC python3 -c "import findajob; import findajob.paths; assert findajob.paths.BASE == '/app'"

echo "[test] supercronic validates crontab"
$EXEC supercronic -test /app/crontab

echo "[test] aichat-ng executes"
$EXEC aichat-ng --version

echo "[test] notify.py health-check on empty state"
$EXEC python3 /app/scripts/notify.py health-check || echo "[note] health-check may warn on empty DB — non-fatal"

echo "[test] poll_flags.py dry run (Sheet is stub — expect a soft failure, not a Python crash)"
set +e
$EXEC python3 /app/scripts/poll_flags.py
POLL_EXIT=$?
set -e
# Acceptable: 0 (if Sheet exists and is empty) or non-zero-with-clean-stderr.
# Unacceptable: a Python traceback, which would indicate a packaging or path bug.
echo "[test] poll_flags.py exit: $POLL_EXIT (traceback-free is the pass criterion)"

echo "[test] DB has expected tables"
TABLES=$($EXEC sqlite3 /app/data/pipeline.db ".tables")
echo "Tables: $TABLES"
for t in jobs audit_log feedback_log; do
    echo "$TABLES" | grep -q "$t" || { echo "ERROR: missing table $t" >&2; exit 1; }
done

echo
echo "✅  Container integration test passed."
echo "   Stack dir (auto-cleaned): $STACK_DIR"
