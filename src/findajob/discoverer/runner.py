"""Orchestration for the company_discoverer pipeline (#284).

Reads the candidate profile, builds the prompt, calls aichat-ng,
strips think-block residue, parses, validates, and atomically writes
the output pair. On any failure: logs to pipeline.jsonl, optionally
ntfys, and returns a failure RunResult without raising.

Mirrors the `aichat()` helper pattern at scripts/prep_application.py:40-52.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import NamedTuple

from findajob.discoverer.parser import DiscoveryParseError, parse_markdown
from findajob.discoverer.prompt import build_prompt
from findajob.discoverer.writer import commit_atomically
from findajob.paths import AICHAT, BASE
from findajob.utils import log_event

_DEFAULT_TIMEOUT_S = 540  # under cron's 600s timeout, room for IO
_DEFAULT_COST_THRESHOLD_USD = 10.00
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


class RunResult(NamedTuple):
    success: bool
    count: int  # type: ignore[assignment]  # NamedTuple field shadows tuple.count method
    error: str | None
    cost_usd: float | None


def _extract_cost_usd(stderr: str) -> float | None:
    """Parse aichat-ng stderr for the per-call cost line, if present.

    aichat-ng emits a `usage` line on stderr when verbose. The exact format
    depends on the OpenRouter provider's reporting; for sonar-reasoning-pro
    the line includes a `total_cost` field. Returns None if no line matches.
    """
    m = re.search(r"total_cost[^0-9]*([0-9]+\.[0-9]+)", stderr or "")
    if m:
        return float(m.group(1))
    return None


def _send_ntfy(title: str, body: str) -> None:
    """Best-effort ntfy via scripts/notify.py send-raw.

    Uses subprocess to call the existing notify.py CLI; suppresses any
    error so a notification failure cannot mask a successful run.
    """
    try:
        subprocess.run(
            [sys.executable, str(Path(BASE) / "scripts" / "notify.py"), "send-raw", title, body],
            check=False,
            capture_output=True,
            timeout=15,
        )
    except Exception:
        pass


def _cost_threshold() -> float:
    raw = os.environ.get("DISCOVERY_COST_THRESHOLD_USD", "")
    try:
        return float(raw) if raw else _DEFAULT_COST_THRESHOLD_USD
    except ValueError:
        return _DEFAULT_COST_THRESHOLD_USD


def run(
    base_root: Path,
    profile_path: Path | None = None,
    ntfy_enabled: bool = True,
) -> RunResult:
    """Run the full discovery pipeline. Never raises.

    Returns a :class:`RunResult` describing success/failure and metadata.
    """
    profile = profile_path or (base_root / "candidate_context" / "profile.md")
    if not profile.is_file():
        msg = f"profile not found at {profile}"
        log_event("discovery_failed", reason="profile_missing", path=str(profile))
        if ntfy_enabled:
            _send_ntfy("discovery: profile missing", msg)
        return RunResult(success=False, count=0, error=msg, cost_usd=None)

    try:
        profile_text = profile.read_text(encoding="utf-8")
        prompt = build_prompt(profile_text)
        completed = subprocess.run(
            [AICHAT, "--role", "company_discoverer", "-S", prompt],
            capture_output=True,
            text=True,
            timeout=_DEFAULT_TIMEOUT_S,
        )
        if completed.returncode != 0 or not completed.stdout.strip():
            stderr = (completed.stderr or "")[:500]
            log_event(
                "discovery_failed",
                reason="aichat_returncode",
                returncode=completed.returncode,
                stderr=stderr.strip(),
            )
            if ntfy_enabled:
                _send_ntfy("discovery: aichat failed", f"returncode={completed.returncode}\n{stderr[:200]}")
            return RunResult(success=False, count=0, error=f"aichat failed (rc={completed.returncode})", cost_usd=None)

        raw_md = _THINK_RE.sub("", completed.stdout).strip()
        if raw_md == "INSUFFICIENT_PROFILE":
            log_event("discovery_failed", reason="insufficient_profile")
            if ntfy_enabled:
                _send_ntfy("discovery: insufficient profile", "LLM returned INSUFFICIENT_PROFILE")
            return RunResult(success=False, count=0, error="LLM returned INSUFFICIENT_PROFILE", cost_usd=None)

        parsed = parse_markdown(raw_md)
        json_payload: dict = {
            "generated_at": datetime.now(UTC).strftime("%Y-%m-%d"),
            "model": "openrouter:perplexity/sonar-reasoning-pro",
            "companies": [
                {
                    "name": c.name,
                    "cluster": c.cluster,
                    "channel": c.channel,
                    "reasoning": c.reasoning,
                    "citations": list(c.citations),
                }
                for c in parsed.companies
            ],
        }
        commit_atomically(base_root, parsed.markdown_clean + "\n", json_payload)

        cost = _extract_cost_usd(completed.stderr)
        log_event(
            "discovery_complete",
            count=len(parsed.companies),
            cost_usd=cost,
        )
        threshold = _cost_threshold()
        if cost is not None and cost > threshold and ntfy_enabled:
            _send_ntfy(
                "discovery: cost exceeded threshold",
                f"run cost ${cost:.2f} > threshold ${threshold:.2f} (still wrote {len(parsed.companies)} companies)",
            )
        return RunResult(success=True, count=len(parsed.companies), error=None, cost_usd=cost)

    except subprocess.TimeoutExpired:
        msg = f"aichat timeout after {_DEFAULT_TIMEOUT_S}s"
        log_event("discovery_failed", reason="timeout", timeout_s=_DEFAULT_TIMEOUT_S)
        if ntfy_enabled:
            _send_ntfy("discovery: timeout", msg)
        return RunResult(success=False, count=0, error=msg, cost_usd=None)
    except DiscoveryParseError as e:
        msg = str(e)
        log_event("discovery_failed", reason="parse_error", message=msg)
        if ntfy_enabled:
            _send_ntfy("discovery: parse error", msg[:200])
        return RunResult(success=False, count=0, error=msg, cost_usd=None)
    except Exception as e:  # noqa: BLE001 — guarantee never-raise contract
        msg = f"{type(e).__name__}: {e}"
        log_event("discovery_failed", reason="unhandled", message=msg)
        if ntfy_enabled:
            _send_ntfy("discovery: unhandled error", msg[:200])
        return RunResult(success=False, count=0, error=msg, cost_usd=None)
