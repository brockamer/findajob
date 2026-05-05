"""Tests for the aichat-ng config template shipped in ops/aichat-ng/.

The template is YAML-valid and contains the client entries the pipeline
expects. As of #455 (RAG removal), the only direct client in the template
is `openrouter` (every chat call). Direct openai / claude / perplexity /
gemini-chat / groq / xai / gemini-embed clients were retired — every chat
model now reaches its provider through openrouter. We can't run aichat-ng
against the template itself (no API keys loaded at test time), but we can
assert structure so a future edit doesn't accidentally break parseability.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
TEMPLATE = REPO / "ops" / "aichat-ng" / "config.yaml.example"


def test_template_parses_as_yaml():
    assert TEMPLATE.exists(), f"template not at {TEMPLATE}"
    data = yaml.safe_load(TEMPLATE.read_text())
    assert isinstance(data, dict)


def test_template_has_required_clients():
    data = yaml.safe_load(TEMPLATE.read_text())
    assert "clients" in data
    # Clients are identified by explicit `name:` when present, otherwise
    # aichat-ng falls back to the `type:` for the client name.
    names = set()
    for c in data["clients"]:
        names.add(c.get("name") or c.get("type"))
    # Post-#455 minimal client set: every chat call routes through openrouter.
    # RAG was removed (#455), so gemini-embed must NOT be present.
    required = {"openrouter"}
    missing = required - names
    assert not missing, f"template missing required clients: {missing}"
    # Negative assertion: the retired direct chat clients must NOT come back
    # without an explicit decision (each is a key in the plaintext-keys
    # surface, and each provider is already reachable via openrouter).
    retired = {"openai", "groq", "xai", "claude", "perplexity", "gemini-embed"}
    leaked = retired & names
    assert not leaked, (
        f"template re-introduced retired direct client(s): {leaked}. "
        f"Reach these providers via openrouter unless there's a specific reason "
        f"to bring back a direct client (and update this assertion if so)."
    )
    # The chat-mode `gemini` client (with safetySettings patches) was
    # retired alongside the others — gemini chat routes through openrouter now.
    gemini_clients = [c for c in data["clients"] if c.get("type") == "gemini"]
    assert not gemini_clients, (
        f"template has unexpected gemini client(s): {gemini_clients}. "
        f"gemini chat routes through openrouter; no gemini clients should remain."
    )


def test_template_has_no_literal_keys():
    text = TEMPLATE.read_text()
    # API-key-shaped strings on an api_key line: `sk-...`, `pplx-...`,
    # `xai-...`, `gsk_...`, `AIza...`, or 40+ alphanumeric chars.
    suspicious = re.findall(
        r"api_key:\s*(sk-[A-Za-z0-9_-]+|pplx-[A-Za-z0-9]+|xai-[A-Za-z0-9]+|"
        r"gsk_[A-Za-z0-9]+|AIza[A-Za-z0-9_-]+|[A-Za-z0-9]{40,})",
        text,
    )
    assert not suspicious, f"template contains literal keys: {suspicious}"
