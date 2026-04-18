"""Regression guard (#74): every notify.py subcommand in ops/crontab must be
a valid key in scripts/notify.py's COMMANDS dispatcher.

PR #72 shipped three invalid invocations — `stats`, `issues`, `feedback` —
that silently failed at runtime because supercronic fires the command, the
process prints a usage line, and exits 1 with no operator-visible signal.
"""

from __future__ import annotations

import ast
import pathlib
import re

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
CRONTAB = REPO_ROOT / "ops" / "crontab"
NOTIFY = REPO_ROOT / "scripts" / "notify.py"

_CRON_NOTIFY_RE = re.compile(r"notify\.py\s+([a-z0-9-]+)")


def _notify_commands() -> set[str]:
    """Parse scripts/notify.py and return the keys of the top-level COMMANDS dict."""
    tree = ast.parse(NOTIFY.read_text())
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == "COMMANDS"
            and isinstance(node.value, ast.Dict)
        ):
            keys: set[str] = set()
            for k in node.value.keys:
                assert isinstance(k, ast.Constant) and isinstance(k.value, str)
                keys.add(k.value)
            return keys
    raise AssertionError("COMMANDS dict not found in scripts/notify.py")


def _crontab_notify_subcommands() -> list[str]:
    """Extract every `notify.py <subcmd>` invocation from non-comment crontab lines."""
    subs: list[str] = []
    for line in CRONTAB.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        subs.extend(m.group(1) for m in _CRON_NOTIFY_RE.finditer(line))
    return subs


def test_notify_commands_dict_is_parseable():
    cmds = _notify_commands()
    assert cmds, "COMMANDS dict is empty or missing"
    for expected in {"daily-stats", "health-check", "issues-ping", "apply-reminder", "feedback-review"}:
        assert expected in cmds, f"Expected COMMANDS to contain '{expected}'"


def test_crontab_extracts_notify_subcommands():
    subs = _crontab_notify_subcommands()
    assert subs, "No notify.py invocations found in ops/crontab"


def test_every_crontab_notify_subcommand_is_valid():
    valid = _notify_commands()
    invoked = _crontab_notify_subcommands()
    unknown = [s for s in invoked if s not in valid]
    assert not unknown, (
        f"ops/crontab invokes notify.py subcommands not in COMMANDS: {unknown}. Valid subcommands: {sorted(valid)}"
    )
