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

    ## 2. Install → [`install-docker.md`](../operations/install-docker.md)

    Also see [`../troubleshooting.md`](../troubleshooting.md).
    """
)


GETTING_STARTED_PREREQ_MD = "# Prerequisites\n\nNeeded before install.\n"
GETTING_STARTED_START_HERE_FLY_MD = "# Start Here (Fly)\n\nThe beginner Fly install.\n"
GETTING_STARTED_INSTALL_FLY_MD = "# Install on Fly\n\nThe CLI-tier Fly install.\n"
GETTING_STARTED_API_KEYS_MD = "# API Keys\n\nSign up for OpenRouter.\n"
GETTING_STARTED_COST_MD = "# Cost\n\nWhat this costs to run.\n"
GETTING_STARTED_GMAIL_MD = "# Gmail\n\nGmail integration setup.\n"
GETTING_STARTED_NOTIFICATIONS_MD = "# Notifications\n\nntfy setup.\n"
GETTING_STARTED_INSTALL_DOCKER_MD = "# Install with Docker\n\nThe primary install path.\n"
CONFIG_REFERENCE_MD = "# Config Reference\n\nFile-by-file config walkthrough.\n"
TUNING_MD = "# Tuning\n\nHow to tune the scorer.\n"
OPERATIONS_README_MD = "# Operations\n\nManual commands, log rotation, restore.\n"
OPERATIONS_INTERNET_EXPOSURE_MD = "# Exposing findajob to the public internet\n\nBasic auth pattern.\n"


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    db = tmp_path / "pipeline.db"
    sqlite3.connect(db).close()
    companies = tmp_path / "companies"
    companies.mkdir()

    # Seed docs/ under tmp_path so app.state.image_root=tmp_path finds them.
    # image_root (#771) is the code-path root — docs/ is image-bound, not
    # volume-bound, so the docs route reads through image_root, not base_root.
    docs = tmp_path / "docs"
    (docs / "getting-started").mkdir(parents=True)
    (docs / "operations").mkdir(parents=True)
    (docs / "usage.md").write_text(USAGE_MD)
    (docs / "tuning.md").write_text(TUNING_MD)
    (docs / "troubleshooting.md").write_text(TROUBLESHOOTING_MD)
    (docs / "getting-started" / "README.md").write_text(GETTING_STARTED_README_MD)
    (docs / "getting-started" / "prerequisites.md").write_text(GETTING_STARTED_PREREQ_MD)
    (docs / "getting-started" / "start-here-fly.md").write_text(GETTING_STARTED_START_HERE_FLY_MD)
    (docs / "getting-started" / "install-fly.md").write_text(GETTING_STARTED_INSTALL_FLY_MD)
    (docs / "getting-started" / "api-keys.md").write_text(GETTING_STARTED_API_KEYS_MD)
    (docs / "getting-started" / "cost.md").write_text(GETTING_STARTED_COST_MD)
    (docs / "getting-started" / "gmail.md").write_text(GETTING_STARTED_GMAIL_MD)
    (docs / "getting-started" / "notifications.md").write_text(GETTING_STARTED_NOTIFICATIONS_MD)
    (docs / "operations" / "install-docker.md").write_text(GETTING_STARTED_INSTALL_DOCKER_MD)
    (docs / "operations" / "config-reference.md").write_text(CONFIG_REFERENCE_MD)
    (docs / "operations" / "README.md").write_text(OPERATIONS_README_MD)
    (docs / "operations" / "internet-exposure.md").write_text(OPERATIONS_INTERNET_EXPOSURE_MD)

    return TestClient(create_app(companies_root=companies, db_path=db, base_root=tmp_path, image_root=tmp_path))


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
    c = TestClient(create_app(companies_root=companies, db_path=db, base_root=tmp_path, image_root=tmp_path))
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


def test_operations_install_docker_renders(client: TestClient) -> None:
    r = client.get("/docs/operations/install-docker")
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
    assert 'href="/docs/operations/install-docker"' in r.text


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


def test_index_has_getting_started_cta(client: TestClient) -> None:
    r = client.get("/docs/")
    assert "New here?" in r.text
    assert 'href="/docs/getting-started/start-here-fly"' in r.text


def test_breadcrumbs_on_subpage(client: TestClient) -> None:
    r = client.get("/docs/getting-started/install-fly")
    assert r.status_code == 200
    assert 'href="/docs/"' in r.text
    assert 'href="/docs/getting-started"' in r.text
    assert "Install Fly" in r.text


def test_breadcrumbs_on_top_level_page(client: TestClient) -> None:
    r = client.get("/docs/usage")
    assert r.status_code == 200
    assert 'href="/docs/"' in r.text
    assert "Usage" in r.text


def test_next_step_on_sequential_page(client: TestClient) -> None:
    r = client.get("/docs/getting-started/install-fly")
    assert r.status_code == 200
    assert 'href="/docs/getting-started/api-keys"' in r.text
    assert "Next:" in r.text


def test_no_next_step_on_terminal_page(client: TestClient) -> None:
    r = client.get("/docs/usage")
    assert "Next:" not in r.text


def test_tuning_page_renders(client: TestClient) -> None:
    r = client.get("/docs/tuning")
    assert r.status_code == 200
    assert ">Tuning</h1>" in r.text
