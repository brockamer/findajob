"""Cache-control plumbing validation spike for #470 / AC#5 (revised scope).

Validates that findajob.llm.openrouter's cached_prefix produces cache
hits when the same shared content is reused across consecutive calls
against an Anthropic Opus model. This validates the wrapper's
cache_control plumbing — it does NOT validate cross-role prep-chain
caching (that's Phase 2 / #471, see #470 design-note comment).

Run from a dev VM venv:

    OPENROUTER_API_KEY=$(grep '^OPENROUTER_API_KEY=' data/.env | cut -d= -f2) \\
    uv run python scripts/diag/cache_control_spike.py

Cost: ~$0.05-0.10 for 2 small Opus calls. Reports to stdout; paste the
output into the Session note on #470.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

from findajob.llm.openrouter import complete

# ~5000 tokens of stable shared content — well above Anthropic's 1024-token
# minimum cache size. Mimics the shape of profile + JD content the prep
# chain will eventually share.
SHARED_CONTEXT = (
    "CANDIDATE PROFILE (synthetic for spike validation)\n"
    "================================================\n"
    "20+ years data center infrastructure NPI experience.\n"
    "Hardware validation, server/GPU/accelerator launches.\n"
    "Cross-functional program management.\n" + ("Filler line to reach cache threshold.\n" * 200)
)


def _setup_synthetic_role() -> Path:
    """Create a tmp role file pointing at Anthropic Opus."""
    tmpdir = Path(tempfile.mkdtemp(prefix="cache_spike_"))
    role_file = tmpdir / "spike_role.md"
    role_file.write_text(
        "---\n"
        "model: openrouter:anthropic/claude-opus-4-7\n"
        "max_tokens: 50\n"
        "---\n"
        "You are a one-line responder. Answer in under 10 words.\n"
    )
    return tmpdir


def main() -> int:
    if not os.environ.get("OPENROUTER_API_KEY"):
        print("OPENROUTER_API_KEY not set in environment")
        return 2

    roles_dir = _setup_synthetic_role()
    print("# Cache-control plumbing spike for #470 (AC #5 revised scope)")
    print()

    # Provider pinning IS required for cache warmth across calls — sticky
    # routing isn't auto-triggered by cache writes alone (verified empirically
    # 2026-05-06 against Opus 4.7). Without pin_provider the second call
    # routes to a different Anthropic edge with a cold cache and hit rate
    # falls to 0%. Lowercase "anthropic" matches OpenRouter's provider slug.
    print("Call 1 (cache write expected):")
    try:
        r1 = complete(
            role="spike_role",
            prompt="What is 2 + 2?",
            cached_prefix=SHARED_CONTEXT,
            pin_provider="anthropic",
            roles_dir=roles_dir,
            timeout_s=120,
        )
    except Exception as e:  # noqa: BLE001
        print(f"FAIL on call 1: {type(e).__name__}: {e}")
        return 1
    print(f"  prompt_tokens={r1.prompt_tokens}  cached_tokens={r1.cached_tokens}  cost=${r1.cost_usd:.4f}")

    print("Call 2 (cache read expected):")
    try:
        r2 = complete(
            role="spike_role",
            prompt="What is 3 + 3?",
            cached_prefix=SHARED_CONTEXT,
            pin_provider="anthropic",
            roles_dir=roles_dir,
            timeout_s=120,
        )
    except Exception as e:  # noqa: BLE001
        print(f"FAIL on call 2: {type(e).__name__}: {e}")
        return 1
    print(f"  prompt_tokens={r2.prompt_tokens}  cached_tokens={r2.cached_tokens}  cost=${r2.cost_usd:.4f}")
    print()

    hit_pct = (r2.cached_tokens / r2.prompt_tokens * 100) if r2.prompt_tokens else 0
    print(f"Call 2 cache hit: {hit_pct:.1f}% ({r2.cached_tokens} / {r2.prompt_tokens} tokens)")
    print()
    if r2.cached_tokens >= r1.prompt_tokens * 0.8:
        print("VERDICT: cache_control plumbing works end-to-end.")
        print("  - cached_prefix (~5K tokens) was cached on call 1")
        print("  - call 2 read the cached prefix")
        print("  - usage.cached_tokens flowed back to CompletionResult")
        print("Phase 2 may proceed with cached_prefix as the cross-call cache mechanism.")
        return 0
    print("VERDICT: cache hit below 80% threshold — investigate before Phase 2.")
    print("  Possible causes:")
    print("  - provider routing variance (verify pin_provider='anthropic' is set; lowercase slug)")
    print("  - TTL expiry (>5min between calls)")
    print("  - payload mismatch (verify byte-identical cached_prefix on both calls)")
    return 1


if __name__ == "__main__":
    sys.exit(main())
