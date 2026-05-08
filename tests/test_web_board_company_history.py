"""#234 — Dashboard + Waitlist rows surface prior-application history by company.

Counts split into `pending` (stage IN applied/interview/offer) and `not_selected`
with a 90-day recency window for the yellow-flag signal. Company matching uses
first-normalized-word (normalize(company).split()[0]) so "Meta" and "Meta
Platforms" collapse. Operator-side `rejected` stage is excluded (AC7). The row's
own fingerprint is excluded from its own history.

Schema note: audit_log.job_id stores jobs.id (UUID), not jobs.fingerprint, so
fixtures include jobs.id. See test_web_board_applied.py for the broader
convention.
"""

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from findajob.onboarding import mark_complete
from findajob.web.app import create_app
from findajob.web.company_history import _company_key


def _iso_days_ago(days: int) -> str:
    return (datetime.now(UTC) - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")


def _make_client(tmp_path: Path) -> tuple[sqlite3.Connection, Path]:
    """Build a tmp pipeline.db via the production migration runner.

    Pre-M5/M6 this fixture maintained a hand-written subset of the
    schema. Using ``apply_pending`` matches production exactly.
    """
    from findajob.db.migrate import apply_pending

    db = tmp_path / "pipeline.db"
    conn = sqlite3.connect(db)
    apply_pending(conn)
    return conn, db


def _finalize(tmp_path: Path, conn: sqlite3.Connection, db: Path) -> TestClient:
    conn.commit()
    conn.close()
    companies = tmp_path / "companies"
    companies.mkdir()
    mark_complete(tmp_path)
    return TestClient(create_app(companies_root=companies, db_path=db, base_root=tmp_path))


# ── Helper unit tests ────────────────────────────────────────────────────────


def test_company_key_first_word_match() -> None:
    """Loose match by first normalized word — 'Meta' and 'Meta Platforms' collapse."""
    assert _company_key("Meta") == "meta"
    assert _company_key("Meta Platforms") == "meta"
    assert _company_key("META, Inc.") == "meta"
    assert _company_key("Google") == "google"
    assert _company_key("Google Cloud") == "google"


def test_company_key_blank_guard() -> None:
    assert _company_key("") == ""
    assert _company_key(None) == ""  # type: ignore[arg-type]


# ── Integration tests ───────────────────────────────────────────────────────


@pytest.fixture
def empty_history_client(tmp_path: Path) -> TestClient:
    """Company with a single dashboard-eligible row and no prior applications."""
    conn, db = _make_client(tmp_path)
    conn.execute(
        "INSERT INTO jobs (id, fingerprint, title, company, url, source, stage, relevance_score, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        ("id-only", "fp-only", "Lead NPI", "OpenAI", "https://example.com/x", "test", "scored", 8, _iso_days_ago(1)),
    )
    return _finalize(tmp_path, conn, db)


def test_dashboard_empty_history_renders_dash(empty_history_client: TestClient) -> None:
    """AC2: empty history renders a subtle dash, not a zero, so high-signal rows stand out."""
    r = empty_history_client.get("/board/dashboard")
    assert r.status_code == 200
    assert "Lead NPI" in r.text
    idx = r.text.find('data-fingerprint="fp-only"')
    assert idx > 0
    row_end = r.text.find("</tr>", idx)
    row = r.text[idx:row_end]
    # history cell exists, content is an em-dash or empty
    assert 'data-col="company_history"' in row
    assert "0 pending" not in row and "0 not selected" not in row


@pytest.fixture
def pending_only_client(tmp_path: Path) -> TestClient:
    """Meta has one scored-dashboard row + one applied sibling (pending)."""
    conn, db = _make_client(tmp_path)
    conn.execute(
        "INSERT INTO jobs (id, fingerprint, title, company, url, source, stage, relevance_score, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (
            "id-dash",
            "fp-dash",
            "Data Center PM",
            "Meta",
            "https://example.com/x",
            "test",
            "scored",
            8,
            _iso_days_ago(0),
        ),
    )
    conn.execute(
        "INSERT INTO jobs (id, fingerprint, title, company, url, source, stage, stage_updated) "
        "VALUES (?,?,?,?,?,?,?,?)",
        ("id-app", "fp-app", "NPI Lead", "Meta", "https://example.com/x", "test", "applied", _iso_days_ago(5)),
    )
    return _finalize(tmp_path, conn, db)


def test_dashboard_pending_count_shows(pending_only_client: TestClient) -> None:
    """AC1: 'N pending' for applied/interview/offer siblings at the same company."""
    r = pending_only_client.get("/board/dashboard")
    assert r.status_code == 200
    idx = r.text.find('data-fingerprint="fp-dash"')
    assert idx > 0
    row = r.text[idx : r.text.find("</tr>", idx)]
    assert "1 pending" in row


