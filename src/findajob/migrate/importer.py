"""Stack importer — pushes a tarball into a Fly app's volume (#816).

Orchestration:

1. **Pre-flight** (unless ``--force``): ssh ``ls /app/state/manifest.json``
   and refuse if it returns 0 — that means a prior migration ran and a
   second pass would clobber the tester's state.
2. **Upload**: sftp put the tarball to ``/tmp/<basename>`` on the Fly
   machine. Single-file, atomic from the Fly side's perspective.
3. **Extract**: ssh ``tar -xzf /tmp/<name> -C /app/state``.
4. **Cleanup**: ssh ``rm /tmp/<name>`` — uploaded artifact removed even
   if a later step fails (small disk-leak otherwise).
5. **Verify**: ssh ``python -m findajob.migrate verify --state-dir
   /app/state``, which emits a JSON :class:`~findajob.migrate.verifier.VerifyResult`-
   shaped payload that this side parses and surfaces.

The :class:`Transport` protocol decouples the orchestration from the
``fly`` CLI so tests can inject a :class:`FakeTransport`. The real
implementation lives at :class:`FlyTransport` and shells out to
``fly ssh sftp`` / ``fly ssh console -C``.
"""

from __future__ import annotations

import json
import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


class TargetNotEmptyError(RuntimeError):
    """Raised when the Fly volume already contains a manifest.json
    (prior migration). Use ``force=True`` to override."""


class RemoteCommandError(RuntimeError):
    """Raised when a remote ssh command exits non-zero unexpectedly."""


class Transport(Protocol):
    def sftp_put(self, local: Path, remote: str) -> None: ...

    def run_cmd(self, cmd: str) -> tuple[str, str, int]: ...


@dataclass
class ImportResult:
    failures: list[str] = field(default_factory=list)
    observed: dict[str, object] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.failures


def import_to_fly(
    *,
    tarball: Path,
    transport: Transport,
    force: bool = False,
) -> ImportResult:
    """Orchestrate the import. See module docstring for the 5-step flow."""
    if not tarball.exists():
        raise FileNotFoundError(f"tarball not found: {tarball}")

    remote_path = f"/tmp/{tarball.name}"

    if not force:
        _, _, rc = transport.run_cmd("ls /app/state/manifest.json")
        if rc == 0:
            raise TargetNotEmptyError(
                "/app/state/manifest.json already exists on target — a prior migration "
                "ran. Pass force=True to clobber, or use a freshly-deployed Fly app."
            )

    transport.sftp_put(tarball, remote_path)

    quoted_remote = shlex.quote(remote_path)
    _, stderr_extract, rc_extract = transport.run_cmd(f"tar -xzf {quoted_remote} -C /app/state")
    if rc_extract != 0:
        # Try to clean up the upload before bailing.
        transport.run_cmd(f"rm {quoted_remote}")
        raise RemoteCommandError(f"remote tar extract failed (rc={rc_extract}): {stderr_extract.strip()}")

    transport.run_cmd(f"rm {quoted_remote}")

    stdout_verify, stderr_verify, rc_verify = transport.run_cmd(
        "python -m findajob.migrate verify --state-dir /app/state"
    )
    if rc_verify != 0 and not stdout_verify.strip():
        raise RemoteCommandError(f"remote verify failed (rc={rc_verify}): {stderr_verify.strip()}")

    try:
        payload = json.loads(stdout_verify)
    except json.JSONDecodeError as e:
        raise RemoteCommandError(f"remote verify did not emit JSON: {e}; stdout was: {stdout_verify!r}") from e

    return ImportResult(
        failures=list(payload.get("failures", [])),
        observed=dict(payload.get("observed", {})),
    )


@dataclass
class FlyTransport:
    """Real transport — shells out to ``fly ssh sftp`` and
    ``fly ssh console -C``. ``app`` is the Fly app slug."""

    app: str

    def sftp_put(self, local: Path, remote: str) -> None:
        # fly ssh sftp shell expects sftp commands on stdin.
        sftp_cmd = f"put {shlex.quote(str(local))} {shlex.quote(remote)}\n"
        proc = subprocess.run(
            ["fly", "ssh", "sftp", "shell", "--app", self.app],
            input=sftp_cmd,
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            raise RemoteCommandError(f"fly ssh sftp put failed (rc={proc.returncode}): {proc.stderr.strip()}")

    def run_cmd(self, cmd: str) -> tuple[str, str, int]:
        proc = subprocess.run(
            ["fly", "ssh", "console", "--app", self.app, "-C", cmd],
            capture_output=True,
            text=True,
            check=False,
        )
        return (proc.stdout, proc.stderr, proc.returncode)
