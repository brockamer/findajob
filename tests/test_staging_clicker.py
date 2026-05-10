"""Staging clicker picker + sentinel-writeback unit tests (#565)."""

from __future__ import annotations

import json
import sqlite3
import urllib.request
from pathlib import Path

import pytest

from findajob.staging import clicker


@pytest.fixture
def staging_db(tmp_path: Path) -> Path:
    """Minimal pipeline.db with a few jobs in different stages."""
    db_path = tmp_path / "pipeline.db"
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE jobs (
            id INTEGER PRIMARY KEY,
            fingerprint TEXT UNIQUE,
            title TEXT,
            company TEXT,
            stage TEXT,
            score INTEGER,
            stage_updated TEXT,
            applied_at TEXT
        )
    """)
    rows = [
        (1, "fp_scored_a", "Project Coordinator", "Stripe", "scored", 7, "2026-05-09T10:00:00Z", None),
        (2, "fp_scored_b", "Operations Manager", "Notion", "scored", 8, "2026-05-09T11:00:00Z", None),
        (3, "fp_prep_done", "Operations Lead", "DataDog", "materials_drafted", 7, "2026-05-09T12:00:00+00:00", None),
        (4, "fp_applied", "Customer Ops", "Cloudflare", "applied", 6, "2026-05-09T13:00:00Z", "2026-05-09T13:00:00Z"),
        (5, "fp_rejected", "Coordinator", "Figma", "rejected", 4, "2026-05-09T14:00:00Z", None),
    ]
    conn.executemany("INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()
    return db_path


def test_pick_for_prep_returns_scored_job(staging_db: Path) -> None:
    fp = clicker._pick_for_prep(staging_db)
    assert fp in {"fp_scored_a", "fp_scored_b"}


def test_pick_for_prep_skips_already_drafted(staging_db: Path) -> None:
    """materials_drafted jobs must not be picked for prep."""
    for _ in range(20):
        fp = clicker._pick_for_prep(staging_db)
        assert fp != "fp_prep_done"


def test_pick_for_interview_returns_applied_job(staging_db: Path) -> None:
    fp = clicker._pick_for_interview(staging_db)
    assert fp == "fp_applied"


def test_pick_for_interview_returns_none_when_no_candidates(tmp_path: Path) -> None:
    """Empty applied stage means no candidate."""
    db_path = tmp_path / "pipeline.db"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE jobs (id INTEGER PRIMARY KEY, fingerprint TEXT, stage TEXT, applied_at TEXT)")
    conn.commit()
    conn.close()
    assert clicker._pick_for_interview(db_path) is None


def test_pick_for_advance_returns_old_drafted_job(staging_db: Path) -> None:
    """advance picks jobs in materials_drafted older than threshold."""
    fp = clicker._pick_for_advance(staging_db, threshold_hours=1)
    assert fp == "fp_prep_done"


def test_pick_for_advance_skips_recent_drafted(staging_db: Path) -> None:
    """advance does not pick jobs younger than threshold."""
    fp = clicker._pick_for_advance(staging_db, threshold_hours=24 * 365)
    assert fp is None


def test_speculative_target_picker(tmp_path: Path) -> None:
    """speculative reads from a target file and picks one company."""
    target_file = tmp_path / "speculative_targets.txt"
    target_file.write_text("# header\nStripe\nNotion\nDataDog\n")
    pick = clicker._pick_speculative_target(target_file)
    assert pick in {"Stripe", "Notion", "DataDog"}


def test_sentinel_writes_payload(tmp_path: Path) -> None:
    """The clicker writes a sentinel that green-check reads."""
    sentinel = tmp_path / ".staging_clicker_last_status"
    clicker._write_sentinel(sentinel, exit_code=0, mode="prep")
    payload = json.loads(sentinel.read_text())
    assert payload["exit_code"] == 0
    assert payload["mode"] == "prep"
    assert "timestamp" in payload


def test_run_speculative_posts_correct_form(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """_run_speculative must POST company=<urlencoded> with correct Content-Type."""
    target_file = tmp_path / "speculative_targets.txt"
    target_file.write_text("Stripe\n")

    captured: dict = {}

    class _FakeResp:
        status = 200

        def __enter__(self) -> _FakeResp:
            return self

        def __exit__(self, *a: object) -> bool:
            return False

    def fake_urlopen(req: urllib.request.Request, timeout: int | None = None) -> _FakeResp:
        captured["url"] = req.full_url
        captured["body"] = req.data
        captured["content_type"] = req.get_header("Content-type")
        return _FakeResp()

    monkeypatch.setattr(clicker.urllib.request, "urlopen", fake_urlopen)
    rc = clicker._run_speculative("http://localhost", target_file)

    assert rc == 0
    assert captured["url"].endswith("/ingest/speculative")
    assert captured["body"] == b"company=Stripe"
    assert captured["content_type"] == "application/x-www-form-urlencoded"
