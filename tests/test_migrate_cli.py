"""CLI dispatch tests (#816)."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from findajob.migrate import __main__ as cli


def _build_stack(state: Path) -> None:
    (state / "data").mkdir(parents=True)
    (state / "companies" / "foo").mkdir(parents=True)
    (state / "candidate_context").mkdir(parents=True)
    db = state / "data" / "pipeline.db"
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE jobs (id INTEGER PRIMARY KEY)")
    conn.execute("CREATE TABLE audit_log (id INTEGER PRIMARY KEY)")
    conn.execute("CREATE TABLE feedback_log (id INTEGER PRIMARY KEY)")
    conn.execute("CREATE TABLE cost_log (id INTEGER PRIMARY KEY)")
    conn.commit()
    conn.close()
    (state / "companies" / "foo" / "x.md").write_text("x\n")
    (state / "candidate_context" / "profile.md").write_text("p\n")


def test_export_subcommand_writes_tarball(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    state = tmp_path / "state"
    _build_stack(state)
    tarball = tmp_path / "out.tar.gz"
    rc = cli.main(
        [
            "export",
            "--state-dir",
            str(state),
            "--tarball",
            str(tarball),
            "--stack-tag",
            "findajob-test",
        ]
    )
    assert rc == 0
    assert tarball.exists()
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["dry_run"] is False
    assert payload["manifest"]["source_stack_tag"] == "findajob-test"


def test_export_dry_run_no_tarball(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    state = tmp_path / "state"
    _build_stack(state)
    tarball = tmp_path / "out.tar.gz"
    rc = cli.main(
        [
            "export",
            "--state-dir",
            str(state),
            "--tarball",
            str(tarball),
            "--stack-tag",
            "findajob-test",
            "--dry-run",
        ]
    )
    assert rc == 0
    assert not tarball.exists()
    payload = json.loads(capsys.readouterr().out)
    assert payload["dry_run"] is True


def test_export_missing_state_exits_2(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc = cli.main(
        [
            "export",
            "--state-dir",
            str(tmp_path / "nope"),
            "--tarball",
            str(tmp_path / "out.tar.gz"),
            "--stack-tag",
            "x",
        ]
    )
    assert rc == 2
    assert "error" in capsys.readouterr().err


def test_verify_subcommand_emits_ok_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """End-to-end: export, extract, then ``python -m findajob.migrate verify``
    against the extracted dir emits the JSON shape the importer expects."""
    state = tmp_path / "state"
    _build_stack(state)
    tarball = tmp_path / "out.tar.gz"
    cli.main(
        [
            "export",
            "--state-dir",
            str(state),
            "--tarball",
            str(tarball),
            "--stack-tag",
            "findajob-test",
        ]
    )
    capsys.readouterr()  # drop export output

    extracted = tmp_path / "extracted"
    import tarfile

    with tarfile.open(tarball, "r:gz") as tar:
        tar.extractall(extracted)

    rc = cli.main(["verify", "--state-dir", str(extracted)])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["failures"] == []
    assert "manifest_path" in payload
    assert "observed" in payload


def test_verify_missing_manifest_emits_failure_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Even on missing manifest, the verify subcommand must emit JSON
    (importer expects machine-parseable output unconditionally)."""
    empty = tmp_path / "empty"
    empty.mkdir()
    rc = cli.main(["verify", "--state-dir", str(empty)])
    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert any("manifest" in f.lower() for f in payload["failures"])


def test_module_runs_as_python_dash_m(tmp_path: Path) -> None:
    """python -m findajob.migrate must work as the actual ssh-side invocation."""
    state = tmp_path / "state"
    _build_stack(state)
    tarball = tmp_path / "out.tar.gz"
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "findajob.migrate",
            "export",
            "--state-dir",
            str(state),
            "--tarball",
            str(tarball),
            "--stack-tag",
            "findajob-test",
            "--dry-run",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["dry_run"] is True
