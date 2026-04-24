"""Tests for sync_sheet.py row-building and formula-generation logic."""

import sys
from unittest.mock import MagicMock, patch

import pytest

# Mock Google API modules before importing sync_sheet (module-level side effects)
sys.modules["google.oauth2"] = MagicMock()
sys.modules["google.oauth2.service_account"] = MagicMock()
sys.modules["googleapiclient"] = MagicMock()
sys.modules["googleapiclient.discovery"] = MagicMock()

# Mock the file read for sheet_id.txt at module level
_real_open = open


def _patched_open(path, *args, **kwargs):
    if "sheet_id.txt" in str(path):
        from io import StringIO

        return StringIO("fake-sheet-id\n")
    return _real_open(path, *args, **kwargs)


with patch("builtins.open", side_effect=_patched_open):
    from scripts.sync_sheet import (
        DASH_HEADERS,
        DASH_LOOKUP,
        build_row,
        hyperlink,
        materials_company_cell,
        safe_str,
    )


# ---------------------------------------------------------------------------
# FakeRow — mimics sqlite3.Row for build_row tests
# ---------------------------------------------------------------------------


class FakeRow:
    """Dict-like object that supports .keys() like sqlite3.Row."""

    def __init__(self, data):
        self._data = data

    def __getitem__(self, key):
        return self._data[key]

    def keys(self):
        return self._data.keys()


# ---------------------------------------------------------------------------
# hyperlink()
# ---------------------------------------------------------------------------


class TestHyperlink:
    def test_normal_url_and_label(self):
        result = hyperlink("https://example.com", "Click here")
        assert result == '=HYPERLINK("https://example.com","Click here")'

    def test_url_with_double_quotes(self):
        result = hyperlink('https://example.com/q="test"', "Label")
        assert result == '=HYPERLINK("https://example.com/q=%22test%22","Label")'

    def test_label_with_double_quotes(self):
        result = hyperlink("https://example.com", 'Say "hello"')
        assert result == '=HYPERLINK("https://example.com","Say ""hello""")'

    def test_empty_url_returns_plain_label(self):
        result = hyperlink("", "Plain label")
        assert result == "Plain label"

    def test_none_url_returns_plain_label(self):
        result = hyperlink(None, "Plain label")
        assert result == "Plain label"

    def test_none_label_returns_empty_hyperlink(self):
        result = hyperlink("https://example.com", None)
        assert result == '=HYPERLINK("https://example.com","")'

    def test_both_url_and_label_with_quotes(self):
        result = hyperlink('https://x.com/"path"', '"Title"')
        assert result == '=HYPERLINK("https://x.com/%22path%22","""Title""")'


# ---------------------------------------------------------------------------
# safe_str()
# ---------------------------------------------------------------------------


class TestSafeStr:
    def test_normal_string_unchanged(self):
        assert safe_str("hello") == "hello"

    def test_none_returns_empty(self):
        assert safe_str(None) == ""

    def test_leading_equals_prefixed(self):
        assert safe_str("=SUM(A1)") == "'=SUM(A1)"

    def test_leading_plus_prefixed(self):
        assert safe_str("+1234") == "'+1234"

    def test_leading_minus_prefixed(self):
        assert safe_str("-negative") == "'-negative"

    def test_leading_at_prefixed(self):
        assert safe_str("@mention") == "'@mention"

    def test_empty_string_unchanged(self):
        assert safe_str("") == ""

    def test_number_converted_to_string(self):
        assert safe_str(42) == "42"


# ---------------------------------------------------------------------------
# build_row() — Dashboard mode
# ---------------------------------------------------------------------------


def _make_row(**overrides):
    """Create a FakeRow with sensible defaults for all columns used by build_row."""
    defaults = {
        "fingerprint": "abc123",
        "apply_flag": 0,
        "reject_reason": "",
        "fit_score": 7,
        "probability_score": 80,
        "relevance_score": 8,
        "title": "Software Engineer",
        "company": "Acme Corp",
        "location": "Remote",
        "remote_status": "remote",
        "known_contacts": "",
        "comp_estimate": "$150k",
        "ai_notes": "Good fit",
        "created_at": "2026-04-10",
        "source": "linkedin",
        "url": "https://example.com/job",
        "stage": "scored",
        "gdrive_folder_url": "",
        "prep_folder_path": "",
    }
    defaults.update(overrides)
    return FakeRow(defaults)


