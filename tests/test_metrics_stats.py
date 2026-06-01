"""Tests for findajob.metrics.stats — Wilson CI, min-N gate, stratification."""

from __future__ import annotations

import sqlite3

import pytest

from findajob.metrics.stats import (
    before_after_metrics,
    config_change_markers,
    min_n_gate,
    wilson_ci,
    wilson_ci_pct,
)
from tests.conftest import init_test_db


class TestWilsonCI:
    def test_zero_total(self):
        lo, hi = wilson_ci(0, 0)
        assert lo == 0.0
        assert hi == 0.0

    def test_negative_total(self):
        lo, hi = wilson_ci(0, -1)
        assert lo == 0.0
        assert hi == 0.0

    def test_all_successes(self):
        lo, hi = wilson_ci(100, 100)
        assert lo > 0.95
        assert hi > 0.99

    def test_no_successes(self):
        lo, hi = wilson_ci(0, 100)
        assert lo == 0.0
        assert hi < 0.05

    def test_half(self):
        lo, hi = wilson_ci(50, 100)
        assert 0.35 < lo < 0.45
        assert 0.55 < hi < 0.65

    def test_small_n_wide_interval(self):
        lo, hi = wilson_ci(1, 5)
        assert hi - lo > 0.3

    def test_large_n_narrow_interval(self):
        lo, hi = wilson_ci(500, 1000)
        assert hi - lo < 0.1

    def test_boundary_n_equals_1(self):
        lo, hi = wilson_ci(1, 1)
        assert lo > 0.0
        assert hi == 1.0

    def test_bounds_always_valid(self):
        for s in range(0, 21):
            lo, hi = wilson_ci(s, 20)
            assert 0.0 <= lo <= hi <= 1.0


class TestWilsonCIPct:
    def test_returns_three_floats(self):
        pct, lo, hi = wilson_ci_pct(50, 100)
        assert pct == 50.0
        assert lo < pct < hi

    def test_zero_total(self):
        pct, lo, hi = wilson_ci_pct(0, 0)
        assert pct == 0.0
        assert lo == 0.0
        assert hi == 0.0

    def test_rounding(self):
        pct, lo, hi = wilson_ci_pct(1, 3)
        assert isinstance(pct, float)
        assert isinstance(lo, float)
        assert isinstance(hi, float)


class TestMinNGate:
    def test_below_threshold(self):
        assert not min_n_gate(19)

    def test_at_threshold(self):
        assert min_n_gate(20)

    def test_above_threshold(self):
        assert min_n_gate(100)

    def test_zero(self):
        assert not min_n_gate(0)

    def test_custom_threshold(self):
        assert min_n_gate(5, threshold=5)
        assert not min_n_gate(4, threshold=5)


