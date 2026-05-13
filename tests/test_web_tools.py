"""Integration tests for the /tools/ page (#150)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from findajob.web.app import create_app
from findajob.web.tools_registry import (
    MAX_PROMPT_URL_LEN,
    TILES,
    claude_url_for,
    hydrate_tiles,
    load_prompt,
)

_MINIMAL_SCHEMA = """
CREATE TABLE jobs (
    id TEXT PRIMARY KEY,
    fingerprint TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL,
    company TEXT NOT NULL,
    stage TEXT DEFAULT 'discovered',
    created_at TEXT DEFAULT (datetime('now')),
    synthetic INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL,
    field_changed TEXT NOT NULL,
    old_value TEXT,
    new_value TEXT,
    changed_at TEXT DEFAULT (datetime('now'))
);
"""


@pytest.fixture()
def base_root(tmp_path: Path) -> Path:
    prompts = tmp_path / "config" / "tool_prompts"
    prompts.mkdir(parents=True)
    (prompts / "profile_refresh.md").write_text("Refresh prompt body.\n")
    (prompts / "exclusion_tuning.md").write_text("Exclusion prompt body.\n")
    (prompts / "cover_letter_voice.md").write_text("Voice prompt body.\n")
    # candidate_context/ for the onboarding-sentinel side; absent triggers
    # onboarding guard, but /tools/ sits outside the guard so we leave it.
    return tmp_path


@pytest.fixture()
def client(base_root: Path, tmp_path: Path) -> TestClient:
    db_path = tmp_path / "pipeline.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(_MINIMAL_SCHEMA)
    conn.close()
    companies = tmp_path / "companies"
    companies.mkdir()
    app = create_app(
        companies_root=companies,
        db_path=db_path,
        base_root=base_root,
    )
    return TestClient(app)


# -- Registry shape ----------------------------------------------------------


def test_registry_has_required_prompt_slugs() -> None:
    expected = {"profile_refresh", "exclusion_tuning", "cover_letter_voice"}
    prompt_slugs = {t["slug"] for t in TILES if t["kind"] == "prompt"}
    assert expected.issubset(prompt_slugs), f"missing prompt tiles: {expected - prompt_slugs}"


def test_registry_has_link_tiles_to_onboarding_and_config() -> None:
    hrefs = {t["href"] for t in TILES if t["kind"] == "link"}
    assert "/onboarding/?mode=rerun" in hrefs
    assert "/config/" in hrefs


def test_registry_every_tile_has_kind_link_or_prompt() -> None:
    for tile in TILES:
        assert tile["kind"] in ("link", "prompt"), tile


# -- load_prompt -------------------------------------------------------------


def test_load_prompt_reads_disk(base_root: Path) -> None:
    body = load_prompt(base_root, "profile_refresh.md")
    assert "Refresh prompt body" in body


def test_load_prompt_missing_returns_empty(base_root: Path) -> None:
    body = load_prompt(base_root, "nonexistent.md")
    assert body == ""


def test_load_prompt_empty_filename_returns_empty(base_root: Path) -> None:
    # Link tiles have prompt_file=""; the loader shouldn't try to open ""/foo.
    body = load_prompt(base_root, "")
    assert body == ""


# -- claude_url_for ----------------------------------------------------------


def test_claude_url_short_prompt_returns_url() -> None:
    url = claude_url_for("hello world")
    assert url is not None
    assert url.startswith("https://claude.ai/new?q=")
    assert "hello%20world" in url


def test_claude_url_empty_returns_none() -> None:
    assert claude_url_for("") is None
    assert claude_url_for("   \n  \n") is None


def test_claude_url_too_long_returns_none() -> None:
    # Encoded length depends on which chars; an ASCII string of plain alpha
    # encodes 1:1, so > MAX_PROMPT_URL_LEN raw will exceed the cap.
    big = "a" * (MAX_PROMPT_URL_LEN + 1)
    assert claude_url_for(big) is None


def test_claude_url_encodes_special_chars() -> None:
    # Newlines and slashes must be encoded — they're literal in the prompt
    # but reserved in a URL query.
    url = claude_url_for("line one\nline two & path/here")
    assert url is not None
    assert "\n" not in url  # must be %0A
    assert "%0A" in url
    assert "%26" in url  # &


# -- hydrate_tiles -----------------------------------------------------------


def test_hydrate_tiles_attaches_body_and_claude_url_to_prompt_tiles(
    base_root: Path,
) -> None:
    tiles = hydrate_tiles(base_root)
    by_slug = {t["slug"]: t for t in tiles}
    pr = by_slug["profile_refresh"]
    assert pr["body"] == "Refresh prompt body.\n"
    assert isinstance(pr["claude_url"], str)
    assert "claude.ai/new?q=" in pr["claude_url"]  # type: ignore[operator]


def test_hydrate_tiles_link_tiles_have_no_body_key(base_root: Path) -> None:
    tiles = hydrate_tiles(base_root)
    by_slug = {t["slug"]: t for t in tiles}
    config_tile = by_slug["config_editor"]
    assert "body" not in config_tile
    assert config_tile["href"] == "/config/"


def test_hydrate_tiles_missing_prompt_file_yields_empty_body_and_none_url(
    tmp_path: Path,
) -> None:
    # No config/tool_prompts/ directory at all.
    tiles = hydrate_tiles(tmp_path)
    by_slug = {t["slug"]: t for t in tiles}
    pr = by_slug["profile_refresh"]
    assert pr["body"] == ""
    assert pr["claude_url"] is None


# -- HTTP route --------------------------------------------------------------


def test_get_tools_returns_200(client: TestClient) -> None:
    resp = client.get("/tools/")
    assert resp.status_code == 200


def test_get_tools_renders_every_tile_title(client: TestClient) -> None:
    html = client.get("/tools/").text
    for tile in TILES:
        assert tile["title"] in html, f"tile {tile['slug']} title missing"


def test_get_tools_renders_link_tile_anchors(client: TestClient) -> None:
    html = client.get("/tools/").text
    assert 'href="/config/"' in html
    assert 'href="/onboarding/?mode=rerun"' in html


def test_get_tools_renders_copy_button_per_prompt_tile(client: TestClient) -> None:
    html = client.get("/tools/").text
    expected = sum(1 for t in TILES if t["kind"] == "prompt")
    copy_buttons = html.count('data-action="copy-prompt"')
    assert copy_buttons == expected


def test_get_tools_renders_open_in_claude_for_short_prompts(
    client: TestClient,
) -> None:
    html = client.get("/tools/").text
    # Fixtures use short prompt bodies → each prompt tile gets an Open-in-Claude
    # anchor. Substring is sufficient to verify the affordance exists.
    assert html.count("claude.ai/new?q=") >= 3


def test_get_tools_omits_open_in_claude_when_prompt_too_long(
    tmp_path: Path,
) -> None:
    # Write a prompt that exceeds MAX_PROMPT_URL_LEN so the URL is None.
    prompts = tmp_path / "config" / "tool_prompts"
    prompts.mkdir(parents=True)
    huge = "x" * (MAX_PROMPT_URL_LEN + 100)
    (prompts / "profile_refresh.md").write_text(huge)
    (prompts / "exclusion_tuning.md").write_text("short")
    (prompts / "cover_letter_voice.md").write_text("short")

    db_path = tmp_path / "pipeline.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(_MINIMAL_SCHEMA)
    conn.close()
    (tmp_path / "companies").mkdir()
    app = create_app(
        companies_root=tmp_path / "companies",
        db_path=db_path,
        base_root=tmp_path,
    )
    html = TestClient(app).get("/tools/").text
    # Two short prompts each get an Open-in-Claude anchor; the giant one
    # does not. So total claude.ai/new?q= occurrences should be exactly 2.
    assert html.count("claude.ai/new?q=") == 2


def test_get_tools_is_not_behind_onboarding_guard(client: TestClient) -> None:
    # /tools/ must be reachable mid-onboarding so the operator can use the
    # config editor link before completing the interview.
    resp = client.get("/tools/")
    assert resp.status_code == 200