@pytest.fixture
def not_selected_only_client(tmp_path: Path) -> TestClient:
    """Google has one scored-dashboard row + one not_selected 30d ago (within 90d)."""
    conn, db = _make_client(tmp_path)
    conn.execute(
        "INSERT INTO jobs (id, fingerprint, title, company, url, source, stage, relevance_score, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        ("id-dash", "fp-dash", "Infra PM", "Google", "https://example.com/x", "test", "scored", 8, _iso_days_ago(0)),
    )
    conn.execute(
        "INSERT INTO jobs (id, fingerprint, title, company, url, source, stage, reject_reason) "
        "VALUES (?,?,?,?,?,?,?,?)",
        ("id-ns", "fp-ns", "NPI Lead", "Google", "https://example.com/x", "test", "not_selected", "No Response"),
    )
    conn.execute(
        "INSERT INTO audit_log (job_id, field_changed, old_value, new_value, changed_at, changed_by) "
        "VALUES (?,?,?,?,?,?)",
        ("id-ns", "stage", "applied", "not_selected", _iso_days_ago(30), "user"),
    )
    return _finalize(tmp_path, conn, db)


def test_dashboard_not_selected_count_shows(not_selected_only_client: TestClient) -> None:
    """AC1: 'N not selected' for company-rejection siblings."""
    r = not_selected_only_client.get("/board/dashboard")
    assert r.status_code == 200
    idx = r.text.find('data-fingerprint="fp-dash"')
    row = r.text[idx : r.text.find("</tr>", idx)]
    assert "1 not selected" in row


def test_dashboard_recent_not_selected_flagged_yellow(not_selected_only_client: TestClient) -> None:
    """AC3: not_selected within 90d renders a yellow flag class."""
    r = not_selected_only_client.get("/board/dashboard")
    idx = r.text.find('data-fingerprint="fp-dash"')
    row = r.text[idx : r.text.find("</tr>", idx)]
    # Yellow signaling uses an amber/yellow tailwind class on the history cell
    assert "history-yellow" in row


@pytest.fixture
def old_not_selected_client(tmp_path: Path) -> TestClient:
    """Not_selected OUTSIDE the 90-day window — counts but not yellow."""
    conn, db = _make_client(tmp_path)
    conn.execute(
        "INSERT INTO jobs (id, fingerprint, title, company, url, source, stage, relevance_score, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        ("id-dash", "fp-dash", "Infra PM", "Amazon", "https://example.com/x", "test", "scored", 8, _iso_days_ago(0)),
    )
    conn.execute(
        "INSERT INTO jobs (id, fingerprint, title, company, url, source, stage) VALUES (?,?,?,?,?,?,?)",
        ("id-ns", "fp-ns", "Old Role", "Amazon", "https://example.com/x", "test", "not_selected"),
    )
    conn.execute(
        "INSERT INTO audit_log (job_id, field_changed, old_value, new_value, changed_at, changed_by) "
        "VALUES (?,?,?,?,?,?)",
        ("id-ns", "stage", "applied", "not_selected", _iso_days_ago(120), "user"),
    )
    return _finalize(tmp_path, conn, db)


def test_dashboard_old_not_selected_counts_but_no_yellow(old_not_selected_client: TestClient) -> None:
    """AC3: not_selected >90d ago counts in the tally but does NOT trigger yellow."""
    r = old_not_selected_client.get("/board/dashboard")
    idx = r.text.find('data-fingerprint="fp-dash"')
    row = r.text[idx : r.text.find("</tr>", idx)]
    assert "1 not selected" in row
    assert "history-yellow" not in row


@pytest.fixture
def offer_green_client(tmp_path: Path) -> TestClient:
    """Anthropic has an offer-stage sibling — dashboard row should render green."""
    conn, db = _make_client(tmp_path)
    conn.execute(
        "INSERT INTO jobs (id, fingerprint, title, company, url, source, stage, relevance_score, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (
            "id-dash",
            "fp-dash",
            "Research Eng",
            "Anthropic",
            "https://example.com/x",
            "test",
            "scored",
            9,
            _iso_days_ago(0),
        ),
    )
    conn.execute(
        "INSERT INTO jobs (id, fingerprint, title, company, url, source, stage) VALUES (?,?,?,?,?,?,?)",
        ("id-offer", "fp-offer", "Infra Lead", "Anthropic", "https://example.com/x", "test", "offer"),
    )
    return _finalize(tmp_path, conn, db)


def test_dashboard_offer_flagged_green(offer_green_client: TestClient) -> None:
    """AC3: offer anywhere at the company renders a strong green signal."""
    r = offer_green_client.get("/board/dashboard")
    idx = r.text.find('data-fingerprint="fp-dash"')
    row = r.text[idx : r.text.find("</tr>", idx)]
    assert "history-green" in row