class TestBuildRowDashboard:
    """build_row for Dashboard tab rows."""

    def test_materials_drafted_no_override_ready_to_apply(self):
        row = _make_row(stage="materials_drafted")
        result = build_row(row, DASH_HEADERS, DASH_LOOKUP)
        assert result[0] == "Ready to Apply"

    def test_scored_apply_flag_1_flag_for_prep(self):
        row = _make_row(stage="scored", apply_flag=1)
        result = build_row(row, DASH_HEADERS, DASH_LOOKUP)
        assert result[0] == "Flag for Prep"

    def test_scored_apply_flag_0_empty(self):
        row = _make_row(stage="scored", apply_flag=0)
        result = build_row(row, DASH_HEADERS, DASH_LOOKUP)
        assert result[0] == ""

    def test_status_override_used(self):
        row = _make_row(stage="scored", apply_flag=0)
        result = build_row(
            row,
            DASH_HEADERS,
            DASH_LOOKUP,
            status_override="Applied",
        )
        assert result[0] == "Applied"

    def test_reject_override_used(self):
        row = _make_row(reject_reason="")
        result = build_row(
            row,
            DASH_HEADERS,
            DASH_LOOKUP,
            reject_override="Low Fit Score",
        )
        # REJECT_REASON is at index 1 in DASH_HEADERS
        assert result[1] == "Low Fit Score"

    def test_reject_override_none_falls_back_to_db(self):
        row = _make_row(reject_reason="Wrong Level")
        result = build_row(row, DASH_HEADERS, DASH_LOOKUP)
        assert result[1] == "Wrong Level"

    def test_prep_in_progress_shows_prep_in_progress(self):
        row = _make_row(stage="prep_in_progress")
        result = build_row(row, DASH_HEADERS, DASH_LOOKUP)
        assert result[0] == "Prep in Progress"

    def test_prep_in_progress_apply_flag_1_still_shows_prep_in_progress(self):
        """apply_flag=1 should NOT override stage-derived status."""
        row = _make_row(stage="prep_in_progress", apply_flag=1)
        result = build_row(row, DASH_HEADERS, DASH_LOOKUP)
        assert result[0] == "Prep in Progress"

    def test_applied_stage_shows_applied(self):
        row = _make_row(stage="applied", apply_flag=1)
        result = build_row(row, DASH_HEADERS, DASH_LOOKUP)
        assert result[0] == "Applied"

    def test_interview_stage_shows_interviewing(self):
        row = _make_row(stage="interview", apply_flag=1)
        result = build_row(row, DASH_HEADERS, DASH_LOOKUP)
        assert result[0] == "Interviewing"

    def test_offer_stage_shows_offer(self):
        row = _make_row(stage="offer", apply_flag=1)
        result = build_row(row, DASH_HEADERS, DASH_LOOKUP)
        assert result[0] == "Offer"


# ---------------------------------------------------------------------------
# materials_company_cell() — hyperlinks company name to the web materials viewer
# ---------------------------------------------------------------------------


