"""Self-verifies the basic-auth gate from inside the running container (#487).

Run after every `docker compose up -d`:

    docker exec <container> python -m findajob.web.verify_auth

Exit codes:
    0  auth gate is healthy
    2  FINDAJOB_AUTH_USER and/or FINDAJOB_AUTH_PASS not set in container env
    3  anonymous probe did not return 401 + WWW-Authenticate: Basic
    4  authenticated probe with configured creds did not return 200
    5  unexpected exception talking to the local app (network, decode, etc.)

The probed port comes from `FINDAJOB_INTERNAL_PORT` (default 8090), matching
the env-driven uvicorn port in `ops/entrypoint.sh` — a Fly web-launched
instance serves 8080 and must still verify clean.

Hard-rule contract: any non-zero exit must trigger
`cd /opt/stacks/<stack> && docker compose down`. See CLAUDE.md
"Auth Gate Must Be Verified Post-Deploy".
"""

from __future__ import annotations

import base64
import os
import sys
import urllib.error
import urllib.request

_DEFAULT_PORT = "8090"
_TIMEOUT = 10.0


def _probe_url() -> str:
    """Build the probe URL from the port the app actually serves on (#1062).

    Read at call time, not import time: `main()` calls `load_env()` to pull
    `data/.env` into `os.environ`, which happens long after this module is
    imported. A module-level constant would miss that — and miss Fly's
    web-launch default of 8080.

    Mirrors `${FINDAJOB_INTERNAL_PORT:-8090}` in `ops/entrypoint.sh`, including
    its empty-string handling, so the verifier and uvicorn can't disagree about
    which port the gate lives behind.
    """
    port = os.environ.get("FINDAJOB_INTERNAL_PORT", "").strip() or _DEFAULT_PORT
    return f"http://127.0.0.1:{port}/board/dashboard"


def _probe(headers: dict[str, str]) -> tuple[int, dict[str, str]]:
    req = urllib.request.Request(_probe_url(), headers=headers)
    try:
        r = urllib.request.urlopen(req, timeout=_TIMEOUT)  # noqa: S310
        return r.status, dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers)


def main() -> int:
    # Load data/.env so credentials set via in-app onboarding (#895) are
    # visible to this standalone process — os.environ only has Fly secrets
    # or compose env_file values, not runtime-written ones.
    try:
        from findajob.paths import load_env

        load_env()
    except Exception:  # noqa: BLE001
        pass  # best-effort; env vars may already be set via secrets

    # Match install_basic_auth's whitespace-stripping behavior so the
    # verifier's "both set" contract aligns with what the runtime actually
    # sees after the env_file parser.
    user = os.environ.get("FINDAJOB_AUTH_USER", "").strip()
    password = os.environ.get("FINDAJOB_AUTH_PASS", "").strip()
    if not user or not password:
        print(
            "FAIL: FINDAJOB_AUTH_USER and/or FINDAJOB_AUTH_PASS not set in container env",
            file=sys.stderr,
        )
        return 2

    try:
        anon_code, anon_headers = _probe({})
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL: anonymous probe raised {type(exc).__name__}: {exc}", file=sys.stderr)
        return 5

    www_auth = anon_headers.get("WWW-Authenticate") or anon_headers.get("www-authenticate") or ""
    if anon_code != 401 or not www_auth.lower().startswith("basic"):
        print(
            f"FAIL: anonymous probe expected 401 + WWW-Authenticate: Basic, "
            f"got {anon_code} (www-authenticate={www_auth!r})",
            file=sys.stderr,
        )
        return 3

    auth_header = "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode("ascii")
    try:
        authed_code, _ = _probe({"Authorization": auth_header})
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL: authenticated probe raised {type(exc).__name__}: {exc}", file=sys.stderr)
        return 5

    if authed_code != 200:
        print(f"FAIL: authenticated probe expected 200, got {authed_code}", file=sys.stderr)
        return 4

    print("OK: auth gate healthy")
    return 0


if __name__ == "__main__":
    sys.exit(main())
