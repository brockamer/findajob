"""_nav.html partial highlights the current route."""

import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from findajob.onboarding import mark_complete
from findajob.web.app import create_app


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    db = tmp_path / "pipeline.db"
    # Bootstrap the canonical schema via init_db.py rather than hand-rolling —
    # avoids drift when tables get added.
    subprocess.run(
        [sys.executable, "scripts/init_db.py", str(db)],
        check=True,
        cwd=Path(__file__).resolve().parent.parent,
    )
    companies = tmp_path / "companies"
    companies.mkdir()
    mark_complete(tmp_path)
    app = create_app(companies_root=companies, db_path=db, base_root=tmp_path)
    return TestClient(app)


def test_nav_present_on_landing(client: TestClient) -> None:
    r = client.get("/materials/")
    assert r.status_code == 200
    assert 'href="/"' in r.text
    assert 'href="/materials/"' in r.text
    assert 'href="/board/dashboard"' in r.text
    assert 'href="/ingest/"' in r.text
    assert 'href="/stats/funnel"' in r.text
    assert 'href="/tools/"' in r.text
    assert 'href="/config/"' in r.text
    assert 'href="/docs/"' in r.text


def test_materials_index_moved(client: TestClient) -> None:
    r = client.get("/materials/")
    assert r.status_code == 200
    assert "In flight" in r.text or "Applied" in r.text or "Rejected" in r.text


def test_every_nav_link_resolves(client: TestClient) -> None:
    """Regression: every href in the top nav returns 200, not 404.

    /stats/funnel uses follow_redirects=True to absorb the /stats/ → /stats/funnel
    redirect (the link points at /stats/funnel directly, so this is just defensive).
    """
    for path in ["/", "/materials/", "/board/dashboard", "/ingest/", "/stats/funnel", "/tools/", "/config/", "/docs/"]:
        r = client.get(path, follow_redirects=True)
        assert r.status_code == 200, f"Nav link {path} returned {r.status_code}"


def test_board_link_highlights_on_every_board_page(client: TestClient) -> None:
    """Regression for #138: Board link in top nav highlights on /board/applied,
    /board/waitlist, etc., not just on /board/dashboard."""
    for path in ["/board/dashboard", "/board/applied", "/board/waitlist", "/board/review", "/board/archive"]:
        r = client.get(path)
        assert r.status_code == 200, f"{path} returned {r.status_code}"
        idx = r.text.index('href="/board/dashboard"')
        snippet = r.text[idx : idx + 300]
        assert 'aria-current="page"' in snippet, f"Board link not active on {path}"


def test_nav_renders_spend_chip_on_fresh_stack(client: TestClient) -> None:
    """The nav spend chip always renders — on a fresh stack with no cost_log
    rows it shows $0.00 this month rather than disappearing. The chip <li>
    carries ml-auto so the right-cluster (bell + Admin) is pushed to the
    nav's right edge."""
    r = client.get("/")
    assert r.status_code == 200
    assert "nav-credits" in r.text
    assert "$0.00 this month" in r.text
    assert "ml-auto" in r.text
