"""Importer unit tests (#816).

Tests the orchestration of sftp put / ssh extract / remote verify with
a fake transport. Real ``fly ssh`` invocations are covered by the
dogfood round-trip against ``findajob-staging`` -> throwaway Fly app
(documented in the AC, not unit-testable here).
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from findajob.migrate import exporter, importer


@dataclass
class FakeTransport:
    """Records every put/run call. ``run_responses`` queues canned
    (stdout, stderr, returncode) tuples; each ``run_cmd`` call pops one."""

    puts: list[tuple[Path, str]] = field(default_factory=list)
    runs: list[str] = field(default_factory=list)
    run_responses: list[tuple[str, str, int]] = field(default_factory=list)

    def sftp_put(self, local: Path, remote: str) -> None:
        self.puts.append((local, remote))

    def run_cmd(self, cmd: str) -> tuple[str, str, int]:
        self.runs.append(cmd)
        if not self.run_responses:
            return ("", "", 0)
        return self.run_responses.pop(0)


def _build_tarball(tmp_path: Path) -> Path:
    state = tmp_path / "src"
    (state / "data").mkdir(parents=True)
    (state / "companies").mkdir()
    (state / "candidate_context").mkdir()
    db = state / "data" / "pipeline.db"
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE jobs (id INTEGER PRIMARY KEY)")
    conn.commit()
    conn.close()
    (state / "candidate_context" / "profile.md").write_text("# x\n")
    tarball = tmp_path / "stack.tar.gz"
    exporter.export(state_dir=state, tarball_path=tarball, source_stack_tag="findajob-x")
    return tarball


def _verify_ok_json() -> str:
    return json.dumps({"ok": True, "failures": [], "observed": {}, "manifest_path": "/app/state/manifest.json"})


def test_import_orchestration_uploads_extracts_and_verifies(tmp_path: Path) -> None:
    tarball = _build_tarball(tmp_path)
    transport = FakeTransport(
        run_responses=[
            ("", "ls: cannot access ...: No such file", 1),  # pre-flight: manifest absent
            ("", "", 0),  # tar extract
            ("", "", 0),  # rm /tmp/<name>
            (_verify_ok_json(), "", 0),  # remote verify
        ],
    )
    result = importer.import_to_fly(tarball=tarball, transport=transport)
    assert result.ok
    assert len(transport.puts) == 1
    local, remote = transport.puts[0]
    assert local == tarball
    assert remote.startswith("/tmp/")
    assert any("manifest.json" in c for c in transport.runs)
    assert any("tar -xzf" in c for c in transport.runs)
    assert any("findajob.migrate" in c and "verify" in c for c in transport.runs)


def test_import_refuses_when_target_already_has_manifest(tmp_path: Path) -> None:
    tarball = _build_tarball(tmp_path)
    transport = FakeTransport(
        run_responses=[
            ("manifest.json\n", "", 0),  # pre-flight: manifest present -> refuse
        ],
    )
    with pytest.raises(importer.TargetNotEmptyError):
        importer.import_to_fly(tarball=tarball, transport=transport)
    assert transport.puts == []


def test_import_refuses_missing_tarball(tmp_path: Path) -> None:
    transport = FakeTransport()
    with pytest.raises(FileNotFoundError):
        importer.import_to_fly(tarball=tmp_path / "no-such.tar.gz", transport=transport)


def test_import_force_skips_pre_flight_check(tmp_path: Path) -> None:
    """--force bypasses the manifest.json-presence guard. Operator takes
    responsibility for clobbering an existing migration."""
    tarball = _build_tarball(tmp_path)
    transport = FakeTransport(
        run_responses=[
            ("", "", 0),  # extract
            ("", "", 0),  # cleanup
            (_verify_ok_json(), "", 0),  # verify
        ],
    )
    result = importer.import_to_fly(tarball=tarball, transport=transport, force=True)
    assert result.ok
    assert not any("ls" in c and "manifest.json" in c for c in transport.runs)


def test_import_surfaces_verify_failures(tmp_path: Path) -> None:
    tarball = _build_tarball(tmp_path)
    bad_verify = json.dumps(
        {
            "ok": False,
            "failures": ["db_row_counts[jobs]: expected 3, observed 2"],
            "observed": {},
            "manifest_path": "/app/state/manifest.json",
        }
    )
    transport = FakeTransport(
        run_responses=[
            ("", "no such file", 1),  # pre-flight
            ("", "", 0),  # extract
            ("", "", 0),  # cleanup
            (bad_verify, "", 0),  # verify reports failure
        ],
    )
    result = importer.import_to_fly(tarball=tarball, transport=transport)
    assert not result.ok
    assert any("jobs" in f for f in result.failures)


def test_import_raises_on_extract_failure(tmp_path: Path) -> None:
    tarball = _build_tarball(tmp_path)
    transport = FakeTransport(
        run_responses=[
            ("", "no such file", 1),  # pre-flight
            ("", "tar: extract failed", 1),  # extract fails
        ],
    )
    with pytest.raises(importer.RemoteCommandError) as excinfo:
        importer.import_to_fly(tarball=tarball, transport=transport)
    assert "tar" in str(excinfo.value)


def test_import_attempts_cleanup_even_on_verify_failure(tmp_path: Path) -> None:
    """If verify fails, the uploaded /tmp tarball should still be
    cleaned up. The verify failure is then surfaced as a non-ok result."""
    tarball = _build_tarball(tmp_path)
    bad_verify = json.dumps({"ok": False, "failures": ["x"], "observed": {}, "manifest_path": ""})
    transport = FakeTransport(
        run_responses=[
            ("", "no such file", 1),  # pre-flight
            ("", "", 0),  # extract
            ("", "", 0),  # cleanup
            (bad_verify, "", 0),  # verify
        ],
    )
    result = importer.import_to_fly(tarball=tarball, transport=transport)
    assert not result.ok
    assert any(c.startswith("rm ") and "/tmp/" in c for c in transport.runs)
