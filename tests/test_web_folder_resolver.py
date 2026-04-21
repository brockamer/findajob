"""Tests for fingerprint → folder path resolution."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from findajob.web.folder_resolver import resolve_folder


@pytest.fixture
def companies_root(tmp_path: Path) -> Path:
    (tmp_path / "Meta_SWE_2026-04-20_120000").mkdir()
    (tmp_path / "_applied" / "Google_PM_2026-04-15_100000").mkdir(parents=True)
    (tmp_path / "_waitlisted" / "Stripe_DE_2026-04-10_090000").mkdir(parents=True)
    (tmp_path / "_rejected" / "X_SRE_2026-04-01_080000").mkdir(parents=True)
    return tmp_path


@pytest.fixture
def db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE jobs (fingerprint TEXT PRIMARY KEY, prep_folder_path TEXT, stage TEXT)")
    return conn


def _seed(db: sqlite3.Connection, fp: str, folder: str, stage: str = "materials_drafted") -> None:
    db.execute(
        "INSERT INTO jobs (fingerprint, prep_folder_path, stage) VALUES (?, ?, ?)",
        (fp, folder, stage),
    )


def test_resolve_active_folder(companies_root: Path, db: sqlite3.Connection) -> None:
    _seed(db, "fp-active", str(companies_root / "Meta_SWE_2026-04-20_120000"))
    result = resolve_folder("fp-active", db, companies_root)
    assert result == companies_root / "Meta_SWE_2026-04-20_120000"


def test_resolve_applied_folder(companies_root: Path, db: sqlite3.Connection) -> None:
    _seed(
        db,
        "fp-applied",
        str(companies_root / "_applied" / "Google_PM_2026-04-15_100000"),
        stage="applied",
    )
    result = resolve_folder("fp-applied", db, companies_root)
    assert result == companies_root / "_applied" / "Google_PM_2026-04-15_100000"


def test_resolve_unknown_fingerprint_returns_none(companies_root: Path, db: sqlite3.Connection) -> None:
    assert resolve_folder("fp-unknown", db, companies_root) is None


def test_resolve_folder_missing_on_disk_returns_none(companies_root: Path, db: sqlite3.Connection) -> None:
    _seed(db, "fp-ghost", str(companies_root / "Ghost_Folder_Never_Existed"))
    assert resolve_folder("fp-ghost", db, companies_root) is None


def test_resolve_null_prep_folder_path_returns_none(companies_root: Path, db: sqlite3.Connection) -> None:
    db.execute(
        "INSERT INTO jobs (fingerprint, prep_folder_path, stage) VALUES (?, NULL, 'scored')",
        ("fp-nopath",),
    )
    assert resolve_folder("fp-nopath", db, companies_root) is None


def test_rejects_absolute_path_outside_root(
    companies_root: Path, db: sqlite3.Connection, tmp_path_factory: pytest.TempPathFactory
) -> None:
    outside = tmp_path_factory.mktemp("outside")
    _seed(db, "fp-outside", str(outside))
    assert resolve_folder("fp-outside", db, companies_root) is None


def test_rejects_dotdot_traversal(companies_root: Path, db: sqlite3.Connection) -> None:
    malicious = str(companies_root / ".." / "outside-root")
    _seed(db, "fp-traversal", malicious)
    assert resolve_folder("fp-traversal", db, companies_root) is None


def test_rejects_symlink_escaping_root(
    companies_root: Path,
    db: sqlite3.Connection,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    outside_target = tmp_path_factory.mktemp("outside_target")
    link = companies_root / "escape_link"
    link.symlink_to(outside_target)
    _seed(db, "fp-symlink", str(link))
    assert resolve_folder("fp-symlink", db, companies_root) is None
