"""Tests for scripts/render_crontab.py — #344 scheduled-jobs config layer."""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from render_crontab import RenderError, render  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
LIVE_YAML = REPO_ROOT / "ops" / "scheduled-jobs.yaml"


def _load_live() -> dict:
    return yaml.safe_load(LIVE_YAML.read_text(encoding="utf-8"))


def _executable_lines(rendered: str) -> list[tuple[str, str]]:
    """Extract (schedule, command) tuples for each non-comment cron line."""
    out: list[tuple[str, str]] = []
    for raw in rendered.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line and re.match(r"^[A-Z_][A-Z0-9_]*=", line):
            # env directive — not a job line
            continue
        # supercronic cron format: 5 schedule fields + command
        parts = line.split(None, 5)
        if len(parts) >= 6:
            schedule = " ".join(parts[:5])
            command = parts[5]
            out.append((schedule, command))
    return out


# ── Pure render() unit tests ──────────────────────────────────────────────


def test_render_emits_header_and_python_unbuffered() -> None:
    out = render({"jobs": {}}, {})
    assert "RENDERED at container start" in out
    assert "PYTHONUNBUFFERED=1" in out


def test_render_single_enabled_job() -> None:
    yaml_data = {
        "jobs": {
            "triage": {
                "schedule": "0 0 * * *",
                "command": "python3 /app/scripts/triage.py",
                "description": "Daily ingest.",
                "enabled": True,
            }
        }
    }
    out = render(yaml_data, {})
    assert "# triage: Daily ingest." in out
    assert "0 0 * * *   python3 /app/scripts/triage.py" in out


def test_render_disabled_job_is_a_comment_not_a_line() -> None:
    yaml_data = {
        "jobs": {
            "scoreboard": {
                "schedule": "30 8 * * 1",
                "command": "python3 /app/scripts/notify.py scoreboard",
                "description": "Weekly scoreboard.",
                "enabled": False,
            }
        }
    }
    out = render(yaml_data, {})
    assert "# scoreboard: DISABLED — Weekly scoreboard." in out
    assert _executable_lines(out) == []


def test_render_enabled_defaults_to_true_when_omitted() -> None:
    yaml_data = {"jobs": {"j": {"schedule": "* * * * *", "command": "true"}}}
    out = render(yaml_data, {})
    assert _executable_lines(out) == [("* * * * *", "true")]


def test_render_preserves_yaml_insertion_order() -> None:
    yaml_data = {
        "jobs": {
            "first": {"schedule": "1 1 * * *", "command": "a"},
            "second": {"schedule": "2 2 * * *", "command": "b"},
            "third": {"schedule": "3 3 * * *", "command": "c"},
        }
    }
    out = render(yaml_data, {})
    assert out.index("first") < out.index("second") < out.index("third")


# ── Env-var overrides ─────────────────────────────────────────────────────


def test_env_schedule_override_replaces_yaml_value() -> None:
    yaml_data = {"jobs": {"triage": {"schedule": "0 0 * * *", "command": "x"}}}
    env = {"FINDAJOB_TRIAGE_SCHEDULE": "30 1 * * *"}
    out = render(yaml_data, env)
    assert ("30 1 * * *", "x") in _executable_lines(out)
    assert ("0 0 * * *", "x") not in _executable_lines(out)


def test_env_enabled_false_drops_an_active_yaml_job() -> None:
    yaml_data = {"jobs": {"watchdog": {"schedule": "*/10 * * * *", "command": "x", "enabled": True}}}
    env = {"FINDAJOB_WATCHDOG_ENABLED": "false"}
    assert _executable_lines(render(yaml_data, env)) == []


def test_env_enabled_true_re_enables_a_disabled_yaml_job() -> None:
    yaml_data = {"jobs": {"scoreboard": {"schedule": "30 8 * * 1", "command": "x", "enabled": False}}}
    env = {"FINDAJOB_SCOREBOARD_ENABLED": "true"}
    assert _executable_lines(render(yaml_data, env)) == [("30 8 * * 1", "x")]


def test_env_var_name_handles_hyphenated_job_keys() -> None:
    """`notify-apply` → FINDAJOB_NOTIFY_APPLY_SCHEDULE."""
    yaml_data = {"jobs": {"notify-apply": {"schedule": "0 6 * * *", "command": "x"}}}
    env = {"FINDAJOB_NOTIFY_APPLY_SCHEDULE": "0 7 * * *"}
    assert _executable_lines(render(yaml_data, env)) == [("0 7 * * *", "x")]


