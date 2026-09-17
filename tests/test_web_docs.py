"""/docs/ user-facing docs viewer (#224)."""

from __future__ import annotations

import base64
import sqlite3
import textwrap
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from findajob.web.app import create_app
from findajob.web.markdown import render_markdown

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
GETTING_STARTED_INSTALL_FLY_MD = (
    "# Install on Fly\n\nThe CLI-tier Fly install.\n\n"
    "![Dashboard](install-fly-web/01-shot.png)\n\n"
    "![Remote](https://example.com/remote.png)\n"
)

# getting-started/README.md maps to slug "getting-started" — the img src must
# resolve against the source FILE's dir, not the slug's.
GETTING_STARTED_README_IMG = "\n![Readme shot](install-fly-web/01-shot.png)\n"

# Smallest valid PNG (1x1, transparent).
_PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)
GETTING_STARTED_API_KEYS_MD = "# API Keys\n\nSign up for OpenRouter.\n"
GETTING_STARTED_COST_MD = "# Cost\n\nWhat this costs to run.\n"
GETTING_STARTED_GMAIL_MD = "# Gmail\n\nGmail integration setup.\n"
GETTING_STARTED_NOTIFICATIONS_MD = "# Notifications\n\nntfy setup.\n"
GETTING_STARTED_INSTALL_DOCKER_MD = "# Install with Docker\n\nThe primary install path.\n"
CONFIG_REFERENCE_MD = "# Config Reference\n\nFile-by-file config walkthrough.\n"
TUNING_MD = "# Tuning\n\nHow to tune the scorer.\n"
UPDATING_MD = "# Updating\n\nHow to update findajob.\n"
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
    (docs / "updating.md").write_text(UPDATING_MD)
    (docs / "troubleshooting.md").write_text(TROUBLESHOOTING_MD)
    (docs / "getting-started" / "README.md").write_text(GETTING_STARTED_README_MD + GETTING_STARTED_README_IMG)
    (docs / "getting-started" / "prerequisites.md").write_text(GETTING_STARTED_PREREQ_MD)
    (docs / "getting-started" / "start-here-fly.md").write_text(GETTING_STARTED_START_HERE_FLY_MD)
    (docs / "getting-started" / "install-fly.md").write_text(GETTING_STARTED_INSTALL_FLY_MD)
    (docs / "getting-started" / "install-fly-web").mkdir(parents=True)
    (docs / "getting-started" / "install-fly-web" / "01-shot.png").write_bytes(_PNG_1X1)
    # Outside docs_root — the traversal guard must keep this unreachable.
    (tmp_path / "outside.png").write_bytes(_PNG_1X1)
    (docs / "getting-started" / "api-keys.md").write_text(GETTING_STARTED_API_KEYS_MD)
    (docs / "getting-started" / "cost.md").write_text(GETTING_STARTED_COST_MD)
    (docs / "getting-started" / "gmail.md").write_text(GETTING_STARTED_GMAIL_MD)
    (docs / "getting-started" / "notifications.md").write_text(GETTING_STARTED_NOTIFICATIONS_MD)
    (docs / "operations" / "install-docker.md").write_text(GETTING_STARTED_INSTALL_DOCKER_MD)
    (docs / "operations" / "config-reference.md").write_text(CONFIG_REFERENCE_MD)
    (docs / "operations" / "README.md").write_text(OPERATIONS_README_MD)
    (docs / "operations" / "internet-exposure.md").write_text(OPERATIONS_INTERNET_EXPOSURE_MD)

    return TestClient(create_app(companies_root=companies, db_path=db, base_root=tmp_path, image_root=tmp_path))


def test_index_lists_guides(client: TestClient) -> None:
    r = client.get("/docs/")
    assert r.status_code == 200
    assert 'href="/docs/getting-started"' in r.text
    assert 'href="/docs/usage"' in r.text
    assert 'href="/docs/operations"' in r.text
    assert 'href="/docs/troubleshooting"' in r.text
    # One-line descriptions surface on the index.
    assert "Daily workflow" in r.text


def test_index_surfaces_updating_and_tuning(client: TestClient) -> None:
    """#938: updating.md and tuning.md are in _PAGES but were undiscoverable from the index."""
    r = client.get("/docs/")
    assert r.status_code == 200
    assert 'href="/docs/updating"' in r.text
    assert 'href="/docs/tuning"' in r.text


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


# --- embedded doc images (#1053) -------------------------------------------


def test_doc_image_serves_through_viewer(client: TestClient) -> None:
    r = client.get("/docs/getting-started/install-fly-web/01-shot.png")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("image/")
    assert r.content == _PNG_1X1


def test_img_src_rewritten_to_docs_url(client: TestClient) -> None:
    r = client.get("/docs/getting-started/install-fly")
    assert r.status_code == 200
    assert 'src="/docs/getting-started/install-fly-web/01-shot.png"' in r.text
    assert 'src="install-fly-web/01-shot.png"' not in r.text


def test_img_src_resolves_against_source_file_dir_not_slug(client: TestClient) -> None:
    # Slug "getting-started" maps to getting-started/README.md. Resolving
    # against the slug would yield /docs/install-fly-web/01-shot.png.
    r = client.get("/docs/getting-started")
    assert r.status_code == 200
    assert 'src="/docs/getting-started/install-fly-web/01-shot.png"' in r.text


def test_external_img_src_untouched(client: TestClient) -> None:
    r = client.get("/docs/getting-started/install-fly")
    assert 'src="https://example.com/remote.png"' in r.text


def test_image_traversal_outside_docs_root_is_blocked(client: TestClient) -> None:
    # Without the relative_to() guard this resolves to tmp_path/outside.png,
    # which exists — so a 200 here means the guard is gone.
    r = client.get("/docs/getting-started/%2e%2e/%2e%2e/outside.png")
    assert r.status_code == 404


def test_markdown_source_is_not_served_as_an_asset(client: TestClient) -> None:
    # The _PAGES allowlist stays the only way to reach a .md file.
    r = client.get("/docs/getting-started/install-fly.md")
    assert r.status_code == 404


def test_materials_render_leaves_img_src_untouched() -> None:
    # The materials viewer calls render_markdown with no source.
    html = render_markdown("![shot](shot.png)\n")
    assert 'src="shot.png"' in html
    assert "/docs/" not in html
