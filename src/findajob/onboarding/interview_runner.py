"""Multi-turn LLM interview runner — Phase 2 thin delegate around the canonical wrapper.

Delegates to :func:`findajob.llm.openrouter.complete`. Translates
:class:`OpenRouterError` to :class:`InterviewRunnerError` so the route
layer's verbatim user_message render contract (#336 Task 6) is preserved.

Signature change from Phase 1: ``run_turn`` no longer accepts a
``system_prompt`` parameter. The wrapper reads ``config/roles/onboarding_interviewer.md``
directly. Callers (``routes/onboarding_interview.py``) must drop that argument.
"""

from __future__ import annotations

from findajob.llm.openrouter import OpenRouterError, complete

# Model pin retained as module-level constants so existing imports in
# test_onboarding_interview_runner.py continue to resolve.
INTERVIEW_MODEL = "anthropic/claude-sonnet-4-6"
INTERVIEW_MAX_TOKENS = 4096


class InterviewRunnerError(Exception):
    """Raised by :func:`run_turn` on any non-success path.

    ``user_message`` is rendered verbatim in the chat UI's error banner.
    ``kind`` classifies the failure so the route layer can pick a UX
    variant (auth/payment/rate_limit/upstream/network/malformed/config)
    without re-parsing the message string. ``status_code`` is the HTTP
    status from OpenRouter when available.
    """

    def __init__(
        self,
        user_message: str,
        *,
        kind: str = "unknown",
        status_code: int | None = None,
    ) -> None:
        super().__init__(user_message)
        self.user_message = user_message
        self.kind = kind
        self.status_code = status_code


def _translate(e: OpenRouterError) -> InterviewRunnerError:
    """Map OpenRouterError to an InterviewRunnerError with a user-facing message.

    Strings are byte-identical to the messages that the Phase 1 inline HTTP
    client produced. Dynamic data (status_code, reason, body snippet) is
    reconstructed from the wrapper's kind/status_code/message fields so the
    chat UI banner renders the same text it always has.
    """
    kind = e.kind
    code = e.status_code
    raw = str(e)  # the wrapper's internal message — used for dynamic strings

    if kind == "auth":
        msg = "OpenRouter rejected the API key (401 Unauthorized). Visit /onboarding/ to update your OpenRouter key."
    elif kind == "payment":
        msg = (
            "Your OpenRouter account is out of credit (402 Payment "
            "Required). Add prepaid credit at "
            "https://openrouter.ai/credits, then continue the interview."
        )
    elif kind == "rate_limit":
        msg = "OpenRouter rate-limited the request (429). Wait a moment and try again."
    elif kind == "upstream":
        # Wrapper produces "OpenRouter/upstream server error (NNN)." for 5xx
        # and "OpenRouter returned HTTP NNN: <body>" for other 4xx codes.
        # Re-emit the original Phase 1 strings using status_code.
        if code is not None and 500 <= code < 600:
            msg = (
                f"OpenRouter or the upstream model returned a server error "
                f"({code}). Try again in a moment; the issue is on their side."
            )
        elif code is not None:
            # Other HTTP errors (e.g. 418) — wrapper embeds body snippet in raw
            # "OpenRouter returned HTTP NNN: <body>" format; preserve as-is.
            msg = raw if raw.startswith("OpenRouter returned HTTP") else f"OpenRouter returned HTTP {code}: {raw[:200]}"
        else:
            msg = "OpenRouter returned an unexpected error. Try again in a moment."
    elif kind == "network":
        # Wrapper: "Could not reach OpenRouter (reason)."
        # Phase 1 added "Check the deployment's network connectivity and try again."
        # Extract reason from wrapper message for byte-fidelity.
        reason = _extract_network_reason(raw)
        msg = f"Could not reach OpenRouter ({reason}). Check the deployment's network connectivity and try again."
    elif kind == "malformed":
        # Wrapper uses short prefixes; map to Phase 1's longer strings.
        msg = _map_malformed(raw)
    elif kind == "config":
        msg = (
            "No OpenRouter key on file for this stack. Visit /onboarding/ "
            "Step 1 to provide your API keys, then return here to start "
            "the interview."
        )
    else:
        raw_msg = raw.removeprefix("Unexpected error: ")
        msg = f"Unexpected error talking to OpenRouter: {raw_msg[:200]}"

    return InterviewRunnerError(msg, kind=kind, status_code=code)


def _extract_network_reason(raw: str) -> str:
    """Pull the reason from 'Could not reach OpenRouter (reason).' wrapper message."""
    prefix = "Could not reach OpenRouter ("
    if raw.startswith(prefix) and raw.endswith(")."):
        return raw[len(prefix) : -2]
    # Fallback: return the whole wrapper message as the reason
    return raw


def _map_malformed(raw: str) -> str:
    """Map wrapper's short malformed messages to Phase 1's longer strings."""
    # Wrapper prefix → Phase 1 prefix mapping
    if raw.startswith("Non-JSON response:"):
        body_snippet = raw[len("Non-JSON response:") :].lstrip()
        return f"OpenRouter returned non-JSON response: {body_snippet}"
    if raw.startswith("Unexpected shape:"):
        body_snippet = raw[len("Unexpected shape:") :].lstrip()
        return f"OpenRouter returned unexpected response shape: {body_snippet}"
    if raw.startswith("Could not parse content:"):
        body_snippet = raw[len("Could not parse content:") :].lstrip()
        return f"Could not parse assistant content from OpenRouter response: {body_snippet}"
    if raw.startswith("Content not a string:"):
        type_name = raw[len("Content not a string:") :].lstrip()
        return f"Assistant content was not a string: {type_name}"
    # Fallback — preserve wrapper's message unchanged
    return raw


def run_turn(
    api_key: str,
    history: list[dict[str, str]],
    user_message: str,
) -> tuple[str, dict]:
    """Submit one user turn and receive the assistant turn.

    Args:
        api_key: tester's OpenRouter key (collected at /onboarding/ Step 1).
            The chat is funded by this key — there is no operator-funded
            fallback.
        history: prior turns as ``[{"role":"user"|"assistant","content":"..."}, ...]``.
            May be an empty list for the first turn.
        user_message: the new user turn text.

    Returns:
        ``(assistant_text, usage_dict)``. ``usage_dict`` carries token + cost
        fields: prompt_tokens, completion_tokens, cached_tokens, cost,
        generation_id.

    Raises:
        InterviewRunnerError: every non-success path — empty key, network,
            auth, payment, rate-limit, model 5xx, malformed response.
    """
    if not api_key or not api_key.strip():
        raise InterviewRunnerError(
            "No OpenRouter key on file for this stack. Visit /onboarding/ "
            "Step 1 to provide your API keys, then return here to start "
            "the interview.",
            kind="config",
        )

    try:
        result = complete(
            role="onboarding_interviewer",
            prompt=user_message,
            cache_system=True,
            pin_provider="anthropic",
            history=history,
            api_key=api_key,
        )
    except OpenRouterError as e:
        raise _translate(e) from e

    usage = {
        "prompt_tokens": result.prompt_tokens,
        "completion_tokens": result.completion_tokens,
        "cached_tokens": result.cached_tokens,
        "cost": result.cost_usd,
        "generation_id": result.generation_id,
    }
    return result.text, usage
