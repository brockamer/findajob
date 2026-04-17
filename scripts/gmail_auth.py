#!/usr/bin/env python3
# scripts/gmail_auth.py
"""
Gmail OAuth helper — standalone script run once per instance to mint
the Gmail API token that triage.py, backfill_jd.py, and notify.py use.

Two modes:
  --mode=device  (default)  OAuth 2.0 Limited Input Device flow.
                            Prints google.com/device URL + code; polls
                            for user consent. No callback, no port.
                            Used in v0.1.0.
  --mode=local              InstalledAppFlow.run_local_server on --port.
                            Requires the redirect URI to be authorized on
                            the OAuth client. NOT USED in v0.1.0 — present
                            for v0.2.0 when #59 lands with reverse-proxy
                            routing.

Writes credentials to --token-out (default: /app/config/gmail_token.json),
which is bind-mounted into the container and subsequently read by
fetchers.py on every scheduled run.

Usage inside the scheduler container:
  docker compose --profile setup run --rm gmail-auth
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
        description="Mint a Gmail API token via device or local flow.",
    )
    p.add_argument(
        "--mode",
        choices=["device", "local"],
        default="device",
        help="OAuth flow type. device = limited-input (default); local = callback server.",
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
        help="Port for --mode=local callback server (ignored for device mode).",
    )
    return p


def run_device(client_secrets: str):
    """
    OAuth 2.0 Limited Input Device flow.

    User opens google.com/device on any browser, enters the code we print,
    signs in, grants scopes. We poll until consent and return credentials.
    """
    # Imported lazily so --help works even without the google libs installed
    import json
    import time

    import requests
    from google.oauth2.credentials import Credentials

    with open(client_secrets) as f:
        secrets = json.load(f)
    installed = secrets.get("installed") or secrets.get("web") or {}
    client_id = installed["client_id"]
    client_secret = installed["client_secret"]

    # Step 1: ask Google for a device code
    r = requests.post(
        "https://oauth2.googleapis.com/device/code",
        data={"client_id": client_id, "scope": " ".join(GMAIL_SCOPES)},
        timeout=30,
    )
    r.raise_for_status()
    dev = r.json()

    print()
    print(f"  Open this URL on any browser: {dev['verification_url']}")
    print(f"  Enter this code:              {dev['user_code']}")
    print()
    print(f"  Waiting for consent (expires in {dev['expires_in']}s)...")
    print()

    # Step 2: poll the token endpoint until the user completes consent
    interval = int(dev.get("interval", 5))
    deadline = time.time() + int(dev["expires_in"])
    while time.time() < deadline:
        time.sleep(interval)
        r = requests.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "device_code": dev["device_code"],
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            },
            timeout=30,
        )
        body = r.json()
        if r.ok:
            return Credentials(
                token=body["access_token"],
                refresh_token=body.get("refresh_token"),
                token_uri="https://oauth2.googleapis.com/token",
                client_id=client_id,
                client_secret=client_secret,
                scopes=GMAIL_SCOPES,
            )
        if body.get("error") == "authorization_pending":
            continue
        if body.get("error") == "slow_down":
            interval += 5
            continue
        # Any other error is terminal
        raise SystemExit(f"Device flow failed: {body}")

    raise SystemExit("Device flow timed out waiting for user consent.")


def run_local(client_secrets: str, port: int):
    """
    OAuth 2.0 callback flow — listens on 0.0.0.0:port for the redirect.

    Requires the OAuth client to list the corresponding http://host:port/...
    as an authorized redirect URI. Not used in v0.1.0.
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
            f"Download the client JSON from Google Cloud Console and place it at that path.",
            file=sys.stderr,
        )
        raise SystemExit(2)

    if args.mode == "device":
        creds = run_device(args.client_secrets)
    else:
        creds = run_local(args.client_secrets, args.port)

    out = Path(args.token_out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(creds.to_json())
    out.chmod(0o600)
    print(f"Token written to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
