"""Tests for spend_chip_context_for_template factory in app.py (#671).

Uses a real cost_log table (not a synthetic dict mock) so the real
spend_this_month() codepath is exercised. Threshold boundaries:
  - ratio < 0.90  → "normal"
  - 0.90 ≤ ratio < 1.0 → "warn"
  - ratio ≥ 1.0   → "crossed"
  - no ceiling    → "no_ceiling"
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from findajob import config_loader
from findajob.web.app import create_app


def _now_str() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")


def _build_db(db_path: Path) -> None:
    from findajob.db.migrate import apply_pending

    conn = sqlite3.connect(str(db_path))
    try:
        apply_pending(conn)
    finally:
        conn.close()


def _insert_cost(conn: sqlite3.Connection, amount: float) -> None:
    conn.execute(
        "INSERT INTO cost_log (operation, model, cost_usd, logged_at) VALUES (?,?,?,?)",
        ("test_op", "test-model", amount, _now_str()),
    )


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    p = tmp_path / "pipeline.db"
    _build_db(p)
    return p


@pytest.fixture()
def _ceiling_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Write spend_ceiling.txt = 50.00 and point config_loader at it."""
    p = tmp_path / "spend_ceiling.txt"
    p.write_text("50.00")
    monkeypatch.setattr(config_loader, "_SPEND_CEILING_PATH", p)
    return p


def _get_chip_fn(db_path: Path, tmp_path: Path):
    """Build app, extract the chip context callable registered as a Jinja global."""
    from findajob.onboarding import mark_complete

    companies = tmp_path / "companies"
    companies.mkdir(exist_ok=True)
    mark_complete(tmp_path)
    app = create_app(companies_root=companies, db_path=db_path, base_root=tmp_path)
    return app.state.templates.env.globals["spend_chip_context_for_template"]


# ── no ceiling ────────────────────────────────────────────────────────────────


def test_no_ceiling_returns_no_ceiling_state(db_path: Path, tmp_path: Path) -> None:
    """When spend_ceiling.txt is absent/None, state == 'no_ceiling'."""
    # conftest already points _SPEND_CEILING_PATH at a non-existent file → None
    chip = _get_chip_fn(db_path, tmp_path)
    ctx = chip()
    assert ctx["state"] == "no_ceiling"
    assert ctx["ceiling"] is None


# ── normal (<90%) ─────────────────────────────────────────────────────────────


def test_normal_when_ratio_below_90pct(db_path: Path, tmp_path: Path, _ceiling_file: Path) -> None:
    """Sum < 90% of ceiling → state == 'normal'."""
    conn = sqlite3.connect(str(db_path))
    _insert_cost(conn, 40.0)  # 40/50 = 0.80
    conn.commit()
    conn.close()

    chip = _get_chip_fn(db_path, tmp_path)
    ctx = chip()
    assert ctx["state"] == "normal"
    assert ctx["ceiling"] == pytest.approx(50.0)
    assert ctx["ratio"] == pytest.approx(0.80)


# ── warn (≥90%, <100%) ────────────────────────────────────────────────────────


def test_warn_when_ratio_exactly_90pct(db_path: Path, tmp_path: Path, _ceiling_file: Path) -> None:
    """Ratio == 0.90 exactly → state == 'warn' (boundary is ≥0.90)."""
    conn = sqlite3.connect(str(db_path))
    _insert_cost(conn, 45.0)  # 45/50 = 0.90
    conn.commit()
    conn.close()

    chip = _get_chip_fn(db_path, tmp_path)
    ctx = chip()
    assert ctx["state"] == "warn"


def test_warn_when_ratio_in_90_to_100_range(db_path: Path, tmp_path: Path, _ceiling_file: Path) -> None:
    """Ratio in [90%, 100%) → state == 'warn'."""
    conn = sqlite3.connect(str(db_path))
    _insert_cost(conn, 49.0)  # 49/50 = 0.98
    conn.commit()
    conn.close()

    chip = _get_chip_fn(db_path, tmp_path)
    ctx = chip()
    assert ctx["state"] == "warn"


# ── crossed (≥100%) ───────────────────────────────────────────────────────────


def test_crossed_when_ratio_exactly_100pct(db_path: Path, tmp_path: Path, _ceiling_file: Path) -> None:
    """Sum == ceiling exactly → state == 'crossed' (boundary is ≥1.0)."""
    conn = sqlite3.connect(str(db_path))
    _insert_cost(conn, 50.0)  # 50/50 = 1.00
    conn.commit()
    conn.close()

    chip = _get_chip_fn(db_path, tmp_path)
    ctx = chip()
    assert ctx["state"] == "crossed"


def test_crossed_when_sum_exceeds_ceiling(db_path: Path, tmp_path: Path, _ceiling_file: Path) -> None:
    """Sum > ceiling → state == 'crossed'."""
    conn = sqlite3.connect(str(db_path))
    _insert_cost(conn, 75.0)  # 75/50 = 1.50
    conn.commit()
    conn.close()

    chip = _get_chip_fn(db_path, tmp_path)
    ctx = chip()
    assert ctx["state"] == "crossed"
    assert ctx["ratio"] == pytest.approx(1.5)
