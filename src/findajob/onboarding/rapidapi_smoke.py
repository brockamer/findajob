"""Smoke-check a user-supplied RapidAPI key at onboarding Step 1 (#689).

Single-purpose module: issues one minimal-query call against the
jobs-api14 RapidAPI endpoint and reports whether the key authenticates.
Used by the onboarding form to refuse to write the credentials row when
the user's key is invalid — failing loudly here is far better than
letting the pipeline silently fail on the first scheduled triage.

Parity shape with ``openrouter_smoke.py``: stdlib-only
(``urllib`` + ``json``), accepts the candidate key as a parameter
(not an env var), returns ``(True, None)`` on success or
``(False, error_message)`` with a user-actionable string on failure.

The endpoint + host constants are imported from ``JobsApi14Adapter`` so
the smoke probe stays in sync with the upstream the adapter actually
uses; the call itself does not import ``requests`` or instantiate the
adapter — Step 1 runs before the candidate's ``target_locations.txt``
exists, which the adapter's ``live_test`` requires.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

from findajob.fetchers.adapters.jobs_api14 import JobsApi14Adapter

SMOKE_TIMEOUT_S = 30

# Single representative query/location for the auth probe. The semantics
# we care about are "does the key authenticate?" — 401/403 fires before
# any query interpretation. Values mirror the adapter's defaults so the
# upstream sees a well-formed request and returns 200 or 4xx cleanly.
_PROBE_PARAMS = {
    "query": "engineer",
    "location": "United States",
    "datePosted": "day",
    "employmentTypes": "fulltime",
}


def verify_rapidapi_key(api_key: str) -> tuple[bool, str | None]:
    """Verify the RapidAPI key by issuing one minimal jobs-api14 query.

    Returns ``(True, None)`` on success, ``(False, error_message)`` on
    failure. Never raises — every failure mode returns a human-readable
    string suitable for rendering directly in the onboarding UI.
    """
    if not api_key or not api_key.strip():
        return False, "RapidAPI key is empty."

    query_string = urllib.parse.urlencode(_PROBE_PARAMS)
    url = f"{JobsApi14Adapter._ENDPOINT}?{query_string}"

    req = urllib.request.Request(  # noqa: S310 — GETting a fixed https URL
        url,
        headers={
            "x-rapidapi-host": JobsApi14Adapter._HOST,
            "x-rapidapi-key": api_key.strip(),
        },
        method="GET",
    )

    try:
        with urllib.request.urlopen(req, timeout=SMOKE_TIMEOUT_S) as resp:  # noqa: S310
            body = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        try:
            error_body = e.read().decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            error_body = ""
        if e.code == 401:
            return False, (
                "RapidAPI rejected the key (HTTP 401 Unauthorized). The key is invalid or "
                "has been revoked. Verify it at https://rapidapi.com/developer/security and "
                "re-paste."
            )
        if e.code == 403:
            # 403 from jobs-api14 is multi-cause: invalid key, not subscribed
            # to the jobs-api14 API, OR the deployment's egress IP is region-
            # blocked (#679 — observed on Fly egress IPs 2026-05-15). Surface
            # all three so Fly testers don't burn time chasing the wrong cause.
            # body_excerpt mirrors the adapter's jobsapi_403 log event.
            excerpt = error_body[:200].strip()
            excerpt_clause = f' Response body: "{excerpt}".' if excerpt else ""
            return False, (
                f"RapidAPI rejected the request (HTTP 403 Forbidden).{excerpt_clause} "
                "This usually means one of: (1) the key is invalid — verify at "
                "https://rapidapi.com/developer/security; (2) you are not subscribed to "
                "jobs-api14 — subscribe at "
                "https://rapidapi.com/letscrape-6bRBa3QguO5/api/jobs-api14 (free Basic plan "
                "works); (3) your deployment's outbound IP is region-blocked by jobs-api14 "
                "(observed on some Fly egress IPs — see issue #679)."
            )
        if e.code == 429:
            return False, "RapidAPI rate-limited the verification request. Wait a minute and re-paste."
        if 500 <= e.code < 600:
            return False, (
                f"RapidAPI returned HTTP {e.code}: {error_body[:200]}. "
                "Check RapidAPI status and try again in a few minutes."
            )
        return False, (
            f"RapidAPI returned HTTP {e.code}: {error_body[:200]}. "
            "Verify the key at https://rapidapi.com/developer/security."
        )
    except urllib.error.URLError as e:
        return False, (
            f"Could not reach RapidAPI ({e.reason}). Check that the container has network access and re-paste."
        )
    except Exception as e:  # noqa: BLE001
        return False, (
            f"Unexpected error verifying RapidAPI key: {type(e).__name__}: {str(e)[:200]}. "
            "Verify the key at https://rapidapi.com/developer/security."
        )

    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return False, (f"RapidAPI returned non-JSON response: {body[:200]}. Check RapidAPI status and try again.")

    # jobs-api14 reports auth/subscription problems via a 200 body with
    # ``hasError: true`` (mirrors the live_test branch in the adapter).
    if isinstance(data, dict) and data.get("hasError"):
        return False, (
            f"RapidAPI accepted the request but reported an error: {data.get('errors')}. "
            "This usually means the key is valid but you are not subscribed to the "
            "jobs-api14 API. Subscribe at "
            "https://rapidapi.com/letscrape-6bRBa3QguO5/api/jobs-api14."
        )

    return True, None
