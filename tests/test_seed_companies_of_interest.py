"""Tests for scripts/seed_companies_of_interest.py (issue #222)."""

import importlib
import json
from pathlib import Path

import pytest

from findajob import utils as utils_mod


@pytest.fixture
def seed_env(tmp_path, monkeypatch):
    """Relocate BASE and log path under a tmpdir, return the reloaded module."""
    (tmp_path / "config").mkdir()
    (tmp_path / "logs").mkdir()
    log_path = tmp_path / "logs" / "pipeline.jsonl"
    monkeypatch.setattr(utils_mod, "LOG_PATH", str(log_path))
    monkeypatch.setattr("findajob.paths.BASE", str(tmp_path))

    import scripts.seed_companies_of_interest as seed

    importlib.reload(seed)
    return seed, tmp_path, log_path


def _read_events(log_path: Path) -> list[dict]:
    if not log_path.exists():
        return []
    return [json.loads(line) for line in log_path.read_text().splitlines() if line.strip()]


def test_writes_when_missing(seed_env):
    seed, base, log_path = seed_env
    (base / "config" / "target_companies.md").write_text(
        "# Target companies\n\n## Tier 1\n\n- Acme Corp\n- Widget Co — leader in widgets\n- Globex\n",
        encoding="utf-8",
    )

    assert seed.main() == 0

    dst = base / "config" / "companies_of_interest.txt"
    assert dst.exists()
    assert dst.read_text().splitlines() == ["Acme Corp", "Widget Co", "Globex"]

    events = _read_events(log_path)
    assert any(e["event"] == "companies_of_interest_derived" and e["count"] == 3 for e in events)


def test_idempotent_does_not_overwrite_existing(seed_env):
    seed, base, _ = seed_env
    (base / "config" / "target_companies.md").write_text(
        "## Tier 1\n- Acme\n",
        encoding="utf-8",
    )
    dst = base / "config" / "companies_of_interest.txt"
    dst.write_text("UserEditedList\n", encoding="utf-8")

    assert seed.main() == 0

    assert dst.read_text() == "UserEditedList\n"


def test_noop_when_source_missing(seed_env):
    seed, base, log_path = seed_env

    assert seed.main() == 0

    assert not (base / "config" / "companies_of_interest.txt").exists()
    assert _read_events(log_path) == []


def test_skip_when_no_tier1_section(seed_env):
    seed, base, log_path = seed_env
    (base / "config" / "target_companies.md").write_text(
        "# Target companies\n\n## Tier 2\n- Acme\n",
        encoding="utf-8",
    )

    assert seed.main() == 0

    assert not (base / "config" / "companies_of_interest.txt").exists()
    events = _read_events(log_path)
    assert any(e["event"] == "companies_of_interest_derive_skip" and e["reason"] == "no_tier1_section" for e in events)