@pytest.fixture
def mixed_client(tmp_path: Path) -> TestClient:
    """xAI — 2 pending + 1 recent not_selected. Tests that both counts surface."""
    conn, db = _make_client(tmp_path)
    conn.execute(
        "INSERT INTO jobs (id, fingerprint, title, company, url, source, stage, relevance_score, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        ("id-dash", "fp-dash", "Systems Eng", "xAI", "https://example.com/x", "test", "scored", 8, _iso_days_ago(0)),
    )
    conn.execute(
        "INSERT INTO jobs (id, fingerprint, title, company, url, source, stage) VALUES (?,?,?,?,?,?,?)",
        ("id-app1", "fp-app1", "Infra Lead", "xAI", "https://example.com/x", "test", "applied"),
    )
    conn.execute(
        "INSERT INTO jobs (id, fingerprint, title, company, url, source, stage) VALUES (?,?,?,?,?,?,?)",
        ("id-app2", "fp-app2", "Hardware Eng", "xAI", "https://example.com/x", "test", "interview"),
    )
    conn.execute(
        "INSERT INTO jobs (id, fingerprint, title, company, url, source, stage) VALUES (?,?,?,?,?,?,?)",
        ("id-ns", "fp-ns", "PM", "xAI", "https://example.com/x", "test", "not_selected"),
    )
    conn.execute(
        "INSERT INTO audit_log (job_id, field_changed, old_value, new_value, changed_at, changed_by) "
        "VALUES (?,?,?,?,?,?)",
        ("id-ns", "stage", "applied", "not_selected", _iso_days_ago(45), "user"),
    )
    return _finalize(tmp_path, conn, db)


def test_dashboard_mixed_history_shows_both_counts(mixed_client: TestClient) -> None:
    r = mixed_client.get("/board/dashboard")
    idx = r.text.find('data-fingerprint="fp-dash"')
    row = r.text[idx : r.text.find("</tr>", idx)]
    assert "2 pending" in row
    assert "1 not selected" in row


@pytest.fixture
def loose_match_client(tmp_path: Path) -> TestClient:
    """Dashboard row company='Meta' matches applied row company='Meta Platforms'."""
    conn, db = _make_client(tmp_path)
    conn.execute(
        "INSERT INTO jobs (id, fingerprint, title, company, url, source, stage, relevance_score, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (
            "id-dash",
            "fp-dash",
            "Data Center Lead",
            "Meta",
            "https://example.com/x",
            "test",
            "scored",
            8,
            _iso_days_ago(0),
        ),
    )
    conn.execute(
        "INSERT INTO jobs (id, fingerprint, title, company, url, source, stage) VALUES (?,?,?,?,?,?,?)",
        ("id-app", "fp-app", "NPI Lead", "Meta Platforms", "https://example.com/x", "test", "applied"),
    )
    return _finalize(tmp_path, conn, db)


def test_dashboard_loose_company_match(loose_match_client: TestClient) -> None:
    """AC5: 'Meta' and 'Meta Platforms' collapse via first-normalized-word match."""
    r = loose_match_client.get("/board/dashboard")
    idx = r.text.find('data-fingerprint="fp-dash"')
    row = r.text[idx : r.text.find("</tr>", idx)]
    assert "1 pending" in row


@pytest.fixture
def rejected_excluded_client(tmp_path: Path) -> TestClient:
    """Operator-rejected jobs must NOT count in history (AC7)."""
    conn, db = _make_client(tmp_path)
    conn.execute(
        "INSERT INTO jobs (id, fingerprint, title, company, url, source, stage, relevance_score, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        ("id-dash", "fp-dash", "Infra PM", "Microsoft", "https://example.com/x", "test", "scored", 8, _iso_days_ago(0)),
    )
    conn.execute(
        "INSERT INTO jobs (id, fingerprint, title, company, url, source, stage, reject_reason) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (
            "id-rej",
            "fp-rej",
            "Wrong Stack",
            "Microsoft",
            "https://example.com/x",
            "test",
            "rejected",
            "Tech Stack Mismatch",
        ),
    )
    return _finalize(tmp_path, conn, db)


def test_dashboard_operator_rejected_not_counted(rejected_excluded_client: TestClient) -> None:
    """AC7: operator-rejected rows are noise for this decision, not signal."""
    r = rejected_excluded_client.get("/board/dashboard")
    idx = r.text.find('data-fingerprint="fp-dash"')
    row = r.text[idx : r.text.find("</tr>", idx)]
    # Scope to the history cell — the word "pending" appears elsewhere in row
    # markup (HTMX pendingAction wiring on the reject cell).
    hist_start = row.find('data-col="company_history"')
    assert hist_start > 0
    hist_end = row.find("</td>", hist_start)
    hist_cell = row[hist_start:hist_end]
    assert "pending" not in hist_cell
    assert "not selected" not in hist_cell


