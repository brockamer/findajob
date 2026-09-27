"""Every response carries a Content-Security-Policy, the middleware leaves
streaming untouched, and templates keep unescaped output confined to the
sanitized Markdown renderers."""

from __future__ import annotations

import asyncio
import base64
import re
import sqlite3
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient
from starlette.types import Message, Receive, Scope, Send

from findajob.onboarding import mark_complete
from findajob.web.app import create_app
from findajob.web.middleware import CONTENT_SECURITY_POLICY, SecurityHeadersMiddleware
from tests.conftest import init_test_db

TEMPLATES = Path(__file__).resolve().parent.parent / "src" / "findajob" / "web" / "templates"


def _app(tmp_path: Path):
    db = tmp_path / "pipeline.db"
    init_test_db(db)
    (tmp_path / "companies").mkdir()
    mark_complete(tmp_path)
    return create_app(companies_root=tmp_path / "companies", db_path=db, base_root=tmp_path)


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.delenv("FINDAJOB_AUTH_USER", raising=False)
    monkeypatch.delenv("FINDAJOB_AUTH_PASS", raising=False)
    return TestClient(_app(tmp_path), follow_redirects=False)


def _directives(policy: str) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for part in policy.split(";"):
        tokens = part.split()
        if tokens:
            out[tokens[0]] = set(tokens[1:])
    return out


# --- Policy content ---------------------------------------------------------


def test_policy_closes_the_vectors_it_claims_to() -> None:
    d = _directives(CONTENT_SECURITY_POLICY)
    assert d["default-src"] == {"'self'"}
    assert d["object-src"] == {"'none'"}
    assert d["base-uri"] == {"'self'"}
    assert d["form-action"] == {"'self'"}
    assert d["frame-ancestors"] == {"'none'"}
    # connect-src falls back to default-src 'self'.
    assert "connect-src" not in d
    # Scripts: this origin plus the CDN hosts base.html names, nothing wider.
    assert "*" not in d["script-src"]
    assert "https:" not in d["script-src"]
    assert "data:" not in d["script-src"]


def test_every_external_script_in_templates_is_allowed_by_script_src() -> None:
    allowed = {t for t in _directives(CONTENT_SECURITY_POLICY)["script-src"] if t.startswith("https://")}
    for path in TEMPLATES.rglob("*.html"):
        for src in re.findall(r'<script[^>]+src="(https?://[^"]+)"', path.read_text()):
            origin = "{0.scheme}://{0.netloc}".format(urlsplit(src))
            assert origin in allowed, f"{path.relative_to(TEMPLATES)} loads {src}, which the CSP blocks"


# --- Header on real responses -----------------------------------------------


@pytest.mark.parametrize("path", ["/healthz", "/docs/", "/board/dashboard", "/no-such-page"])
def test_responses_carry_csp(client: TestClient, path: str) -> None:
    r = client.get(path)
    assert r.headers.get("content-security-policy") == CONTENT_SECURITY_POLICY


def test_jd_viewer_response_carries_csp(client: TestClient, tmp_path: Path) -> None:
    conn = sqlite3.connect(tmp_path / "pipeline.db")
    conn.execute(
        "INSERT INTO jobs (id, fingerprint, url, title, company, source, stage, raw_jd_text) "
        "VALUES ('j1', 'fp1', 'https://example.com/j', 'Engineer', 'Acme', 'test', 'scored', 'Body.')"
    )
    conn.commit()
    conn.close()
    r = client.get("/jobs/fp1/jd")
    assert r.status_code == 200
    assert r.headers.get("content-security-policy") == CONTENT_SECURITY_POLICY


def test_auth_challenge_and_authenticated_page_carry_csp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FINDAJOB_AUTH_USER", "tester")
    monkeypatch.setenv("FINDAJOB_AUTH_PASS", "s3cret-token-xyz")
    client = TestClient(_app(tmp_path), follow_redirects=False)

    anon = client.get("/docs/")
    assert anon.status_code == 401
    assert anon.headers.get("content-security-policy") == CONTENT_SECURITY_POLICY

    token = base64.b64encode(b"tester:s3cret-token-xyz").decode("ascii")
    authed = client.get("/docs/", headers={"Authorization": f"Basic {token}"})
    assert authed.status_code == 200
    assert authed.headers.get("content-security-policy") == CONTENT_SECURITY_POLICY


# --- Middleware mechanics ---------------------------------------------------


def _run(app, scope: Scope) -> list[Message]:
    sent: list[Message] = []

    async def receive() -> Message:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: Message) -> None:
        sent.append(message)

    asyncio.run(app(scope, receive, send))
    return sent


