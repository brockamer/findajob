"""Tests for ops/ci-fly-deploy.sh — the CI auto-deploy + verify loop (#914).

The script deploys a prebuilt image to each Fly app in ``FLY_DEPLOY_APPS`` and
runs the mandated post-deploy auth-gate verify per app. Contract:

- clean no-op (exit 0) when ``FLY_DEPLOY_APPS`` is unset / empty / whitespace,
- deploy AND verify every app (split on any whitespace, incl. newlines),
- NOT fail-fast — every app attempted even if an earlier one failed,
- exit non-zero if ANY app's deploy or verify failed (the red CI run is the
  failure signal),
- never echo ``FLY_API_TOKEN``.

Driven via subprocess with a fake ``fly`` on PATH, so no real Fly API calls
happen. The fake logs each invocation's argv to ``$FLY_CALL_LOG`` and fails for
apps named in ``$FAKE_FLY_DEPLOY_FAIL_APPS`` / ``$FAKE_FLY_VERIFY_FAIL_APPS``.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "ops" / "ci-fly-deploy.sh"

_FAKE_FLY = r"""#!/usr/bin/env bash
# Fake flyctl. Logs argv (one line per call) to $FLY_CALL_LOG; fails for apps
# named in $FAKE_FLY_DEPLOY_FAIL_APPS / $FAKE_FLY_VERIFY_FAIL_APPS.
printf '%s\n' "$*" >> "$FLY_CALL_LOG"

# The app slug follows -a / --app.
app=""
prev=""
for tok in "$@"; do
  if [[ "$prev" == "-a" || "$prev" == "--app" ]]; then app="$tok"; break; fi
  prev="$tok"
done

case "$1" in
  deploy)
    for f in ${FAKE_FLY_DEPLOY_FAIL_APPS:-}; do
      [[ "$f" == "$app" ]] && { echo "fake: deploy fail $app" >&2; exit 1; }
    done
    echo "fake: deployed $app"
    ;;
  ssh)
    for f in ${FAKE_FLY_VERIFY_FAIL_APPS:-}; do
      [[ "$f" == "$app" ]] && { echo "fake: verify fail $app" >&2; exit 1; }
    done
    echo "fake: verified $app"
    ;;
  *)
    echo "fake: unhandled $*" >&2
    ;;
esac
"""


@pytest.fixture
def run_script(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_fly = bin_dir / "fly"
    fake_fly.write_text(_FAKE_FLY)
    fake_fly.chmod(0o755)
    call_log = tmp_path / "fly_calls.log"
    call_log.write_text("")

    def _run(
        *,
        apps: str | None = "",
        image: str = "ghcr.io/acme/findajob:v1.2.3",
        token: str = "SECRET-TOKEN-xyz",
        deploy_fail: str = "",
        verify_fail: str = "",
        unset_apps: bool = False,
    ):
        env = dict(os.environ)
        env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
        env["FLY_CALL_LOG"] = str(call_log)
        env["FLY_API_TOKEN"] = token
        env["IMAGE"] = image
        env["FLY_VERIFY_SETTLE_SECONDS"] = "0"  # no real settle wait under test
        env["FAKE_FLY_DEPLOY_FAIL_APPS"] = deploy_fail
        env["FAKE_FLY_VERIFY_FAIL_APPS"] = verify_fail
        if unset_apps:
            env.pop("FLY_DEPLOY_APPS", None)
        else:
            env["FLY_DEPLOY_APPS"] = apps or ""
        proc = subprocess.run(
            ["bash", str(SCRIPT)],
            env=env,
            capture_output=True,
            text=True,
        )
        calls = [c for c in call_log.read_text().splitlines() if c.strip()]
        return proc, calls

    return _run


def _deploys(calls):
    return [c for c in calls if c.startswith("deploy")]


def _verifies(calls):
    return [c for c in calls if c.startswith("ssh")]


# ── no-op cases ───────────────────────────────────────────────────────────


def test_noop_when_apps_empty(run_script):
    proc, calls = run_script(apps="")
    assert proc.returncode == 0, proc.stderr
    assert calls == [], "fly must not be invoked when no apps configured"


def test_noop_when_apps_unset(run_script):
    proc, calls = run_script(unset_apps=True)
    assert proc.returncode == 0, proc.stderr
    assert calls == []


def test_noop_when_apps_whitespace_only(run_script):
    proc, calls = run_script(apps="   \n \t ")
    assert proc.returncode == 0, proc.stderr
    assert calls == []


# ── happy path ────────────────────────────────────────────────────────────


def test_deploys_and_verifies_each_app(run_script):
    proc, calls = run_script(apps="app-one app-two")
    assert proc.returncode == 0, proc.stderr
    deploys, verifies = _deploys(calls), _verifies(calls)
    assert any("app-one" in c and "v1.2.3" in c for c in deploys)
    assert any("app-two" in c and "v1.2.3" in c for c in deploys)
    assert any("app-one" in c for c in verifies)
    assert any("app-two" in c for c in verifies)


def test_splits_on_newlines(run_script):
    # FLY_DEPLOY_APPS may be newline-separated (the documented secret format).
    proc, calls = run_script(apps="app-one\napp-two\napp-three")
    assert proc.returncode == 0, proc.stderr
    assert len(_deploys(calls)) == 3


# ── no fail-fast ──────────────────────────────────────────────────────────


def test_no_fail_fast_attempts_all_apps(run_script):
    # app-one's deploy fails; app-two MUST still be attempted.
    proc, calls = run_script(apps="app-one app-two", deploy_fail="app-one")
    assert proc.returncode != 0
    assert any("app-two" in c for c in _deploys(calls)), "app-two not attempted — fail-fast regression"


def test_verify_failure_fails_the_run(run_script):
    proc, calls = run_script(apps="app-one", verify_fail="app-one")
    assert proc.returncode != 0
    assert _verifies(calls), "verify should have been attempted"


def test_deploy_failure_skips_verify_for_that_app(run_script):
    # A failed deploy has nothing to verify — don't ssh into it.
    proc, calls = run_script(apps="app-one", deploy_fail="app-one")
    assert proc.returncode != 0
    assert not any("app-one" in c for c in _verifies(calls))


# ── secret hygiene ────────────────────────────────────────────────────────


def test_token_never_echoed(run_script):
    proc, _ = run_script(apps="app-one app-two", token="SUPERSECRET-DONT-LEAK")
    assert "SUPERSECRET-DONT-LEAK" not in (proc.stdout + proc.stderr)


def test_emits_log_mask_for_each_slug(run_script):
    # App slugs are operator topology — each must be registered as a GitHub
    # Actions log-mask token (`::add-mask::<slug>`) before deploy, so they're
    # redacted in the public Actions log regardless of secret line format.
    proc, _ = run_script(apps="app-one app-two")
    assert proc.returncode == 0, proc.stderr
    assert "::add-mask::app-one" in proc.stdout
    assert "::add-mask::app-two" in proc.stdout


# ── preflight ─────────────────────────────────────────────────────────────


def test_missing_image_with_apps_errors(run_script):
    proc, calls = run_script(apps="app-one", image="")
    assert proc.returncode != 0
    assert calls == [], "must error out before any deploy when IMAGE is empty"


def test_apps_set_but_token_empty_errors(run_script):
    # Partially configured (apps set, token forgotten): error out, deploy nothing.
    proc, calls = run_script(apps="app-one", token="")
    assert proc.returncode != 0
    assert calls == [], "must error out before any deploy when token is empty"
    assert "FLY_API_TOKEN" in proc.stderr
