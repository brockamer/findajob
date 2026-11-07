"""Tests for the prefilter/Target-Role overlap warning in health_check (#1265).

A hard-reject prefilter rule that also matches a phrase in the candidate's
own stated Target Role would silently score-1 a job the candidate wants.
``_prefilter_target_overlap_issues`` surfaces that conflict in the daily
health check. Each test points ``config_loader._RULES_PATH`` at its own tmp
YAML so the fixture file shared by the rest of the suite can't leak in.
"""

from __future__ import annotations

from findajob import config_loader
from findajob.notifications.health_check import _prefilter_target_overlap_issues

PROFILE_WITH_TARGET = """\
## Target Roles

Site Reliability Engineer, Platform Engineer

Not open to: Registered Nurse roles
"""


def _write_rules(tmp_path, text: str):
    path = tmp_path / "prefilter_rules.yaml"
    path.write_text(text)
    return path


class TestPrefilterTargetOverlapWarns:
    def test_prefilter_target_overlap_warns_with_category_and_pattern(self, monkeypatch, tmp_path):
        rules_path = _write_rules(
            tmp_path,
            """
hard_rejects:
  software:
    - "Platform Engineer"
""",
        )
        monkeypatch.setattr(config_loader, "_RULES_PATH", rules_path)

        lines = _prefilter_target_overlap_issues(PROFILE_WITH_TARGET)

        assert lines
        assert "WARN: 1 prefilter rule(s) hard-reject a phrase your Target Role asks for:" in lines[0]
        assert any("software" in line and "Platform Engineer" in line for line in lines[1:])


class TestPrefilterTargetOverlapIgnoresNotOpenTo:
    def test_prefilter_target_overlap_ignores_not_open_to(self, monkeypatch, tmp_path):
        rules_path = _write_rules(
            tmp_path,
            """
hard_rejects:
  healthcare:
    - "Registered Nurse"
""",
        )
        monkeypatch.setattr(config_loader, "_RULES_PATH", rules_path)

        lines = _prefilter_target_overlap_issues(PROFILE_WITH_TARGET)

        assert lines == []


class TestPrefilterTargetOverlapRespectsSuppressor:
    def test_prefilter_target_overlap_respects_suppressor(self, monkeypatch, tmp_path):
        rules_path = _write_rules(
            tmp_path,
            """
hard_rejects:
  software:
    - "Platform Engineer"
context_suppressors:
  - "Site Reliability"
""",
        )
        monkeypatch.setattr(config_loader, "_RULES_PATH", rules_path)

        lines = _prefilter_target_overlap_issues(PROFILE_WITH_TARGET)

        assert lines == []


class TestPrefilterTargetOverlapWithoutProfileIsSilent:
    def test_prefilter_target_overlap_without_profile_is_silent(self, monkeypatch, tmp_path):
        rules_path = _write_rules(
            tmp_path,
            """
hard_rejects:
  software:
    - "Platform Engineer"
""",
        )
        monkeypatch.setattr(config_loader, "_RULES_PATH", rules_path)

        assert _prefilter_target_overlap_issues("") == []

        profile_no_target_section = "## Identity\n\nName: Someone\n"
        assert _prefilter_target_overlap_issues(profile_no_target_section) == []


class TestPrefilterTargetOverlapDoesNotQuoteProfile:
    def test_prefilter_target_overlap_does_not_quote_profile(self, monkeypatch, tmp_path):
        rules_path = _write_rules(
            tmp_path,
            """
hard_rejects:
  software:
    - "Platform Engineer"
""",
        )
        monkeypatch.setattr(config_loader, "_RULES_PATH", rules_path)

        secret_profile = """\
## Target Roles

Site Reliability Engineer, Platform Engineer at TopSecret Corp Classified Project
"""

        lines = _prefilter_target_overlap_issues(secret_profile)

        assert lines
        joined = "\n".join(lines)
        assert "TopSecret Corp Classified Project" not in joined
        assert "software" in joined
        assert "Platform Engineer" in joined