class TestMaterialsCompanyCell:
    """materials_company_cell builds =HYPERLINK when a folder exists and
    FINDAJOB_MATERIALS_BASE_URL is set; otherwise returns plain company text."""

    def test_folder_stage_with_base_url_returns_hyperlink(self):
        result = materials_company_cell(
            company="Acme Corp",
            fingerprint="abc123",
            stage="applied",
            base_url="http://docker.lan:8090",
        )
        assert result == '=HYPERLINK("http://docker.lan:8090/materials/abc123","Acme Corp")'

    def test_materials_drafted_stage_returns_hyperlink(self):
        result = materials_company_cell(
            company="Acme",
            fingerprint="fp-1",
            stage="materials_drafted",
            base_url="http://docker.lan:8090",
        )
        assert result == '=HYPERLINK("http://docker.lan:8090/materials/fp-1","Acme")'

    def test_waitlisted_stage_returns_hyperlink(self):
        result = materials_company_cell(
            company="Acme",
            fingerprint="fp-w",
            stage="waitlisted",
            base_url="http://docker.lan:8090",
        )
        assert result == '=HYPERLINK("http://docker.lan:8090/materials/fp-w","Acme")'

    def test_scored_stage_no_folder_returns_plain(self):
        result = materials_company_cell(
            company="Acme Corp",
            fingerprint="abc123",
            stage="scored",
            base_url="http://docker.lan:8090",
        )
        assert result == "Acme Corp"

    def test_manual_review_stage_no_folder_returns_plain(self):
        result = materials_company_cell(
            company="Acme",
            fingerprint="fp-m",
            stage="manual_review",
            base_url="http://docker.lan:8090",
        )
        assert result == "Acme"

    def test_empty_base_url_returns_plain(self):
        result = materials_company_cell(
            company="Acme Corp",
            fingerprint="abc123",
            stage="applied",
            base_url="",
        )
        assert result == "Acme Corp"

    def test_none_base_url_returns_plain(self):
        result = materials_company_cell(
            company="Acme Corp",
            fingerprint="abc123",
            stage="applied",
            base_url=None,
        )
        assert result == "Acme Corp"

    def test_trailing_slash_on_base_url_is_handled(self):
        result = materials_company_cell(
            company="Acme",
            fingerprint="fp-1",
            stage="applied",
            base_url="http://docker.lan:8090/",
        )
        assert result == '=HYPERLINK("http://docker.lan:8090/materials/fp-1","Acme")'

    def test_company_name_with_double_quote_is_escaped(self):
        result = materials_company_cell(
            company='O"Reilly Media',
            fingerprint="fp-q",
            stage="applied",
            base_url="http://docker.lan:8090",
        )
        assert result == '=HYPERLINK("http://docker.lan:8090/materials/fp-q","O""Reilly Media")'


# ---------------------------------------------------------------------------
# _assert_full_write — partial-write detection (#171)
# ---------------------------------------------------------------------------


class TestAssertFullWrite:
    """The Sheets `values().update()` response includes `updatedRows`, but
    sync_sheet previously trusted the local row count instead. A server-side
    partial write looked identical to success until the user refreshed and
    saw 0 rows on Applied when the log claimed 31."""

    @pytest.fixture(autouse=True)
    def _redirect_log(self, tmp_path, monkeypatch):
        """log_event() appends to a real file; redirect so tests don't need a logs/ dir."""
        from findajob import utils as _utils

        self.log_path = tmp_path / "events.jsonl"
        monkeypatch.setattr(_utils, "LOG_PATH", str(self.log_path))

    def test_passes_when_actual_matches_expected(self):
        from scripts.sync_sheet import _assert_full_write

        result = {"updatedRows": 100, "updatedRange": "Applied!A1:N100"}
        _assert_full_write(result, 100, "Applied")  # must not raise

    def test_raises_when_actual_less_than_expected(self):
        from scripts.sync_sheet import _assert_full_write

        result = {"updatedRows": 56, "updatedRange": "Dashboard!A1:N56"}
        with pytest.raises(RuntimeError, match="partial write"):
            _assert_full_write(result, 9919, "Dashboard")

    def test_raises_when_updated_rows_missing(self):
        """Defensive: a contract change that drops updatedRows must not silently pass."""
        from scripts.sync_sheet import _assert_full_write

        result = {"updatedRange": "Applied!A1:N1"}
        with pytest.raises(RuntimeError):
            _assert_full_write(result, 50, "Applied")

    def test_raises_when_zero_rows_written(self):
        """Today's Applied/Waitlist/Rejected scenario: 0 rows written despite 50 sent."""
        from scripts.sync_sheet import _assert_full_write

        result = {"updatedRows": 0}
        with pytest.raises(RuntimeError):
            _assert_full_write(result, 50, "Applied")

    def test_emits_sync_partial_write_event(self):
        """Operators need a jsonl signal — notify.py health-check alerts on unknown event patterns."""
        import json

        from scripts.sync_sheet import _assert_full_write

        result = {"updatedRows": 2, "updatedRange": "Dashboard!A1:N2"}
        with pytest.raises(RuntimeError):
            _assert_full_write(result, 9, "Dashboard")

        entries = [json.loads(line) for line in self.log_path.read_text().splitlines()]
        partial = [e for e in entries if e["event"] == "sync_partial_write"]
        assert len(partial) == 1
        assert partial[0]["tab"] == "Dashboard"
        assert partial[0]["expected_rows"] == 9
        assert partial[0]["actual_rows"] == 2
        assert partial[0]["updated_range"] == "Dashboard!A1:N2"