def test_env_enabled_accepts_common_truthy_aliases() -> None:
    yaml_data = {"jobs": {"j": {"schedule": "* * * * *", "command": "x", "enabled": False}}}
    for val in ("true", "True", "1", "yes", "YES"):
        out = render(yaml_data, {"FINDAJOB_J_ENABLED": val})
        assert _executable_lines(out) == [("* * * * *", "x")], f"failed for {val!r}"


# ── Fail-fast behavior ────────────────────────────────────────────────────


def test_missing_jobs_key_raises() -> None:
    with pytest.raises(RenderError, match="missing or non-mapping `jobs` key"):
        render({}, {})


def test_jobs_not_a_mapping_raises() -> None:
    with pytest.raises(RenderError, match="missing or non-mapping `jobs` key"):
        render({"jobs": ["not", "a", "map"]}, {})


def test_job_spec_not_a_mapping_raises() -> None:
    with pytest.raises(RenderError, match="job 'triage' is not a mapping"):
        render({"jobs": {"triage": "0 0 * * * x"}}, {})


def test_missing_required_field_raises() -> None:
    with pytest.raises(RenderError, match="missing required field 'command'"):
        render({"jobs": {"j": {"schedule": "* * * * *"}}}, {})
    with pytest.raises(RenderError, match="missing required field 'schedule'"):
        render({"jobs": {"j": {"command": "x"}}}, {})


def test_unrecognized_enabled_override_raises() -> None:
    yaml_data = {"jobs": {"j": {"schedule": "* * * * *", "command": "x"}}}
    with pytest.raises(RenderError, match="unrecognized enabled override value"):
        render(yaml_data, {"FINDAJOB_J_ENABLED": "maybe"})


# ── Live YAML — migration safety ──────────────────────────────────────────


def test_live_yaml_renders_without_error() -> None:
    """The shipped ops/scheduled-jobs.yaml renders cleanly with no overrides."""
    rendered = render(_load_live(), {})
    assert "PYTHONUNBUFFERED=1" in rendered


def test_live_yaml_preserves_legacy_crontab_active_lines() -> None:
    """The set of (schedule, command) tuples produced by the YAML with no
    env overrides must match the legacy `ops/crontab` (after stashing it as
    `ops/crontab.legacy` for the migration). This locks the migration safety
    advisor cut #4: re-render without overrides must equal the legacy active
    set exactly, modulo ordering and whitespace.
    """
    legacy_path = REPO_ROOT / "ops" / "crontab.legacy"
    if not legacy_path.is_file():
        pytest.skip("ops/crontab.legacy not present (pre-migration check)")

    rendered = render(_load_live(), {})
    rendered_pairs = set(_executable_lines(rendered))

    legacy_text = legacy_path.read_text(encoding="utf-8")
    legacy_pairs = set(_executable_lines(legacy_text))

    # Disabled-in-YAML jobs (e.g., notify-scoreboard) are already commented
    # out in the legacy crontab, so they appear in neither set. Any genuinely
    # active job in legacy must appear in rendered with the same schedule +
    # command. Whitespace normalization happens via .split() in the helper.
    assert rendered_pairs == legacy_pairs, (
        f"rendered minus legacy: {rendered_pairs - legacy_pairs}\n"
        f"legacy minus rendered: {legacy_pairs - rendered_pairs}"
    )


def test_live_yaml_includes_canonical_active_jobs() -> None:
    """Positive smoke: known-active jobs must appear in rendered output."""
    rendered = render(_load_live(), {})
    pairs = _executable_lines(rendered)
    schedules = {sched for sched, _ in pairs}
    assert "0 0 * * *" in schedules  # triage
    assert "*/10 * * * *" in schedules  # watchdog
    assert "0 6 * * *" in schedules  # notify-apply
    assert "0 2 * * 0" in schedules  # discover
    assert "0 3 * * 0" in schedules  # rag-rebuild


def test_live_yaml_disables_scoreboard_until_112() -> None:
    """notify-scoreboard is intentionally disabled until #112 lands."""
    rendered = render(_load_live(), {})
    # Schedule line must NOT appear as an executable line
    assert ("30 8 * * 1", "python3 /app/scripts/notify.py scoreboard") not in _executable_lines(rendered)
    # ...but the disabled comment SHOULD appear
    assert "notify-scoreboard: DISABLED" in rendered
