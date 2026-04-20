"""Tests for the aichat-ng config template shipped in ops/aichat-ng/.

The template is YAML-valid and contains the client entries the pipeline
expects (claude, openai, gemini, openrouter, perplexity). We can't run
aichat-ng against the template itself (no API keys loaded at test time),
but we can assert structure so a future edit doesn't accidentally break
parseability.
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
    # These are the clients the roles config actually invokes.
    required = {"claude", "openai", "gemini", "openrouter", "perplexity"}
    missing = required - names
    assert not missing, f"template missing required clients: {missing}"


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
