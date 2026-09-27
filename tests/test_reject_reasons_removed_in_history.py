"""AC #3 from #490: a reason that exists in `feedback_log` historical
rows but has been removed from `reject_reasons.yaml` MUST NOT break
/board/dashboard/ or /board/rejected/.
(/stats/ was in this list until the #1058 teardown removed it.)
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from findajob import config_loader
from findajob.onboarding import mark_complete
from findajob.web.app import create_app
from tests.conftest import init_test_db


@pytest.fixture
def client_with_orphan_feedback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    # 1. reject_reasons.yaml WITHOUT "Orphaned Reason"
    yaml_path = tmp_path / "reject_reasons.yaml"
    yaml_path.write_text("reasons:\n  - Skills Mismatch\n  - Wrong Domain\n")
    monkeypatch.setattr(config_loader, "_REJECT_REASONS_PATH", yaml_path)

    # 2. SQLite via production migration runner.
    db = tmp_path / "pipeline.db"
    init_test_db(db)
    conn = sqlite3.connect(db)

    # 3. Insert one rejected job + a feedback_log row whose reason is
    #    NOT in the current YAML — this is the orphan condition.
    conn.execute(
        "INSERT INTO jobs (id, fingerprint, url, title, company, source, stage, reject_reason, "
        "created_at) "
        "VALUES ('fp-orphan', 'fp-orphan', 'https://example.com/j1', 'Some Job', 'Acme', "
        "'test', 'rejected', 'Orphaned Reason', '2026-04-01 00:00:00')"
    )
    conn.execute(
        "INSERT INTO feedback_log (job_id, title, company, relevance_score, reject_reason, "
        "created_at) "
        "VALUES ('fp-orphan', 'Some Job', 'Acme', 5, 'Orphaned Reason', "
        "'2026-04-01 00:00:00')"
    )
    conn.execute(
        "INSERT INTO audit_log (job_id, field_changed, old_value, new_value, "
        "changed_at, changed_by) "
        "VALUES ('fp-orphan', 'stage', 'scored', 'rejected', '2026-04-01 00:00:00', 'user')"
    )
    conn.commit()
    conn.close()

    companies = tmp_path / "companies"
    companies.mkdir()
    mark_complete(tmp_path)

    return TestClient(
        create_app(companies_root=companies, db_path=db, base_root=tmp_path),
        follow_redirects=True,
    )


@pytest.mark.parametrize(
    "path",
    [
        "/board/dashboard/",
        "/board/rejected/",
    ],
)
def test_orphan_reason_does_not_break_view(client_with_orphan_feedback: TestClient, path: str) -> None:
    resp = client_with_orphan_feedback.get(path)
    assert resp.status_code == 200, f"{path} returned {resp.status_code}: {resp.text[:500]}"
