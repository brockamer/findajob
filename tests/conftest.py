"""Global test fixtures.

Redirects findajob.config_loader to read from tests/fixtures/config/
instead of the production config directory, and resets its cache before
each test so a test's config edits don't leak into the next test.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from findajob import config_loader

FIXTURES = Path(__file__).parent / "fixtures" / "config"


@pytest.fixture(autouse=True)
def _use_fixture_configs(monkeypatch):
    monkeypatch.setattr(config_loader, "_RULES_PATH", FIXTURES / "prefilter_rules.yaml")
    monkeypatch.setattr(config_loader, "_IN_DOMAIN_PATH", FIXTURES / "in_domain_patterns.yaml")
    monkeypatch.setattr(config_loader, "_COMPANIES_PATH", FIXTURES / "companies_of_interest.txt")
    config_loader._reset_cache()
    yield
    config_loader._reset_cache()
