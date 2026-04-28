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
    time, read [`setup/README.md`](setup/README.md) first.

    ## Dashboard

    See the [GitHub repo](https://github.com/brockamer/findajob) for source.

    Jump to the [next section](#applied) for post-application flow.

    ## Applied
    """
)


TROUBLESHOOTING_MD = textwrap.dedent(
    """\
    # Troubleshooting

    See [`setup/README.md`](setup/README.md) and [`usage.md`](usage.md).
    """
)


SETUP_README_MD = textwrap.dedent(
    """\
    # Setup

    ## 1. Prerequisites → [`prerequisites.md`](prerequisites.md)

    ## 2. Install → [`install-docker.md`](install-docker.md)

    Also see [`../troubleshooting.md`](../troubleshooting.md) and the
    [legacy native install](install-linux.md).
    """
)


SETUP_PREREQ_MD = "# Prerequisites\n\nNeeded before install.\n"
SETUP_INSTALL_DOCKER_MD = "# Install with Docker\n\nThe primary install path.\n"
SETUP_INSTALL_LINUX_MD = "# Install on Linux (legacy)\n\nFallback.\n"
SETUP_CONFIGURE_MD = "# Configure\n\nFile-by-file config walkthrough.\n"
SETUP_STATE_MIGRATION_MD = "# State migration\n\nMoving from rclone to viewer.\n"
SETUP_INTERNET_EXPOSURE_MD = "# Exposing findajob to the public internet\n\nBasic auth pattern.\n"


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    db = tmp_path / "pipeline.db"
    sqlite3.connect(db).close()
    companies = tmp_path / "companies"
    companies.mkdir()

    # Seed docs/ under tmp_path so app.state.base_root=tmp_path finds them.
    docs = tmp_path / "docs"
    (docs / "setup").mkdir(parents=True)
    (docs / "usage.md").write_text(USAGE_MD)
    (docs / "troubleshooting.md").write_text(TROUBLESHOOTING_MD)
    (docs / "setup" / "README.md").write_text(SETUP_README_MD)
    (docs / "setup" / "prerequisites.md").write_text(SETUP_PREREQ_MD)
    (docs / "setup" / "install-docker.md").write_text(SETUP_INSTALL_DOCKER_MD)
    (docs / "setup" / "install-linux.md").write_text(SETUP_INSTALL_LINUX_MD)
    (docs / "setup" / "configure.md").write_text(SETUP_CONFIGURE_MD)
    (docs / "setup" / "state-migration.md").write_text(SETUP_STATE_MIGRATION_MD)
    (docs / "setup" / "internet-exposure.md").write_text(SETUP_INTERNET_EXPOSURE_MD)

    return TestClient(create_app(companies_root=companies, db_path=db, base_root=tmp_path))


def test_index_lists_three_guides(client: TestClient) -> None:
    r = client.get("/docs/")
    assert r.status_code == 200
    assert 'href="/docs/setup"' in r.text
    assert 'href="/docs/usage"' in r.text
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


def test_setup_readme_renders(client: TestClient) -> None:
    r = client.get("/docs/setup")
    assert r.status_code == 200
    assert ">Setup</h1>" in r.text


def test_setup_subpage_renders(client: TestClient) -> None:
    r = client.get("/docs/setup/install-docker")
    assert r.status_code == 200
    assert "Install with Docker" in r.text


def test_internet_exposure_subpage_renders(client: TestClient) -> None:
    """#327: new pattern doc reachable in-app via the docs viewer's slug allowlist."""
    r = client.get("/docs/setup/internet-exposure")
    assert r.status_code == 200
    assert "Exposing findajob to the public internet" in r.text


def test_unknown_slug_404s(client: TestClient) -> None:
    r = client.get("/docs/does-not-exist")
    assert r.status_code == 404


def test_md_sibling_links_rewrite(client: TestClient) -> None:
    # usage.md → setup/README.md ends up as /docs/setup (README stripped).
    r = client.get("/docs/usage")
    assert 'href="/docs/setup"' in r.text
    assert 'href="setup/README.md"' not in r.text


def test_md_parent_links_rewrite(client: TestClient) -> None:
    # setup/README.md → ../troubleshooting.md ends up as /docs/troubleshooting.
    r = client.get("/docs/setup")
    assert 'href="/docs/troubleshooting"' in r.text
    assert 'href="../troubleshooting.md"' not in r.text


def test_md_relative_subpage_links_rewrite(client: TestClient) -> None:
    # setup/README.md → prerequisites.md ends up as /docs/setup/prerequisites.
    r = client.get("/docs/setup")
    assert 'href="/docs/setup/prerequisites"' in r.text
    assert 'href="/docs/setup/install-docker"' in r.text
    assert 'href="/docs/setup/install-linux"' in r.text


def test_troubleshooting_cross_links_rewrite(client: TestClient) -> None:
    r = client.get("/docs/troubleshooting")
    assert 'href="/docs/setup"' in r.text
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
