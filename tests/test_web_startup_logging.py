"""Startup log lines from the web app reach the container's stdout (#1049).

uvicorn's ``--log-level`` configures only uvicorn's own loggers, so the
``findajob`` loggers had no handler and their INFO lines — the auth status
and the one-time ``FINDAJOB_SETUP_TOKEN`` — were dropped before reaching
``docker logs``. Logging configuration is process-global and pytest installs
its own capture handlers, so each case runs ``default_app()`` in a fresh
interpreter, the way uvicorn's ``--factory`` does at container start.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
TOKEN_LINE = re.compile(r"FINDAJOB_SETUP_TOKEN=[A-Za-z0-9_-]{20,}")


def _run(tmp_path: Path, code: str, **env_overrides: str) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if k not in ("FINDAJOB_AUTH_USER", "FINDAJOB_AUTH_PASS")}
    env["JSP_BASE"] = str(tmp_path)
    env["PYTHONPATH"] = str(SRC)
    env.update(env_overrides)
    return subprocess.run(
        [sys.executable, "-c", code],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )


BOOT = "from findajob.web.app import default_app; default_app()"


def test_setup_token_reaches_stdout_without_credentials(tmp_path: Path) -> None:
    result = _run(tmp_path, BOOT)

    assert TOKEN_LINE.search(result.stdout), result.stdout + result.stderr
    assert "basic auth: DISABLED" in result.stdout


def test_setup_token_reaches_stdout_with_partial_credentials(tmp_path: Path) -> None:
    result = _run(tmp_path, BOOT, FINDAJOB_AUTH_USER="operator")

    assert TOKEN_LINE.search(result.stdout), result.stdout + result.stderr
    assert "FINDAJOB_AUTH_PASS is empty" in result.stdout


def test_auth_enabled_line_reaches_stdout_with_credentials(tmp_path: Path) -> None:
    result = _run(tmp_path, BOOT, FINDAJOB_AUTH_USER="operator", FINDAJOB_AUTH_PASS="correct-horse")

    assert "basic auth: ENABLED" in result.stdout
    assert "FINDAJOB_SETUP_TOKEN" not in result.stdout + result.stderr


def test_repeated_factory_calls_do_not_duplicate_lines(tmp_path: Path) -> None:
    result = _run(tmp_path, f"{BOOT}; default_app()")

    # One token per call; a second handler would print the second token twice.
    assert len(TOKEN_LINE.findall(result.stdout)) == 2


def test_existing_logging_configuration_is_left_alone(tmp_path: Path) -> None:
    code = f"import logging, sys; logging.basicConfig(stream=sys.stderr, level=logging.INFO); {BOOT}"
    result = _run(tmp_path, code)

    assert "FINDAJOB_SETUP_TOKEN" not in result.stdout
    assert len(TOKEN_LINE.findall(result.stderr)) == 1
