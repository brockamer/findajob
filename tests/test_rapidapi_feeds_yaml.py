"""Tests for the rapidapi_feeds.yaml curation loader (#408)."""

from __future__ import annotations

from pathlib import Path

import pytest

from findajob.fetchers.adapters.curation import (
    CurationLoadError,
    default_adapter,
    load_curation,
    recommend_for_class,
)

_VALID_YAML = """
default: jobs-api14

classes:
  - name: corporate-tech
    description: Corporate / tech / professional services
    recommended_adapter: jobs-api14
    rationale: LinkedIn-heavy

  - name: skilled-trades-regional
    description: Trades, regional employers
    recommended_adapter: jsearch
    rationale: Multi-board

adapters:
  - name: jobs-api14
    display_name: "Jobs API (jobs-api14)"
    rapidapi_url: https://rapidapi.com/Pat92/api/jobs-api14
    free_tier: 150 calls / month
    paid_tier: $5-25 / month
    required_env_var: JOBS_API14_KEY
    coverage:
      best_for: Corporate / tech
      worst_for: Trades / regional

  - name: jsearch
    display_name: JSearch
    rapidapi_url: https://rapidapi.com/letscrape-6bRBa3QguO5/api/jsearch
    free_tier: 200 calls / month
    paid_tier: $25 / month
    required_env_var: JSEARCH_API_KEY
    coverage:
      best_for: Multi-board aggregation
      worst_for: LinkedIn-only employers
"""


def _write(tmp_path: Path, content: str) -> Path:
    f = tmp_path / "rapidapi_feeds.yaml"
    f.write_text(content)
    return f


def test_load_valid_curation(tmp_path: Path) -> None:
    f = _write(tmp_path, _VALID_YAML)
    cur = load_curation(f)
    assert cur.default_name == "jobs-api14"
    assert len(cur.classes) == 2
    assert len(cur.adapters) == 2


def test_recommend_for_class_match(tmp_path: Path) -> None:
    f = _write(tmp_path, _VALID_YAML)
    cur = load_curation(f)
    rec = recommend_for_class(cur, "skilled-trades-regional")
    assert rec.name == "jsearch"
    assert rec.display_name == "JSearch"


def test_recommend_for_class_unknown_falls_back_to_default(tmp_path: Path) -> None:
    f = _write(tmp_path, _VALID_YAML)
    cur = load_curation(f)
    rec = recommend_for_class(cur, "no-such-class")
    assert rec.name == "jobs-api14"  # the default


def test_default_adapter(tmp_path: Path) -> None:
    f = _write(tmp_path, _VALID_YAML)
    cur = load_curation(f)
    assert default_adapter(cur).name == "jobs-api14"


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(CurationLoadError):
        load_curation(tmp_path / "missing.yaml")


def test_malformed_yaml_raises(tmp_path: Path) -> None:
    f = _write(tmp_path, "this is not: valid: yaml: [")
    with pytest.raises(CurationLoadError):
        load_curation(f)


def test_missing_default_field_raises(tmp_path: Path) -> None:
    f = _write(tmp_path, "classes: []\nadapters: []\n")
    with pytest.raises(CurationLoadError):
        load_curation(f)


def test_default_pointing_at_unknown_adapter_raises(tmp_path: Path) -> None:
    f = _write(tmp_path, "default: ghost\nclasses: []\nadapters:\n  - name: jobs-api14\n    display_name: X\n")
    with pytest.raises(CurationLoadError):
        load_curation(f)


def test_class_pointing_at_unknown_adapter_raises(tmp_path: Path) -> None:
    bad = """
default: jobs-api14
classes:
  - name: corporate-tech
    description: x
    recommended_adapter: ghost
    rationale: x
adapters:
  - name: jobs-api14
    display_name: X
"""
    f = _write(tmp_path, bad)
    with pytest.raises(CurationLoadError):
        load_curation(f)
