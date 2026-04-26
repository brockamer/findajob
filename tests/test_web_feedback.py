"""Tests for the in-app feedback widget endpoint (#227).

Validates the POST /feedback/submit route under four scenarios:
empty text, missing PAT, GitHub API success, GitHub API failure. The
GitHub HTTP call is mocked at the ``requests.post`` boundary so tests
never hit a live API.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from findajob.web.app import create_app

_SCHEMA = """
CREATE TABLE jobs (id TEXT PRIMARY KEY);
"""


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    db_path = tmp_path / "pipeline.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(_SCHEMA)
    conn.close()

    companies = tmp_path / "companies"
    companies.mkdir()

    # Onboarding guard — synthesize the sentinel so /feedback/ isn't gated.
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / ".onboarding-complete").write_text("ok")

    monkeypatch.setenv("JSP_BASE", str(tmp_path))
    monkeypatch.delenv("GITHUB_FEEDBACK_PAT", raising=False)
    monkeypatch.delenv("FEEDBACK_STACK_LABEL", raising=False)
    monkeypatch.delenv("FEEDBACK_REPO", raising=False)

    app = create_app(companies_root=companies, db_path=db_path, base_root=tmp_path)
    return TestClient(app)


class _FakeResponse:
    def __init__(self, status_code: int, json_body: dict | None = None) -> None:
        self.status_code = status_code
        self._json = json_body or {}

    def json(self) -> dict:
        return self._json


def test_empty_text_returns_400(client: TestClient) -> None:
    r = client.post("/feedback/submit", data={"text": "   ", "page_url": "/"})
    assert r.status_code == 400
    assert "write something" in r.text.lower()


def test_missing_pat_returns_503(client: TestClient) -> None:
    # No GITHUB_FEEDBACK_PAT in env — fixture explicitly clears it.
    r = client.post("/feedback/submit", data={"text": "thing is broken", "page_url": "/board/"})
    assert r.status_code == 503
    assert "isn't configured" in r.text.lower() or "operator" in r.text.lower()


def test_success_path_files_issue(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_FEEDBACK_PAT", "github_pat_dummy")
    monkeypatch.setenv("FEEDBACK_STACK_LABEL", "from:operator")

    captured: dict = {}

    def fake_post(url, headers=None, json=None, timeout=None):  # noqa: A002 — match requests signature
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        return _FakeResponse(201, {"html_url": "https://github.com/brockamer/findajob/issues/999"})

    with patch("findajob.web.routes.feedback.requests.post", side_effect=fake_post):
        r = client.post(
            "/feedback/submit",
            data={"text": "Promote button hidden under filter\nrepro: ...", "page_url": "/board/archive"},
        )

    assert r.status_code == 200
    assert "thanks" in r.text.lower()
    assert "issues/999" in r.text

    assert captured["url"] == "https://api.github.com/repos/brockamer/findajob/issues"
    assert captured["headers"]["Authorization"] == "Bearer github_pat_dummy"
    payload = captured["json"]
    assert payload["title"] == "Promote button hidden under filter"
    assert payload["labels"] == ["feedback", "from:operator"]
    assert "repro: ..." in payload["body"]
    assert "/board/archive" in payload["body"]
    assert "from:operator" in payload["body"]


def test_success_without_stack_label_files_only_feedback_label(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GITHUB_FEEDBACK_PAT", "github_pat_dummy")
    # FEEDBACK_STACK_LABEL not set.

    captured: dict = {}

    def fake_post(url, headers=None, json=None, timeout=None):  # noqa: A002
        captured["json"] = json
        return _FakeResponse(201, {"html_url": "https://github.com/brockamer/findajob/issues/1000"})

    with patch("findajob.web.routes.feedback.requests.post", side_effect=fake_post):
        r = client.post("/feedback/submit", data={"text": "small bug", "page_url": ""})

    assert r.status_code == 200
    assert captured["json"]["labels"] == ["feedback"]


def test_github_api_failure_returns_502(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_FEEDBACK_PAT", "github_pat_dummy")

    with patch(
        "findajob.web.routes.feedback.requests.post",
        return_value=_FakeResponse(401, {"message": "Bad credentials"}),
    ):
        r = client.post("/feedback/submit", data={"text": "x", "page_url": "/"})

    assert r.status_code == 502
    assert "github" in r.text.lower()


def test_long_first_line_truncates_title(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_FEEDBACK_PAT", "github_pat_dummy")
    long_line = "x" * 200
    captured: dict = {}

    def fake_post(url, headers=None, json=None, timeout=None):  # noqa: A002
        captured["title"] = json["title"]
        return _FakeResponse(201, {"html_url": "https://example.test/1"})

    with patch("findajob.web.routes.feedback.requests.post", side_effect=fake_post):
        r = client.post("/feedback/submit", data={"text": long_line, "page_url": "/"})

    assert r.status_code == 200
    assert captured["title"].endswith("…")
    assert len(captured["title"]) <= 70
