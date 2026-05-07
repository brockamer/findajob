#!/usr/bin/env bash
# scripts/bootstrap.sh — one-shot setup for a new findajob installation on Linux
#
# Usage:
#   bash scripts/bootstrap.sh              # full setup
#   bash scripts/bootstrap.sh --systemd   # only install/reload systemd units
#   bash scripts/bootstrap.sh --check     # verify install without making changes
#
# Safe to re-run. Idempotent. Won't overwrite existing personal config files.
# Run from the repo root: bash scripts/bootstrap.sh

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT_DIR="${REPO}/scripts"
CONFIG_DIR="${REPO}/config"
DATA_DIR="${REPO}/data"
LOG_DIR="${REPO}/logs"
SYSTEMD_DIR="${HOME}/.config/systemd/user"

# Terminal colors
RED='\033[0;31m'; YELLOW='\033[1;33m'; GREEN='\033[0;32m'; NC='\033[0m'
info()  { echo -e "${GREEN}[INFO]${NC} $1"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; }
ok()    { echo -e "${GREEN}[OK]${NC} $1"; }

SYSTEMD_ONLY=false
CHECK_ONLY=false
for arg in "$@"; do
  case "$arg" in
    --systemd) SYSTEMD_ONLY=true ;;
    --check)   CHECK_ONLY=true ;;
  esac
done

echo ""
echo "findajob bootstrap — repo: ${REPO}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# ─────────────────────────────────────────────────────────────────────────────
# SECTION 1: System dependencies
# ─────────────────────────────────────────────────────────────────────────────