def _http_scope() -> Scope:
    return {"type": "http", "method": "GET", "path": "/", "headers": []}


def test_route_supplied_policy_is_not_overwritten() -> None:
    strict = b"default-src 'none'"

    async def inner(scope: Scope, receive: Receive, send: Send) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": [(b"Content-Security-Policy", strict)]})
        await send({"type": "http.response.body", "body": b"ok"})

    sent = _run(SecurityHeadersMiddleware(inner), _http_scope())
    csp = [v for k, v in sent[0]["headers"] if k.lower() == b"content-security-policy"]
    assert csp == [strict]


def test_streamed_body_chunks_pass_through_unchanged() -> None:
    chunks = [b"event: a\ndata: 1\n\n", b"event: b\ndata: 2\n\n", b""]

    async def inner(scope: Scope, receive: Receive, send: Send) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"text/event-stream")]})
        for i, chunk in enumerate(chunks):
            await send({"type": "http.response.body", "body": chunk, "more_body": i < len(chunks) - 1})

    sent = _run(SecurityHeadersMiddleware(inner), _http_scope())
    assert [m["type"] for m in sent] == ["http.response.start"] + ["http.response.body"] * len(chunks)
    assert [m["body"] for m in sent[1:]] == chunks
    assert [m.get("more_body") for m in sent[1:]] == [True, True, False]
    assert (b"content-security-policy", CONTENT_SECURITY_POLICY.encode()) in sent[0]["headers"]


def test_non_http_scopes_pass_through() -> None:
    seen: list[Scope] = []

    async def inner(scope: Scope, receive: Receive, send: Send) -> None:
        seen.append(scope)

    scope: Scope = {"type": "lifespan"}
    _run(SecurityHeadersMiddleware(inner), scope)
    assert seen == [scope]


# --- Template guard: unescaped output stays confined -----------------------

# The only template expressions allowed to bypass autoescaping. Each one is
# the output of findajob.web.markdown's sanitizing renderers. Adding an entry
# here means the new value must come from those renderers too.
_ALLOWED_SAFE_OUTPUTS = {
    ("base.html", "_rendered_md"),
    ("docs/page.html", "rendered_md"),
    ("onboarding/_turn_bubble.html", "turn.rendered_content"),
    ("speculative/review.html", "briefing_html"),
}
_SAFE_EXPR_RE = re.compile(r"\{\{\s*([\w.]+)\s*\|\s*safe\s*\}\}")
_UNESCAPE_RE = re.compile(r"\|\s*safe\b|\bMarkup\(|autoescape\s+false")


def _template_code(path: Path) -> str:
    """Template text with Jinja comments removed (comments may mention `| safe`)."""
    return re.sub(r"\{#.*?#\}", "", path.read_text(), flags=re.S)


def test_unescaped_output_only_at_sanitized_renderer_sites() -> None:
    found: set[tuple[str, str]] = set()
    for path in TEMPLATES.rglob("*.html"):
        code = _template_code(path)
        rel = path.relative_to(TEMPLATES).as_posix()
        exprs = _SAFE_EXPR_RE.findall(code)
        found.update((rel, e) for e in exprs)
        # Any other unescaping construct (filter chains, Markup, autoescape
        # blocks) has no matching allowlist entry and fails here.
        assert len(_UNESCAPE_RE.findall(code)) == len(exprs), f"{rel}: unescaped output outside the allowlist"
    assert found == _ALLOWED_SAFE_OUTPUTS


def test_json_embeds_use_tojson() -> None:
    """`json.dumps(...) | safe` does not escape `</script>`; `| tojson` does."""
    embeds = 0
    for path in TEMPLATES.rglob("*.html"):
        code = _template_code(path)
        for body in re.findall(r'<script[^>]*type="application/json"[^>]*>(.*?)</script>', code, re.S):
            embeds += 1
            assert re.fullmatch(r"\s*\{\{[^}]*\|\s*tojson\s*\}\}\s*", body), (
                f"{path.relative_to(TEMPLATES)}: JSON embed not built with | tojson: {body!r}"
            )
    assert embeds > 0, "guard found no JSON embeds; the pattern no longer matches the templates"


def test_operator_docs_show_the_policy_in_force() -> None:
    doc = Path(__file__).resolve().parent.parent / "docs" / "operations" / "internet-exposure.md"
    assert CONTENT_SECURITY_POLICY in doc.read_text(), "internet-exposure.md shows a stale Content-Security-Policy"
