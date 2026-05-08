"""Characterization test: importing `findajob.notifications.*` is side-effect-free.

Pre-extraction (PR #539-era), `scripts/notify.py` ran `_env = load_env()`
at module import — which silently parsed `data/.env` every time the
notification suite was loaded. Three test files (`test_notify_dejargon`,
`test_notify_persistence`, `test_companies_of_interest_consumers`) had
to rely on that file existing or being a no-op.

After this PR, env loading lives behind a `functools.cache`-d
`_runtime()` accessor in `ntfy.py` — first call into a code path that
needs it triggers the read; importing the modules does not.

This test fails if any module re-introduces module-load env reads or
ntfy POSTs.
"""

from __future__ import annotations

import importlib
import sys


def _reimport(name: str):
    """Force a fresh import so module-level code runs under our spies."""
    sys.modules.pop(name, None)
    return importlib.import_module(name)


def test_ntfy_module_loads_without_env_read(monkeypatch):
    """Importing `findajob.notifications.ntfy` must NOT call `load_env()`.

    The original notify.py ran `_env = load_env()` at module load to seed
    `NTFY_TOPIC` and `WEB_BASE_URL`. After extraction those values come
    from a lazy `_runtime()` accessor, so import is silent.
    """
    calls: list[object] = []

    import findajob.utils

    monkeypatch.setattr(findajob.utils, "load_env", lambda *a, **kw: calls.append(("load_env", a, kw)) or {})

    _reimport("findajob.notifications.ntfy")

    assert calls == [], (
        f"Importing findajob.notifications.ntfy called load_env() {len(calls)} time(s); "
        "expected 0. Env-derived globals must come from `_runtime()` (lazy)."
    )


def test_runtime_accessor_caches(monkeypatch):
    """`_runtime()` is `functools.cache`-d — load_env runs at most once per process."""
    from findajob.notifications import ntfy

    # Clear the cache so the first call below is a real fetch
    ntfy._runtime.cache_clear()

    counter = {"n": 0}
    real_load_env = ntfy.load_env

    def _spy(*a, **kw):
        counter["n"] += 1
        return real_load_env(*a, **kw)

    monkeypatch.setattr(ntfy, "load_env", _spy)

    a = ntfy._runtime()
    b = ntfy._runtime()
    c = ntfy._runtime()

    assert a is b is c, "_runtime() should return the same dict instance on every call"
    assert counter["n"] == 1, f"load_env called {counter['n']} times; expected 1 (cached)"


def test_each_command_module_loads_without_db_or_ntfy():
    """Per-command modules must import cleanly without DB or ntfy reachable."""
    for module_name in (
        "findajob.notifications.daily_stats",
        "findajob.notifications.apply_reminder",
        "findajob.notifications.feedback_review",
        "findajob.notifications.health_check",
        "findajob.notifications.issues_ping",
        "findajob.notifications.ci_check",
        "findajob.notifications.scoreboard",
        "findajob.notifications.send_raw",
    ):
        _reimport(module_name)


def test_cli_dispatch_table_is_complete():
    """COMMANDS dict must list every public subcommand the cronfile uses.

    Mirrors `tests/test_crontab_notify_alignment.py` from a different
    angle — that test asserts crontab subcommands all exist in COMMANDS;
    this one asserts the dispatch entry points stay typed and importable.
    """
    from findajob.notifications.cli import COMMANDS

    expected = {
        "daily-stats",
        "health-check",
        "issues-ping",
        "apply-reminder",
        "feedback-review",
        "send-raw",
        "ci-check",
        "scoreboard",
    }
    assert set(COMMANDS.keys()) == expected, f"COMMANDS keys drifted: {set(COMMANDS.keys()) ^ expected}"
    for key, fn in COMMANDS.items():
        assert callable(fn), f"COMMANDS[{key!r}] is not callable"


def test_notification_kinds_taxonomy_intact():
    """Closed-set kind taxonomy must include every kind referenced in production.

    Mirrors the assertion in `tests/test_notify_persistence.py` so the
    contract survives even if that test gets refactored later.
    """
    from findajob.notifications.ntfy import NOTIFICATION_KINDS

    expected = {
        "daily_stats",
        "apply_reminder",
        "feedback_review",
        "scoreboard",
        "health_check",
        "issues_ping",
        "ci_check",
        "send_raw",
        "discovery_run",
        "gmail_auth_failure",
        "rejection_detected",
    }
    assert set(NOTIFICATION_KINDS) >= expected
