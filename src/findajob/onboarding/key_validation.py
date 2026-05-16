"""Format validators for API keys collected at onboarding.

Pure-data functions — no network calls. Each returns (True, "") on pass or
(False, error_message) on fail.  Error messages are user-actionable and safe
to render in HTML form error bubbles.

Network smoke check lives in openrouter_smoke.py; this module is strictly
about structure/format so the form can give instant inline feedback before
any HTTP round-trip.
"""

import string


def validate_openrouter_format(key: str) -> tuple[bool, str]:
    """Validate the format of an OpenRouter API key.

    Required field — blank input is a failure.
    Valid keys start with ``sk-or-v1-`` (after stripping surrounding whitespace).
    """
    stripped = key.strip()
    if not stripped:
        return False, "OpenRouter API key is required."
    if not stripped.startswith("sk-or-v1-"):
        return (
            False,
            'OpenRouter API key must start with "sk-or-v1-". Copy the full key from '
            "https://openrouter.ai/settings/keys (it is shown only once at creation).",
        )
    return True, ""


def validate_rapidapi_format(key: str) -> tuple[bool, str]:
    """Validate the format of a RapidAPI key.

    Optional field — blank input returns (True, "").
    When non-blank (after strip): must consist entirely of printable ASCII
    characters AND must contain no whitespace.  This catches the dominant
    typo class — accidentally pasting a full curl header line such as
    ``X-RapidAPI-Key: abc123`` (contains a space) or a key with an embedded
    newline from copy/paste.  No positive length range or prefix is
    enforced because RapidAPI key format has varied across the platform's
    history; we only reject the one known-wrong shape (``sk-or-v1-``,
    which is an OpenRouter key pasted into the wrong field).
    """
    stripped = key.strip()
    if not stripped:
        return True, ""

    # Reject if any whitespace exists anywhere inside the stripped value.
    if any(ch in string.whitespace for ch in stripped):
        return (
            False,
            "RapidAPI key must not contain spaces, tabs, or newlines. "
            "Copy only the key value, not the full curl header line.",
        )

    # Reject any non-printable characters (control chars, etc.).
    if not all(ch in string.printable for ch in stripped):
        return (
            False,
            "RapidAPI key contains non-printable characters. Re-copy it directly from the RapidAPI dashboard.",
        )

    # Reject the dominant cross-paste shape — an OpenRouter key landing in
    # the RapidAPI field. The OpenRouter format check (above this function)
    # already enforces the same prefix as required; mirroring the reject
    # here catches the swap before it reaches the network smoke check.
    if stripped.startswith("sk-or-v1-"):
        return (
            False,
            'This looks like an OpenRouter key ("sk-or-v1-..."), not a RapidAPI key. '
            "Paste the RapidAPI key from https://rapidapi.com/developer/security here, "
            "and the OpenRouter key in the OpenRouter field above.",
        )

    return True, ""
