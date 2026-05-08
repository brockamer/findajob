"""Regression guard (#74): every notify.py subcommand referenced in
ops/scheduled-jobs.yaml must be a valid key in scripts/notify.py's COMMANDS
dispatcher.

PR #72 shipped three invalid invocations — `stats`, `issues`, `feedback` —
that silently failed at runtime because supercronic fires the command, the
process prints a usage line, and exits 1 with no operator-visible signal.

Updated for #344: the source of scheduled jobs moved from `ops/crontab` to
`ops/scheduled-jobs.yaml`. The check still parses the YAML's command field
for `notify.py <subcmd>` patterns — covers both enabled and disabled jobs
(disabled jobs may be re-enabled via env var, so a stale subcommand in a
disabled job is still a latent bug).
"""

from __future__ import annotations

import ast
import pathlib
import re

import yaml

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
SCHEDULED_JOBS = REPO_ROOT / "ops" / "scheduled-jobs.yaml"
# Post-#537: the dispatch table moved from `scripts/notify.py` (now a 22-LOC
# shim) into `src/findajob/notifications/cli.py`.
NOTIFY_CLI = REPO_ROOT / "src" / "findajob" / "notifications" / "cli.py"

_CRON_NOTIFY_RE = re.compile(r"notify\.py\s+([a-z0-9-]+)")


def _notify_commands() -> set[str]:
    """Parse findajob.notifications.cli and return the keys of the top-level COMMANDS dict."""
    tree = ast.parse(NOTIFY_CLI.read_text())
    for node in tree.body:
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "COMMANDS"
            and isinstance(node.value, ast.Dict)
        ):
            keys: set[str] = set()
            for k in node.value.keys:
                assert isinstance(k, ast.Constant) and isinstance(k.value, str)
                keys.add(k.value)
            return keys
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == "COMMANDS"
            and isinstance(node.value, ast.Dict)
        ):
            keys = set()
            for k in node.value.keys:
                assert isinstance(k, ast.Constant) and isinstance(k.value, str)
                keys.add(k.value)
            return keys
    raise AssertionError(f"COMMANDS dict not found in {NOTIFY_CLI}")


def _crontab_notify_subcommands() -> list[str]:
    """Extract every `notify.py <subcmd>` from the YAML's command fields."""
    data = yaml.safe_load(SCHEDULED_JOBS.read_text())
    subs: list[str] = []
    for spec in data.get("jobs", {}).values():
        if not isinstance(spec, dict):
            continue
        cmd = spec.get("command", "")
        subs.extend(m.group(1) for m in _CRON_NOTIFY_RE.finditer(cmd))
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