class TestConfigChangeMarkers:
    @pytest.fixture
    def db(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("""
            CREATE TABLE config_changes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                lever TEXT NOT NULL,
                changed_at TEXT DEFAULT (datetime('now')),
                changed_by TEXT DEFAULT 'manual',
                change_summary TEXT,
                content_hash TEXT,
                diff_summary TEXT
            )
        """)
        return conn

    def test_empty_table(self, db):
        markers = config_change_markers(db)
        assert markers == []

    def test_returns_markers(self, db):
        db.execute(
            "INSERT INTO config_changes (lever, changed_at, change_summary) VALUES (?, ?, ?)",
            ("scorer_prompt", "2026-05-20 12:00:00", "test change"),
        )
        db.commit()
        markers = config_change_markers(db)
        assert len(markers) == 1
        assert markers[0]["lever"] == "scorer_prompt"
        assert markers[0]["date"] == "2026-05-20"

    def test_date_filtering(self, db):
        from datetime import date

        db.execute(
            "INSERT INTO config_changes (lever, changed_at) VALUES (?, ?)",
            ("profile", "2026-05-10 12:00:00"),
        )
        db.execute(
            "INSERT INTO config_changes (lever, changed_at) VALUES (?, ?)",
            ("queries", "2026-05-20 12:00:00"),
        )
        db.commit()

        markers = config_change_markers(db, start_date=date(2026, 5, 15))
        assert len(markers) == 1
        assert markers[0]["lever"] == "queries"


class TestBeforeAfterMetrics:
    @pytest.fixture
    def db(self, tmp_path):
        # Build the real schema via the production migration chain rather
        # than hand-rolling CREATE TABLE — a fabricated cost_log with a
        # nonexistent `timestamp` column masked #953. init_test_db is the
        # documented anti-fragility helper (#721).
        db_path = tmp_path / "pipeline.db"
        init_test_db(db_path)
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def test_empty_tables(self, db):
        result = before_after_metrics(db, "2026-05-15")
        assert result["before"]["n_scored"] == 0
        assert result["after"]["n_scored"] == 0
        assert result["delta"]["precision_pct"] is None

    def test_before_window_counts(self, db):
        for i in range(5):
            db.execute(
                "INSERT INTO audit_log (job_id, field_changed, new_value, changed_at) VALUES (?, 'stage', 'scored', ?)",
                (f"job-before-{i}", "2026-05-12 10:00:00"),
            )
        db.execute(
            "INSERT INTO audit_log (job_id, field_changed, new_value, changed_at) VALUES (?, 'stage', 'rejected', ?)",
            ("job-before-rej", "2026-05-13 10:00:00"),
        )
        db.commit()
        result = before_after_metrics(db, "2026-05-15")
        assert result["before"]["n_scored"] == 5
        assert result["before"]["n_rejected"] == 1

    def test_after_window_counts(self, db):
        for i in range(3):
            db.execute(
                "INSERT INTO audit_log (job_id, field_changed, new_value, changed_at) VALUES (?, 'stage', 'scored', ?)",
                (f"job-after-{i}", "2026-05-17 10:00:00"),
            )
        db.commit()
        result = before_after_metrics(db, "2026-05-15")
        assert result["after"]["n_scored"] == 3

    def test_cost_in_both_windows(self, db):
        db.execute(
            "INSERT INTO cost_log (logged_at, cost_usd, operation, model) VALUES (?, ?, ?, ?)",
            ("2026-05-12 10:00:00", 1.50, "score", "scorer"),
        )
        db.execute(
            "INSERT INTO cost_log (logged_at, cost_usd, operation, model) VALUES (?, ?, ?, ?)",
            ("2026-05-17 10:00:00", 2.50, "score", "scorer"),
        )
        db.execute(
            "INSERT INTO audit_log (job_id, field_changed, new_value, changed_at) VALUES (?, 'stage', 'applied', ?)",
            ("job-before-app", "2026-05-13 10:00:00"),
        )
        db.execute(
            "INSERT INTO audit_log (job_id, field_changed, new_value, changed_at) VALUES (?, 'stage', 'applied', ?)",
            ("job-after-app", "2026-05-17 10:00:00"),
        )
        db.commit()
        result = before_after_metrics(db, "2026-05-15")
        assert result["before"]["total_cost"] == 1.50
        assert result["after"]["total_cost"] == 2.50
        assert result["before"]["cost_per_applied"] == 1.50
        assert result["after"]["cost_per_applied"] == 2.50

    def test_outside_window_excluded(self, db):
        db.execute(
            "INSERT INTO audit_log (job_id, field_changed, new_value, changed_at) VALUES (?, 'stage', 'scored', ?)",
            ("job-old", "2026-05-01 10:00:00"),
        )
        db.execute(
            "INSERT INTO audit_log (job_id, field_changed, new_value, changed_at) VALUES (?, 'stage', 'scored', ?)",
            ("job-far-future", "2026-05-30 10:00:00"),
        )
        db.commit()
        result = before_after_metrics(db, "2026-05-15")
        assert result["before"]["n_scored"] == 0
        assert result["after"]["n_scored"] == 0