@pytest.fixture
def self_exclusion_client(tmp_path: Path) -> TestClient:
    """A row must not count itself in its own history."""
    conn, db = _make_client(tmp_path)
    # One waitlisted row at Meta with no siblings — should see no history,
    # not "1 pending" counting itself.
    conn.execute(
        "INSERT INTO jobs (id, fingerprint, title, company, url, source, stage, created_at) VALUES (?,?,?,?,?,?,?,?)",
        ("id-sole", "fp-sole", "Sole Role", "Meta", "https://example.com/x", "test", "waitlisted", _iso_days_ago(0)),
    )
    return _finalize(tmp_path, conn, db)


def test_waitlist_self_not_in_own_history(self_exclusion_client: TestClient) -> None:
    r = self_exclusion_client.get("/board/waitlist")
    idx = r.text.find('data-fingerprint="fp-sole"')
    row = r.text[idx : r.text.find("</tr>", idx)]
    hist_start = row.find('data-col="company_history"')
    assert hist_start > 0
    hist_end = row.find("</td>", hist_start)
    hist_cell = row[hist_start:hist_end]
    assert "pending" not in hist_cell
    assert "not selected" not in hist_cell


@pytest.fixture
def waitlist_history_client(tmp_path: Path) -> TestClient:
    """A waitlisted Meta row with an applied Meta Platforms sibling (loose match)."""
    conn, db = _make_client(tmp_path)
    conn.execute(
        "INSERT INTO jobs (id, fingerprint, title, company, url, source, stage, relevance_score, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        ("id-wait", "fp-wait", "Ops Lead", "Meta", "https://example.com/x", "test", "waitlisted", 8, _iso_days_ago(0)),
    )
    conn.execute(
        "INSERT INTO jobs (id, fingerprint, title, company, url, source, stage) VALUES (?,?,?,?,?,?,?)",
        ("id-app", "fp-app", "NPI Lead", "Meta Platforms", "https://example.com/x", "test", "applied"),
    )
    return _finalize(tmp_path, conn, db)


def test_waitlist_history_cell_present_and_loose_match(waitlist_history_client: TestClient) -> None:
    r = waitlist_history_client.get("/board/waitlist")
    assert r.status_code == 200
    idx = r.text.find('data-fingerprint="fp-wait"')
    assert idx > 0
    row = r.text[idx : r.text.find("</tr>", idx)]
    assert "1 pending" in row


# ── HTMX row-swap coverage ──────────────────────────────────────────────────
# Every POST handler in board_actions.py that re-renders a dashboard row must
# pass history_by_fp to the template, or the swapped-in row loses its history
# cell until the user reloads the page.


@pytest.fixture
def swap_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Prep-dispatches a scored Meta row that has one applied sibling at the
    same company — the swapped row should render '1 pending' in its history cell."""
    conn, db = _make_client(tmp_path)
    conn.execute(
        "INSERT INTO jobs (id, fingerprint, title, company, stage, relevance_score, url, source, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (
            "id-dash",
            "fp-dash",
            "Data Center Lead",
            "Meta",
            "scored",
            8,
            "https://example.com/j",
            "test",
            _iso_days_ago(0),
        ),
    )
    conn.execute(
        "INSERT INTO jobs (id, fingerprint, title, company, url, source, stage) VALUES (?,?,?,?,?,?,?)",
        ("id-app", "fp-app", "NPI Lead", "Meta", "https://example.com/x", "test", "applied"),
    )
    client = _finalize(tmp_path, conn, db)

    # Don't actually fork prep_application.py when the POST handler dispatches.
    from findajob.web.routes import board_actions

    class _FakePopen:
        # The launcher reads ``proc.pid`` to backfill background_tasks.pid
        # — set a fake int so the SQLite UPDATE doesn't crash.
        pid = 99999

        def __init__(self, *_a, **_kw) -> None:  # noqa: ANN003
            pass

    monkeypatch.setattr(board_actions.subprocess, "Popen", _FakePopen)
    return client


def test_prep_swap_includes_company_history(swap_client: TestClient) -> None:
    """POST /board/jobs/{fp}/prep returns the new <tr> as HTMX outerHTML — the
    swapped row must still render the company-history cell (#234). Without
    history_by_fp in the render context, the cell falls back to the empty
    em-dash state and the operator loses context until the next reload."""
    r = swap_client.post("/board/jobs/fp-dash/prep")
    assert r.status_code == 200
    assert 'data-fingerprint="fp-dash"' in r.text
    assert "1 pending" in r.text
