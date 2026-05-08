"""/docs/ user-facing docs viewer (#224)."""

from __future__ import annotations

import sqlite3
import textwrap
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from findajob.web.app import create_app

USAGE_MD = textwrap.dedent(
    """\
    # Usage

    This page walks through the daily workflow. If you're setting up for the first
    time, read [`getting-started/README.md`](getting-started/README.md) first.

    ## Dashboard

    See the [GitHub repo](https://github.com/brockamer/findajob) for source.

    Jump to the [next section](#applied) for post-application flow.

    ## Applied
    """
)


TROUBLESHOOTING_MD = textwrap.dedent(
    """\
    # Troubleshooting

    See [`getting-started/README.md`](getting-started/README.md) and [`usage.md`](usage.md).
    """
)


GETTING_STARTED_README_MD = textwrap.dedent(
    """\
    # Getting started

    ## 1. Prerequisites → [`prerequisites.md`](prerequisites.md)

    ## 2. Install → [`install-docker.md`](install-docker.md)

    Also see [`../troubleshooting.md`](../troubleshooting.md).
    """
)


GETTING_STARTED_PREREQ_MD = "# Prerequisites\n\nNeeded before install.\n"
GETTING_STARTED_INSTALL_DOCKER_MD = "# Install with Docker\n\nThe primary install path.\n"
GETTING_STARTED_CONFIGURE_MD = "# Configure\n\nFile-by-file config walkthrough.\n"
OPERATIONS_README_MD = "# Operations\n\nManual commands, log rotation, restore.\n"
OPERATIONS_INTERNET_EXPOSURE_MD = "# Exposing findajob to the public internet\n\nBasic auth pattern.\n"


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    db = tmp_path / "pipeline.db"
    sqlite3.connect(db).close()
    companies = tmp_path / "companies"
    companies.mkdir()

    # Seed docs/ under tmp_path so app.state.base_root=tmp_path finds them.
    docs = tmp_path / "docs"
    (docs / "getting-started").mkdir(parents=True)
    (docs / "operations").mkdir(parents=True)
    (docs / "usage.md").write_text(USAGE_MD)
    (docs / "troubleshooting.md").write_text(TROUBLESHOOTING_MD)
    (docs / "getting-started" / "README.md").write_text(GETTING_STARTED_README_MD)
    (docs / "getting-started" / "prerequisites.md").write_text(GETTING_STARTED_PREREQ_MD)
    (docs / "getting-started" / "install-docker.md").write_text(GETTING_STARTED_INSTALL_DOCKER_MD)
    (docs / "getting-started" / "configure.md").write_text(GETTING_STARTED_CONFIGURE_MD)
    (docs / "operations" / "README.md").write_text(OPERATIONS_README_MD)
    (docs / "operations" / "internet-exposure.md").write_text(OPERATIONS_INTERNET_EXPOSURE_MD)

    return TestClient(create_app(companies_root=companies, db_path=db, base_root=tmp_path))


def test_index_lists_four_guides(client: TestClient) -> None:
    r = client.get("/docs/")
    assert r.status_code == 200
    assert 'href="/docs/getting-started"' in r.text
    assert 'href="/docs/usage"' in r.text
    assert 'href="/docs/operations"' in r.text
    assert 'href="/docs/troubleshooting"' in r.text
    # One-line descriptions surface on the index.
    assert "Daily workflow" in r.text


def test_index_does_not_require_onboarding(tmp_path: Path) -> None:
    # No mark_complete() call — /docs/ must stay reachable mid-onboarding.
    db = tmp_path / "pipeline.db"
    sqlite3.connect(db).close()
    companies = tmp_path / "companies"
    companies.mkdir()
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "usage.md").write_text("# Usage\n")
    c = TestClient(create_app(companies_root=companies, db_path=db, base_root=tmp_path))
    r = c.get("/docs/", follow_redirects=False)
    assert r.status_code == 200


def test_usage_renders(client: TestClient) -> None:
    r = client.get("/docs/usage")
    assert r.status_code == 200
    assert ">Usage</h1>" in r.text


def test_troubleshooting_renders(client: TestClient) -> None:
    r = client.get("/docs/troubleshooting")
    assert r.status_code == 200
    assert ">Troubleshooting</h1>" in r.text


def test_getting_started_readme_renders(client: TestClient) -> None:
    r = client.get("/docs/getting-started")
    assert r.status_code == 200
    assert ">Getting started</h1>" in r.text


def test_getting_started_subpage_renders(client: TestClient) -> None:
    r = client.get("/docs/getting-started/install-docker")
    assert r.status_code == 200
    assert "Install with Docker" in r.text


def test_operations_readme_renders(client: TestClient) -> None:
    r = client.get("/docs/operations")
    assert r.status_code == 200
    assert ">Operations</h1>" in r.text


def test_internet_exposure_subpage_renders(client: TestClient) -> None:
    """#327: pattern doc reachable in-app under operations/ via the docs viewer's slug allowlist."""
    r = client.get("/docs/operations/internet-exposure")
    assert r.status_code == 200
    assert "Exposing findajob to the public internet" in r.text


def test_unknown_slug_404s(client: TestClient) -> None:
    r = client.get("/docs/does-not-exist")
    assert r.status_code == 404


def test_md_sibling_links_rewrite(client: TestClient) -> None:
    # usage.md → getting-started/README.md ends up as /docs/getting-started (README stripped).
    r = client.get("/docs/usage")
    assert 'href="/docs/getting-started"' in r.text
    assert 'href="getting-started/README.md"' not in r.text


def test_md_parent_links_rewrite(client: TestClient) -> None:
    # getting-started/README.md → ../troubleshooting.md ends up as /docs/troubleshooting.
    r = client.get("/docs/getting-started")
    assert 'href="/docs/troubleshooting"' in r.text
    assert 'href="../troubleshooting.md"' not in r.text


def test_md_relative_subpage_links_rewrite(client: TestClient) -> None:
    # getting-started/README.md → prerequisites.md ends up as /docs/getting-started/prerequisites.
    r = client.get("/docs/getting-started")
    assert 'href="/docs/getting-started/prerequisites"' in r.text
    assert 'href="/docs/getting-started/install-docker"' in r.text


def test_troubleshooting_cross_links_rewrite(client: TestClient) -> None:
    r = client.get("/docs/troubleshooting")
    assert 'href="/docs/getting-started"' in r.text
    assert 'href="/docs/usage"' in r.text


def test_external_links_get_target_blank(client: TestClient) -> None:
    r = client.get("/docs/usage")
    assert 'href="https://github.com/brockamer/findajob"' in r.text
    assert 'target="_blank"' in r.text
    assert 'rel="noopener noreferrer"' in r.text


def test_anchor_fragment_links_pass_through(client: TestClient) -> None:
    r = client.get("/docs/usage")
    # In-page anchors stay as-is; the `toc` extension auto-generates heading IDs.
    assert 'href="#applied"' in r.text
    assert 'id="applied"' in r.text
