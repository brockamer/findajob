# Security Policy

## Supported versions

findajob ships as a Docker image (`ghcr.io/brockamer/findajob`) with two release tracks:

| Track | What it is | Security patches? |
|-------|------------|-------------------|
| `:latest` | Bleeding-edge, rolls on every merge to `main` | Yes |
| `:vMAJOR.MINOR` (e.g. `:v0.20`) | Moving alias for the current minor; bugfix patches roll automatically on `docker compose pull` | Yes |
| `:vMAJOR.MINOR.PATCH` (e.g. `:v0.20.1`) | Immutable per-patch tag | No — pin to `:v0.20` to receive bugfixes |

If you're running an older minor (e.g. `:v0.19` while `:v0.20` is current), bump to the current minor before reporting — many issues are already fixed in newer patches.

## Reporting a vulnerability

**Please do not file a public GitHub issue for security-relevant bugs.**

Use [GitHub's private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability) on this repository: the **Security** tab → **Report a vulnerability**. This opens a private advisory thread visible only to the maintainer.

Include:
- A short description of the vulnerability and its impact.
- A reproducer (commands, sample inputs, or a minimal compose stack that demonstrates the issue).
- The image tag and stack configuration where you observed it.

### Response timeline

- **Acknowledgement**: within 7 days of report.
- **Triage and severity assessment**: within 14 days.
- **Fix or mitigation**: aimed for within 30 days for high-severity issues; longer for low-severity. The advisory thread tracks progress.
- **Disclosure**: coordinated with the reporter; default 90-day window from acknowledgement before public disclosure.

This is a personal project with one maintainer; timelines are best-effort. If you need a faster response for a high-severity issue, say so in the report and I'll prioritize.

## Scope

### In scope

The following surfaces are intended to be hardened. Vulnerabilities here will be triaged and patched:

- **Basic-auth gate** (`findajob.web.auth`). The gate enforces HTTP Basic Auth on every protected route when `FINDAJOB_AUTH_USER` and `FINDAJOB_AUTH_PASS` are set. Issues to report: bypasses, timing oracles, missing protection on a route that should be gated, header-injection.
- **State write surface** (`findajob.web.routes.board_actions` + `findajob.actions`). Every state transition runs through these. Issues to report: unauthenticated state mutation, SQL injection, CSRF on state-changing POST handlers, race conditions that corrupt state.
- **LLM transport** (`findajob.llm.openrouter.complete`). The single point of LLM call in the codebase. Issues to report: credential leakage in logs/responses, request smuggling.
- **Per-stack key isolation invariant** (#339). Each tester stack's `data/.env` is supposed to carry only that tester's credentials. Issues to report: any code path that could read another stack's keys, or that could write a tester's keys somewhere they leak.
- **Pre-commit PII protection**. The `.git/hooks/pre-commit` hook (template at `docs/getting-started/pre-commit-hook.example.sh`) and the CI counterpart at `.github/workflows/pii-scan.yml` are designed to keep personal data out of the public repo. Issues to report: bypasses, false negatives on the documented PATTERNS, or CI workflow injection.
- **Onboarding flow** (`findajob.onboarding.*`, `findajob.web.routes.onboarding_*`). The flow collects API keys and writes them to `data/.env`. Issues to report: leakage to the browser/templates/logs, race conditions during the atomic-write/backup path, sentinel-write bypass that leaves a half-onboarded stack accessible.

### Out of scope

These are residual risks acknowledged by the design but not project vulnerabilities to patch:

- **Prompt injection sourced from job descriptions.** The pipeline ingests JD text from third-party job boards and feeds it to LLM roles (scorer, briefing writer, resume tailor, cover letter writer). A malicious JD could attempt to inject instructions into a downstream prompt. The LLM transport doesn't try to defend against this; the impact is bounded to the operator's own pipeline output (a bad cover letter, a misleading briefing) — not other users, not the host system, not API key exposure. Operators who're worried can review prep materials before submission, which is the recommended workflow anyway.
- **Operator deployment topology.** How the stack is reverse-proxied, what perimeter VPN sits in front of it, how backups are exfiltrated — these are operator decisions documented in operator-private notes. Vulnerabilities in third-party services (NAS firmware, VPN products, reverse-proxy products) should be reported to those vendors, not here.
- **Third-party API key abuse.** API keys live in the operator's `data/.env`. If a key is exfiltrated by malware on the operator's host, that's an endpoint-security issue, not a findajob issue.
- **Supply-chain risk in declared dependencies.** Dependabot updates ride through the normal PR flow. Report supply-chain-attack patterns (typosquatting in `pyproject.toml`, malicious upstream releases) but routine CVEs in dependencies are tracked through GitHub's normal Dependabot alerts.

## Disclosure history

No published advisories yet. When the first one ships, it'll appear under **Security → Advisories** on this repository.
