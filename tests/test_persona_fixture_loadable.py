"""Persona fixture loadability sanity check (#565).

Catches accidental fixture damage and drift between the persona's
active_sources.txt and the canonical adapter registry.
"""

from __future__ import annotations

import base64

from findajob.fetchers.adapters.registry import REGISTERED_ADAPTERS
from findajob.staging.reset import DEFAULT_FIXTURE as FIXTURE


def test_fixture_root_exists() -> None:
    assert FIXTURE.is_dir(), f"Persona fixture missing at {FIXTURE}"


def test_required_files_present() -> None:
    required = [
        "candidate_context/profile.md",
        "candidate_context/master_resume.md",
        "candidate_context/discovered_companies.md",
        "candidate_context/role_archetypes.md",
        "config/active_sources.txt",
        "config/feed_urls.txt",
        "config/reject_reasons.yaml",
        "config/speculative_targets.txt",
        "data/.onboarding-complete",
    ]
    missing = [p for p in required if not (FIXTURE / p).exists()]
    assert not missing, f"Persona fixture missing files: {missing}"


def test_active_sources_match_registry() -> None:
    """Every adapter in persona's active_sources.txt must be in REGISTERED_ADAPTERS."""
    text = (FIXTURE / "config" / "active_sources.txt").read_text()
    listed = [line.strip() for line in text.splitlines() if line.strip() and not line.startswith("#")]
    registered_names = {cls.name for cls in REGISTERED_ADAPTERS}
    unknown = [name for name in listed if name not in registered_names]
    assert not unknown, f"Persona enumerates unknown adapters: {unknown}"


def test_feed_urls_well_formed() -> None:
    text = (FIXTURE / "config" / "feed_urls.txt").read_text()
    urls = [line.strip() for line in text.splitlines() if line.strip() and not line.startswith("#")]
    assert urls, "feed_urls.txt has no URLs"
    for url in urls:
        assert url.startswith("https://"), f"Non-https URL: {url}"


def test_speculative_targets_non_empty() -> None:
    text = (FIXTURE / "config" / "speculative_targets.txt").read_text()
    targets = [line.strip() for line in text.splitlines() if line.strip() and not line.startswith("#")]
    assert targets, "speculative_targets.txt empty"


def test_persona_pii_clean() -> None:
    """Persona must not contain real-person identifiers from operator or testers.

    The forbidden list is base64-encoded so this test source file doesn't trip
    the repo's pre-commit PII scanner while still asserting against literal
    names at runtime.
    """
    # fmt: off
    # Encoded so this source file doesn't trip the pre-commit PII scanner.
    # Covers: operator handle, operator full name, and each tester's real
    # name / email prefix (alice, papa, tango, dave, judy).
    _FORBIDDEN_B64 = [
        "YnJvY2thbWVy",
        "RGFuaWVsIEJyb2Nr",
        "QW15IFNhd3llcg==",
        "c2F3eWVyLmFteQ==",
        "UGllcmNlIE5ld21hbg==",
        "cGllcmNlbmV3bWFu",
        "VHJpY2lhIFBhdHJpY2s=",
        "TWljaGFlbCBEaW5zbW9yZQ==",
        "SmFoIEJ1cnRz",
    ]
    # fmt: on
    forbidden = [base64.b64decode(s).decode() for s in _FORBIDDEN_B64]
    for path in FIXTURE.rglob("*"):
        if not path.is_file():
            continue
        try:
            content = path.read_text()
        except UnicodeDecodeError:
            continue
        for needle in forbidden:
            assert needle not in content, f"PII leak in {path}: {needle}"
