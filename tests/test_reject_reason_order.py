"""#1220 — reject-reason dropdown order: most-used first, bookkeeping last.

Decision recorded on #1220: one flat list. Fit reasons sort by how often
the operator used them (``feedback_log`` counts, ties keep the YAML order);
the bookkeeping reasons that say nothing about fit (Already Applied,
Stale/Closed, Company passed, Other) always go last, in YAML order.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from findajob.onboarding import mark_complete
from findajob.web.app import create_app
from findajob.web.reject_reason_order import order_reject_reasons
from tests.conftest import init_test_db


def test_no_history_keeps_yaml_order_with_bookkeeping_last() -> None:
    reasons = ("Other", "Skills Mismatch", "Already Applied", "Geography")
    assert order_reject_reasons(reasons, {}) == (
        "Skills Mismatch",
        "Geography",
        "Other",
        "Already Applied",
    )


def test_most_used_fit_reasons_first() -> None:
    reasons = ("Skills Mismatch", "Geography", "Compensation", "Stale/Closed")
    counts = {"Compensation": 5, "Geography": 2, "Stale/Closed": 50}
    assert order_reject_reasons(reasons, counts) == (
        "Compensation",
        "Geography",
        "Skills Mismatch",
        "Stale/Closed",
    )


def test_bookkeeping_match_ignores_case() -> None:
    reasons = ("company PASSED", "Too Senior")
    assert order_reject_reasons(reasons, {}) == ("Too Senior", "company PASSED")


def test_counts_for_unknown_reasons_are_ignored() -> None:
    reasons = ("A", "B")
    assert order_reject_reasons(reasons, {"Retired Reason": 9, "B": 1}) == ("B", "A")


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    yaml_path = tmp_path / "reject_reasons.yaml"
    yaml_path.write_text("reasons:\n  - Other\n  - Skills Mismatch\n  - Geography\n  - Compensation\n")
    from findajob import config_loader

    monkeypatch.setattr(config_loader, "_REJECT_REASONS_PATH", yaml_path)
    db = tmp_path / "pipeline.db"
    init_test_db(db)
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO jobs (id, fingerprint, url, title, company, source, stage, relevance_score) "
        "VALUES ('id-dash','fp-dash','https://example.com/j1','Staff Eng','Acme','test','scored',9)"
    )
    for reason, n in (("Compensation", 3), ("Geography", 1)):
        for i in range(n):
            conn.execute(
                "INSERT INTO feedback_log (job_id, title, company, reject_reason) VALUES (?, 't', 'c', ?)",
                (f"old-{reason}-{i}", reason),
            )
    conn.commit()
    conn.close()
    companies = tmp_path / "companies"
    companies.mkdir()
    mark_complete(tmp_path)
    return TestClient(create_app(companies_root=companies, db_path=db, base_root=tmp_path))


def test_dashboard_dropdown_uses_the_ordered_reasons(client: TestClient) -> None:
    html = client.get("/board/dashboard").text
    select = html.split('aria-label="Reject with reason"', 1)[1].split("</select>", 1)[0]
    options = [o for o in re.findall(r'<option value="([^"]*)"', select) if o]
    assert options == ["Compensation", "Geography", "Skills Mismatch", "Other"]
