"""Tests for the prefilter/Target-Role overlap warning in health_check (#1265).

A hard-reject prefilter rule that also matches a phrase in the candidate's
own stated Target Role would silently score-1 a job the candidate wants.
``_prefilter_target_overlap_issues`` surfaces that conflict in the daily
health check. Each test points ``config_loader._RULES_PATH`` at its own tmp
YAML so the fixture file shared by the rest of the suite can't leak in.
"""

from __future__ import annotations

from findajob import config_loader
from findajob.notifications.health_check import _prefilter_target_overlap_issues, _read_profile_text

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


class TestPrefilterTargetOverlapSuppressorIsPerLine:
    """A suppressor on one Target Role line must not cancel a hard-reject
    match on a different, unrelated line (F1, round 2).

    Production (``scorer_prefilter._hard_reject_match``) evaluates one job
    title at a time, so a suppressor only ever cancels a reject for the same
    title it co-occurs with. A multi-role Target Role section is the
    multi-title analogue: checking the suppressor against the whole section
    (instead of the matched line) would let an unrelated suppressor silence
    a real conflict.
    """

    def test_unrelated_line_suppressor_does_not_cancel_match(self, monkeypatch, tmp_path):
        rules_path = _write_rules(
            tmp_path,
            """
hard_rejects:
  software:
    - "\\\\bplatform\\\\s+engineer\\\\b"
context_suppressors:
  - "\\\\bdata\\\\s*center\\\\b"
""",
        )
        monkeypatch.setattr(config_loader, "_RULES_PATH", rules_path)

        profile = "## Target Roles\n\n- Platform Engineer\n- Data Center Technician\n"
        lines = _prefilter_target_overlap_issues(profile)

        assert lines
        assert any("software" in line for line in lines[1:])

    def test_same_line_suppressor_still_cancels_match(self, monkeypatch, tmp_path):
        rules_path = _write_rules(
            tmp_path,
            """
hard_rejects:
  software:
    - "\\\\bplatform\\\\s+engineer\\\\b"
context_suppressors:
  - "\\\\bdata\\\\s*center\\\\b"
""",
        )
        monkeypatch.setattr(config_loader, "_RULES_PATH", rules_path)

        profile = "## Target Roles\n\n- Platform Engineer at a Data Center company\n"
        lines = _prefilter_target_overlap_issues(profile)

        assert lines == []


class TestPrefilterTargetOverlapMalformedConfigIsSilent:
    """A malformed prefilter_rules.yaml must not crash the daily health check
    (F2 / sec-SEC-1265-1, round 2) — it should be treated like the other
    non-fatal gaps this check already tolerates (no profile, invalid regex).
    """

    def test_bad_hard_rejects_shape_does_not_raise(self, monkeypatch, tmp_path):
        rules_path = _write_rules(tmp_path, "hard_rejects: 'not-a-mapping'\n")
        monkeypatch.setattr(config_loader, "_RULES_PATH", rules_path)

        assert _prefilter_target_overlap_issues(PROFILE_WITH_TARGET) == []

    def test_invalid_yaml_syntax_does_not_raise(self, monkeypatch, tmp_path):
        rules_path = _write_rules(tmp_path, "hard_rejects: [oops\n")
        monkeypatch.setattr(config_loader, "_RULES_PATH", rules_path)

        assert _prefilter_target_overlap_issues(PROFILE_WITH_TARGET) == []


class TestReadProfileText:
    """The BASE/candidate_context/profile.md read that feeds the overlap
    check, and its OSError fallback (F3, round 2) — a wrong path here would
    leave the whole feature silently dead while the pure-function tests above
    still pass.
    """

    def test_reads_existing_profile(self, tmp_path):
        (tmp_path / "candidate_context").mkdir()
        (tmp_path / "candidate_context" / "profile.md").write_text(PROFILE_WITH_TARGET)

        assert _read_profile_text(str(tmp_path)) == PROFILE_WITH_TARGET

    def test_missing_profile_returns_empty_string(self, tmp_path):
        assert _read_profile_text(str(tmp_path)) == ""
