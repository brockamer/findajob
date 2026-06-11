import re
import pytest
from findajob import config_loader
from findajob.config_loader import (
    ConfigError,
    add_prefilter_title_pattern,
    remove_prefilter_title_pattern,
    load_hard_reject_rules,
)


@pytest.fixture(autouse=True)
def _tmp_rules(tmp_path, monkeypatch):
    rules = tmp_path / "prefilter_rules.yaml"
    monkeypatch.setattr(config_loader, "_RULES_PATH", rules)
    config_loader._reset_cache()
    yield rules
    config_loader._reset_cache()


def test_remove_deletes_the_pattern_and_keeps_others(_tmp_rules):
    add_prefilter_title_pattern(r"\bfleet\s+readiness\b", category="auto_added")
    add_prefilter_title_pattern(r"\bdirector\s+infrastructure\b", category="auto_added")

    remove_prefilter_title_pattern(r"\bfleet\s+readiness\b", category="auto_added")

    config_loader._reset_cache()
    reject_re, _ = load_hard_reject_rules()
    assert not reject_re.search("Regional Fleet Readiness Manager")
    assert reject_re.search("Director Infrastructure")


def test_remove_drops_empty_category(_tmp_rules):
    add_prefilter_title_pattern(r"\bstaff\s+engineer\b", category="auto_added")
    remove_prefilter_title_pattern(r"\bstaff\s+engineer\b", category="auto_added")
    import yaml
    data = yaml.safe_load(_tmp_rules.read_text()) or {}
    assert "auto_added" not in (data.get("hard_rejects") or {})


def test_remove_missing_pattern_raises(_tmp_rules):
    add_prefilter_title_pattern(r"\bstaff\s+engineer\b", category="auto_added")
    with pytest.raises(ConfigError):
        remove_prefilter_title_pattern(r"\bnope\b", category="auto_added")


def test_remove_missing_file_raises(_tmp_rules):
    with pytest.raises(ConfigError):
        remove_prefilter_title_pattern(r"\bx\b", category="auto_added")