# ---------------------------------------------------------------------------
# Regression: sync is one-way (no values().get() reads from Sheets)
# ---------------------------------------------------------------------------


class TestNoSheetsReads:
    """After #61 PR-B, sync_sheet.py is DB → Sheet only. The four tab sync
    functions must never call svc.spreadsheets().values().get() — all write
    surfaces live in the web UI (findajob.web.routes.board_actions)."""

    @pytest.fixture(autouse=True)
    def _redirect_log(self, tmp_path, monkeypatch):
        from findajob import utils as _utils

        monkeypatch.setattr(_utils, "LOG_PATH", str(tmp_path / "events.jsonl"))

    @pytest.fixture()
    def svc(self, monkeypatch):
        """Mock svc whose values().get() raises if anyone calls it.
        _assert_full_write is stubbed since the mocked update() return shape
        can't satisfy it; this test only cares about whether .get() is called.
        """
        import scripts.sync_sheet as ss

        monkeypatch.setattr(ss, "_assert_full_write", lambda *a, **kw: None)
        svc = MagicMock()
        svc.spreadsheets.return_value.values.return_value.get.side_effect = AssertionError(
            "sync_sheet.py must not read from Sheets"
        )
        return svc

    @pytest.fixture()
    def conn(self):
        import sqlite3

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript("""
            CREATE TABLE jobs (
                id TEXT PRIMARY KEY, fingerprint TEXT, url TEXT, title TEXT,
                company TEXT, location TEXT DEFAULT '', source TEXT,
                relevance_score INTEGER, fit_score REAL, probability_score REAL,
                interview_likelihood REAL, stage TEXT, stage_updated TEXT,
                apply_flag INTEGER DEFAULT 0, prep_folder_path TEXT,
                reject_reason TEXT DEFAULT '', ai_notes TEXT,
                remote_status TEXT DEFAULT '', comp_estimate TEXT DEFAULT '',
                known_contacts TEXT DEFAULT '', user_notes TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now')),
                dupe_of TEXT DEFAULT '', raw_jd_text TEXT,
                score_status TEXT DEFAULT 'scored', score_flag_reason TEXT
            );
            CREATE TABLE audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL, field_changed TEXT NOT NULL,
                old_value TEXT, new_value TEXT,
                changed_at TEXT DEFAULT (datetime('now'))
            );
        """)
        yield conn
        conn.close()

    def test_sync_dashboard_no_reads(self, svc, conn):
        from scripts.sync_sheet import sync_dashboard

        sync_dashboard(svc, conn)
        svc.spreadsheets.return_value.values.return_value.get.assert_not_called()

    def test_sync_review_no_reads(self, svc, conn):
        from scripts.sync_sheet import sync_review

        sync_review(svc, conn)
        svc.spreadsheets.return_value.values.return_value.get.assert_not_called()

    def test_sync_waitlist_no_reads(self, svc, conn):
        from scripts.sync_sheet import sync_waitlist

        sync_waitlist(svc, conn)
        svc.spreadsheets.return_value.values.return_value.get.assert_not_called()

    def test_sync_applied_no_reads(self, svc, conn):
        from scripts.sync_sheet import sync_applied

        sync_applied(svc, conn)
        svc.spreadsheets.return_value.values.return_value.get.assert_not_called()