check_deps() {
  local missing=()
  for cmd in python3 pandoc curl git; do
    if ! command -v "$cmd" &>/dev/null; then
      missing+=("$cmd")
    fi
  done

  if [ ${#missing[@]} -gt 0 ]; then
    warn "Missing tools: ${missing[*]}"
    if $CHECK_ONLY; then
      error "Install missing tools before running bootstrap."
      exit 1
    fi
    return 1
  fi
  ok "All required tools present"
  return 0
}

install_apt_deps() {
  info "Installing system packages..."
  sudo apt-get update -q
  sudo apt-get install -y python3 python3-pip pandoc curl git build-essential
  ok "System packages installed"
}

install_pip_deps() {
  info "Installing Python packages..."
  pip3 install --break-system-packages \
    google-api-python-client \
    google-auth-httplib2 \
    requests \
    jsonschema \
    beautifulsoup4
  ok "Python packages installed"
}

# ─────────────────────────────────────────────────────────────────────────────
# SECTION 2: Directory structure
# ─────────────────────────────────────────────────────────────────────────────

setup_dirs() {
  info "Creating required directories..."
  mkdir -p "${LOG_DIR}"
  mkdir -p "${REPO}/companies/_rejected"
  mkdir -p "${REPO}/companies/_applied"
  mkdir -p "${REPO}/companies/_waitlisted"
  mkdir -p "${REPO}/rag_sources"
  mkdir -p "${REPO}/voice_samples"
  ok "Directories ready"
}

# ─────────────────────────────────────────────────────────────────────────────
# SECTION 3: Personal config files (from templates, won't overwrite existing)
# ─────────────────────────────────────────────────────────────────────────────

setup_personal_config() {
  info "Setting up personal config files from templates..."
  local created=0

  copy_if_missing() {
    local src="$1" dest="$2"
    if [ -f "${dest}" ]; then
      ok "  ${dest##${REPO}/} — already exists, skipping"
    elif [ -f "${src}" ]; then
      cp "${src}" "${dest}"
      warn "  ${dest##${REPO}/} — CREATED from template. Edit this file."
      created=$((created + 1))
    else
      error "  Template not found: ${src}"
    fi
  }

  copy_if_missing "${CONFIG_DIR}/profile.md.example"               "${CONFIG_DIR}/profile.md"
  copy_if_missing "${REPO}/docs/master_resume.md.example"          "${REPO}/rag_sources/master_resume.md"
  copy_if_missing "${CONFIG_DIR}/jsearch_queries.txt.example"      "${CONFIG_DIR}/jsearch_queries.txt"
  copy_if_missing "${CONFIG_DIR}/feed_urls.txt.example"            "${CONFIG_DIR}/feed_urls.txt"
  copy_if_missing "${CONFIG_DIR}/target_companies.md.example"      "${CONFIG_DIR}/target_companies.md"
  copy_if_missing "${REPO}/CLAUDE.local.md.example"                "${REPO}/CLAUDE.local.md"
  copy_if_missing "${CONFIG_DIR}/paths.env.example"                "${CONFIG_DIR}/paths.env"

  if [ ! -f "${DATA_DIR}/.env" ]; then
    if [ -f "${DATA_DIR}/.env.example" ]; then
      cp "${DATA_DIR}/.env.example" "${DATA_DIR}/.env"
      chmod 600 "${DATA_DIR}/.env"
      warn "  data/.env — CREATED from template. Fill in all API keys before running triage."
      created=$((created + 1))
    fi
  else
    ok "  data/.env — already exists, skipping"
  fi

  if [ "${created}" -gt 0 ]; then
    echo ""
    warn "  ${created} config file(s) created from templates."
    warn "  Edit them before running the pipeline. See docs/setup/configure.md"
    echo ""
  fi
}

# ─────────────────────────────────────────────────────────────────────────────
# SECTION 5: Database
# ─────────────────────────────────────────────────────────────────────────────

setup_db() {
  if [ -f "${DATA_DIR}/pipeline.db" ]; then
    ok "  pipeline.db already exists ($(du -sh ${DATA_DIR}/pipeline.db | cut -f1))"
  else
    info "Initializing database..."
    python3 "${SCRIPT_DIR}/init_db.py"
    ok "  pipeline.db created"
  fi
}

# ─────────────────────────────────────────────────────────────────────────────
# SECTION 6: systemd user services
# ─────────────────────────────────────────────────────────────────────────────

# Derive absolute Python path
PYTHON_BIN="$(python3 -c 'import sys; print(sys.executable)')"
USERNAME="$(whoami)"

write_service_unit() {
  local name="$1" script="$2" description="$3"
  cat > "${SYSTEMD_DIR}/findajob-${name}.service" << EOF
[Unit]
Description=findajob ${description}
After=network-online.target

[Service]
Type=oneshot
ExecStart=${PYTHON_BIN} ${SCRIPT_DIR}/${script}
WorkingDirectory=${REPO}
EnvironmentFile=${DATA_DIR}/.env
StandardOutput=append:${LOG_DIR}/${name}.log
StandardError=append:${LOG_DIR}/${name}.log
EOF
}

write_timer_unit() {
  local name="$1" description="$2" on_calendar="$3"
  cat > "${SYSTEMD_DIR}/findajob-${name}.timer" << EOF
[Unit]
Description=${description}

[Timer]
OnCalendar=${on_calendar}
Persistent=true

[Install]
WantedBy=timers.target
EOF
}

write_interval_service() {
  local name="$1" script="$2" description="$3"
  write_service_unit "$name" "$script" "$description"
}

write_interval_timer() {
  local name="$1" description="$2" interval="$3"
  # Convert interval (e.g. "15min", "30min") to OnCalendar spec (e.g. "*:0/15", "*:0/30").
  # OnCalendar is more reliable than OnUnitActiveSec, which can lose its re-arm chain
  # if the service exits abnormally or the timer state file is lost.
  local minutes="${interval%min}"
  cat > "${SYSTEMD_DIR}/findajob-${name}.timer" << EOF
[Unit]
Description=${description}

[Timer]
OnCalendar=*:0/${minutes}
Persistent=true

[Install]
WantedBy=timers.target
EOF
}

write_notify_service() {
  local name="$1" subcommand="$2" description="$3"
  cat > "${SYSTEMD_DIR}/findajob-${name}.service" << EOF
[Unit]
Description=findajob ${description}
After=network-online.target

[Service]
Type=oneshot
ExecStart=${PYTHON_BIN} ${SCRIPT_DIR}/notify.py ${subcommand}
WorkingDirectory=${REPO}
EnvironmentFile=${DATA_DIR}/.env
StandardOutput=append:${LOG_DIR}/notify.log
StandardError=append:${LOG_DIR}/notify.log
EOF
}

install_systemd_units() {
  info "Installing systemd user service units..."
  mkdir -p "${SYSTEMD_DIR}"

  # Triage — 7:00 AM daily.  triage.py has its own SIGTERM handler and
  # uses ThreadPoolExecutor internally.  TimeoutStartSec=3600 (1 hour).
  write_service_unit  "triage"          "triage.py"         "daily triage pipeline"
  echo "TimeoutStartSec=3600" >> "${SYSTEMD_DIR}/findajob-triage.service"
  write_timer_unit    "triage"          "findajob daily triage" "*-*-* 00:00:00 America/Los_Angeles"

  # Watchdog — every 10 min. Single responsibility: reset jobs stuck in
  # prep_in_progress for > 60 min. Fast, no network calls, short timeout.
  write_interval_service "watchdog"     "watchdog.py"       "stale-prep watchdog"
  echo "TimeoutStartSec=300" >> "${SYSTEMD_DIR}/findajob-watchdog.service"
  write_interval_timer   "watchdog"     "findajob stale-prep watchdog"  "10min"

  # Form ingest — every 30 min
  write_interval_service "form-ingest"  "ingest_form.py"    "Google Form ingestion"
  write_interval_timer   "form-ingest"  "findajob form ingest"  "30min"

  # Notifications
  write_notify_service "notify-stats"    "daily-stats"    "daily stats notification"
  write_timer_unit     "notify-stats"    "findajob daily stats notification" "*-*-* 06:15:00 America/Los_Angeles"

  write_notify_service "notify-health"   "health-check"   "health check notification"
  write_timer_unit     "notify-health"   "findajob health check notification" "*-*-* 07:00:00 America/Los_Angeles"

  write_notify_service "notify-apply"    "apply-reminder" "apply reminder notification"
  write_timer_unit     "notify-apply"    "findajob apply reminder notification" "*-*-* 06:00:00 America/Los_Angeles"

  write_notify_service "notify-issues"   "issues-ping"    "issues ping notification"
  cat > "${SYSTEMD_DIR}/findajob-notify-issues.timer" << EOF
[Unit]
Description=findajob issues ping (Mon/Wed/Fri)

[Timer]
OnCalendar=Mon,Wed,Fri *-*-* 08:00:00 America/Los_Angeles
Persistent=true

[Install]
WantedBy=timers.target
EOF

  write_notify_service "notify-scoreboard" "scoreboard" "pipeline scoreboard update"
  cat > "${SYSTEMD_DIR}/findajob-notify-scoreboard.timer" << EOF
[Unit]
Description=findajob pipeline scoreboard (weekly Monday)

[Timer]
OnCalendar=Mon *-*-* 08:30:00 America/Los_Angeles
Persistent=true

[Install]
WantedBy=timers.target
EOF

  write_notify_service "notify-feedback" "feedback-review" "feedback review notification"
  cat > "${SYSTEMD_DIR}/findajob-notify-feedback.timer" << EOF
[Unit]
Description=findajob feedback review (Sunday)

[Timer]
OnCalendar=Sun *-*-* 08:00:00 America/Los_Angeles
Persistent=true

[Install]
WantedBy=timers.target
EOF

  ok "  systemd unit files written to ${SYSTEMD_DIR}"

  # Reload and enable
  info "Reloading systemd daemon and enabling timers..."
  systemctl --user daemon-reload

  local timers=(
    triage poller form-ingest notify-stats notify-health notify-apply
    notify-issues notify-scoreboard notify-feedback
  )
  for t in "${timers[@]}"; do
    systemctl --user enable "findajob-${t}.timer" 2>/dev/null || true
  done

  ok "  All timers enabled. Start them with:"
  echo "     systemctl --user start findajob-triage.timer"
  echo "     (or start all: systemctl --user start findajob-{triage,poller,form-ingest,notify-stats,notify-health,notify-apply,notify-issues,notify-scoreboard,notify-feedback}.timer)"
}

# ─────────────────────────────────────────────────────────────────────────────
# SECTION 7: Verification
# ─────────────────────────────────────────────────────────────────────────────

verify_install() {
  echo ""
  info "Verifying install..."
  local ok_count=0 warn_count=0

  check_item() {
    local label="$1" ok="$2"
    if $ok; then
      ok "  $label"; ok_count=$((ok_count+1))
    else
      warn "  MISSING: $label"; warn_count=$((warn_count+1))
    fi
  }

  check_item "python3"                    "$(command -v python3 &>/dev/null && echo true || echo false)"
  check_item "pandoc"                     "$(command -v pandoc &>/dev/null && echo true || echo false)"
  check_item "data/.env"                  "$([ -f ${DATA_DIR}/.env ] && echo true || echo false)"
  check_item "data/pipeline.db"           "$([ -f ${DATA_DIR}/pipeline.db ] && echo true || echo false)"
  check_item "config/profile.md"          "$([ -f ${CONFIG_DIR}/profile.md ] && echo true || echo false)"
  check_item "rag_sources/master_resume"  "$([ -f ${REPO}/rag_sources/master_resume.md ] && echo true || echo false)"
  check_item "config/ntfy_topic.txt"     "$([ -f ${CONFIG_DIR}/ntfy_topic.txt ] && echo true || echo false)"
  check_item "config/gmail_oauth_client"  "$([ -f ${CONFIG_DIR}/gmail_oauth_client.json ] && echo true || echo false)"
  check_item "CLAUDE.local.md"            "$([ -f ${REPO}/CLAUDE.local.md ] && echo true || echo false)"

  echo ""
  ok "  ${ok_count} checks passed, ${warn_count} items need attention"
  if [ "${warn_count}" -gt 0 ]; then
    echo ""
    warn "  Complete the missing items before running triage."
    echo "  See docs/setup/configure.md and docs/setup/prerequisites.md"
  fi
}

# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

if $CHECK_ONLY; then
  verify_install
  exit 0
fi

if $SYSTEMD_ONLY; then
  install_systemd_units
  exit 0
fi

# Full setup
if ! check_deps 2>/dev/null; then
  info "Installing missing system packages..."
  install_apt_deps
fi

install_pip_deps
setup_dirs
setup_personal_config
setup_db
install_systemd_units
verify_install

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
info "Bootstrap complete."
echo ""
echo "Next steps:"
echo "  1. Fill in API keys:          edit ${DATA_DIR}/.env"
echo "  2. Add your profile:          edit ${CONFIG_DIR}/profile.md"
echo "  3. Add your resume:           edit ${REPO}/rag_sources/master_resume.md"
echo "  4. Add Gmail credentials:     copy gmail_oauth_client.json (optional)"
echo "  5. Add search queries:        edit ${CONFIG_DIR}/jsearch_queries.txt"
echo "  6. Add Greenhouse slugs:      edit ${CONFIG_DIR}/feed_urls.txt"
echo "  7. Start the timers:          systemctl --user start findajob-triage.timer"
echo "  8. Test a manual run:         python3 scripts/triage.py"
echo ""
echo "  See docs/setup/install-linux.md for the full walkthrough."
echo ""
