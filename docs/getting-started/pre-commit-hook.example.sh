#!/usr/bin/env bash
# Pre-commit hook TEMPLATE — blocks commits containing personal identifiers.
#
# INSTALLATION:
#   1. Copy this file to .git/hooks/pre-commit:
#        cp docs/getting-started/pre-commit-hook.example.sh .git/hooks/pre-commit
#        chmod +x .git/hooks/pre-commit
#   2. Edit the PATTERNS array below to match YOUR personal identifiers.
#   3. Test: try `git commit` with a staged file containing one of your patterns.
#      It should be blocked.
#
# This hook runs locally only — it is NOT shared via git. Each clone of the
# repo must install its own copy. This is intentional: your personal patterns
# should never be committed to the repo.

set -euo pipefail

# ── Patterns to block ─────────────────────────────────────────────────────────
# Edit this array with YOUR identifiers. All patterns are case-insensitive.
# Use extended regex (ERE) syntax — backslash-escape dots and special chars.
#
# Categories to consider adding:
#   - Real first/last name and any nicknames
#   - Email addresses and username handles
#   - Phone numbers
#   - Employer names from your career history (especially internal program names)
#   - Certification or credential names unique to you
#   - Personal service handles (ntfy topic, Google Form short URLs, Slack workspace)
#   - systemd unit label prefixes that include your name
#   - Home city if it's tied to your identity
#
PATTERNS=(
    # Your name
    # "your_last_name"
    # "your full name"

    # Email handle or username
    # "yourhandle"

    # Personal service endpoints
    # "my-ntfy-topic"
    # "forms\.gle/<your_form_id>"

    # Employer history (shouldn't leak into role prompts / tracked files)
    # "acmecorp"
    # "internal project codename"

    # systemd unit labels
    # "com\.yourhandle\."
)

# ── Check staged content ──────────────────────────────────────────────────────
STAGED=$(git diff --cached --diff-filter=ACMR -U0 | grep '^+' | grep -v '^+++' || true)

# Diagnostic line — makes silent failures visible (#314). If a commit with PII
# ever slips through, check whether this line printed at all in your terminal
# (or whether --no-verify was used). The line is the canary for silent-fail
# conditions.
ADDED_LINE_COUNT=$(echo -n "$STAGED" | grep -c '^+' || true)
echo "pre-commit: PII scan: ${#PATTERNS[@]} patterns × ${ADDED_LINE_COUNT:-0} added lines" >&2

FOUND=0
for pattern in "${PATTERNS[@]}"; do
    # Skip empty patterns (all commented out is fine)
    [ -z "$pattern" ] && continue
    if echo "$STAGED" | grep -qiE "$pattern"; then
        echo "pre-commit: blocked — staged diff contains personal identifier matching: $pattern"
        FOUND=1
    fi
done

if [ "$FOUND" -eq 1 ]; then
    echo ""
    echo "Remove or redact the matching content before committing."
    echo "To bypass (emergency only): git commit --no-verify"
    exit 1
fi

exit 0
