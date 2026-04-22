#!/usr/bin/env python3
# scripts/gmail_auth.py
"""
Gmail OAuth helper — standalone script run once per instance to mint
the Gmail API token that triage.py and notify.py use.

Uses OAuth 2.0 loopback flow (InstalledAppFlow). Requires:
  1. A "Desktop app" OAuth client from Google Cloud Console (not "TVs and
     Limited Input devices" — that client type blocks Gmail scopes).
  2. An SSH tunnel so the browser redirect reaches the container callback.

See docs/setup/install-docker.md for the full step-by-step.

Writes credentials to --token-out (default: /app/config/gmail_token.json),
which is bind-mounted into the container and subsequently read by
fetchers.py on every scheduled run.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="gmail_auth",
        description="Mint a Gmail API token via loopback OAuth flow.",
    )
    p.add_argument(
        "--client-secrets",
        default="/app/config/gmail_oauth_client.json",
        help="Path to the OAuth client JSON downloaded from Google Cloud Console.",
    )
    p.add_argument(
        "--token-out",
        default="/app/config/gmail_token.json",
        help="Where to write the resulting token JSON.",
    )
    p.add_argument(
        "--port",
        type=int,
        default=8080,
        help="Port for callback server. Forward via SSH tunnel from your laptop.",
    )
    return p


def run_local(client_secrets: str, port: int):
    """
    OAuth 2.0 loopback callback flow.

    Listens on 0.0.0.0:port for the redirect. Requires an SSH tunnel so
    the browser redirect reaches the container. See install-docker.md.
    """
    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_secrets_file(client_secrets, GMAIL_SCOPES)
    return flow.run_local_server(
        host="0.0.0.0",
        port=port,
        open_browser=False,
        authorization_prompt_message="Open this URL to grant access:\n  {url}\n",
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if not os.path.exists(args.client_secrets):
        print(
            f"ERROR: OAuth client file not found: {args.client_secrets}\n"
            "Download the client JSON from Google Cloud Console and place it at that path.",
            file=sys.stderr,
        )
        raise SystemExit(2)

    creds = run_local(args.client_secrets, args.port)

    out = Path(args.token_out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(creds.to_json())
    out.chmod(0o600)
    print(f"Token written to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
