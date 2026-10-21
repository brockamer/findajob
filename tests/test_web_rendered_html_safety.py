"""Rendered Markdown is untrusted input: scraped job descriptions, emails,
LLM-written materials and chat replies all reach the page through
``render_markdown`` / ``render_chat_assistant_html`` and are emitted with
``| safe``. These tests pin that the renderers' output carries no script
vector, and that the formatting findajob itself relies on survives.
"""

from __future__ import annotations

import html
import re
import sqlite3
from html.parser import HTMLParser
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from findajob.onboarding import mark_complete
from findajob.web.app import create_app
from findajob.web.markdown import render_chat_assistant_html, render_markdown
from tests.conftest import init_test_db

# Each payload is a known way to run script through an HTML-passthrough
# renderer. Markdown passes raw HTML through, so every one of these reaches
# the sanitizer as live markup.
HOSTILE_PAYLOADS: list[str] = [
    "<img src=x onerror=alert(1)>",
    '<img src="x" ONERROR="alert(1)">',
    "<svg onload=alert(1)><circle r=1></circle></svg>",
    '<iframe src="https://attacker.invalid/"></iframe>',
    "<details open ontoggle=alert(1)><summary>s</summary></details>",
    '<a href="javascript:alert(1)">click</a>',
    '<a href="JaVaScRiPt:alert(1)">click</a>',
    '<a href="&#106;avascript:alert(1)">click</a>',
    '<a href="  javascript:alert(1)">click</a>',
    "[md link](javascript:alert(1))",
    '<a href="data:text/html,&lt;script&gt;alert(1)&lt;/script&gt;">click</a>',
    '<img src="data:image/svg+xml,&lt;svg onload=alert(1)&gt;">',
    '<p onclick="alert(1)">para</p>',
    '<div onmouseover="alert(1)">div</div>',
    "<script>alert(1)</script>",
    "<SCRIPT SRC=https://attacker.invalid/x.js></SCRIPT>",
    '<object data="https://attacker.invalid/x.swf"></object>',
    '<embed src="https://attacker.invalid/x.swf">',
    '<form action="https://attacker.invalid/"><input name=p></form>',
    '<base href="https://attacker.invalid/">',
    '<meta http-equiv="refresh" content="0;url=https://attacker.invalid/">',
    "<style>body{display:none}</style>",
    '<link rel="stylesheet" href="https://attacker.invalid/x.css">',
    "<math><mtext><table><mglyph><style><img src=x onerror=alert(1)></style></mglyph></table></mtext></math>",
    '<div class="fixed inset-0 z-50 bg-white">overlay</div>',
    '<span style="position:fixed;top:0">overlay</span>',
    '<h2 id="initial-rows">clobber</h2>',
]

_FORBIDDEN_TAGS = frozenset(
    {"script", "svg", "math", "iframe", "object", "embed", "form", "input", "base", "meta", "style", "link"}
)
_ALLOWED_SCHEMES = frozenset({"http", "https", "mailto"})
_URL_ATTRS = frozenset({"href", "src", "action", "formaction", "xlink:href", "cite"})


class _Collector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.elements: list[tuple[str, list[tuple[str, str | None]]]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.elements.append((tag, attrs))

    handle_startendtag = handle_starttag


def _elements(markup: str) -> list[tuple[str, list[tuple[str, str | None]]]]:
    parser = _Collector()
    parser.feed(markup)
    parser.close()
    return parser.elements


def _scheme(url: str) -> str | None:
    """The URL scheme as a browser would see it, or None for a relative URL."""
    # Browsers strip leading/trailing C0 controls and spaces, and ignore
    # tab/newline anywhere, before parsing the scheme.
    cleaned = re.sub(r"[\t\n\r]", "", html.unescape(url)).strip("\x00- ")
    match = re.match(r"([A-Za-z][A-Za-z0-9+.-]*):", cleaned)
    return match.group(1).lower() if match else None


def _assert_inert(markup: str, *, allow_ids: bool = False) -> None:
    for tag, attrs in _elements(markup):
        assert tag not in _FORBIDDEN_TAGS, f"<{tag}> survived: {markup!r}"
        for name, value in attrs:
            assert not name.startswith("on"), f"event attribute {name} survived: {markup!r}"
            assert name != "style" or re.fullmatch(r"\s*text-align\s*:\s*\w+\s*;?\s*", value or ""), (
                f"style survived: {markup!r}"
            )
            if name == "id":
                assert allow_ids and re.fullmatch(r"h[1-6]", tag), f"id survived on <{tag}>: {markup!r}"
            if name in _URL_ATTRS and value is not None:
                scheme = _scheme(value)
                assert scheme is None or scheme in _ALLOWED_SCHEMES, f"{name}={value!r} survived: {markup!r}"
            if name == "class":
                assert set((value or "").split()) <= _KNOWN_CLASS_TOKENS, f"class {value!r} survived: {markup!r}"


# Class tokens findajob's own renderer emits. Anything else in a class
# attribute came from the input and can restyle the page (e.g. a full-screen
# overlay built from Tailwind utilities).
_KNOWN_CLASS_TOKENS = frozenset(
    {
        "text-center",
        "captured-file",
        "inline-flex",
        "items-center",
        "gap-1",
        "px-2",
        "py-0.5",
        "rounded",
        "bg-amber-50",
        "border",
        "border-amber-200",
        "text-amber-900",
        "text-xs",
        "font-mono",
    }
)


@pytest.mark.parametrize("payload", HOSTILE_PAYLOADS)
def test_render_markdown_neutralizes_hostile_html(payload: str) -> None:
    _assert_inert(render_markdown(f"Intro paragraph.\n\n{payload}\n\nOutro."))


@pytest.mark.parametrize("payload", HOSTILE_PAYLOADS)
def test_render_markdown_neutralizes_hostile_html_inline(payload: str) -> None:
    _assert_inert(render_markdown(f"Text before {payload} text after."))


@pytest.mark.parametrize("payload", HOSTILE_PAYLOADS)
def test_render_markdown_docs_mode_neutralizes_hostile_html(payload: str) -> None:
    _assert_inert(render_markdown(f"# Doc\n\n{payload}\n", source="usage/README.md"), allow_ids=True)


@pytest.mark.parametrize("payload", HOSTILE_PAYLOADS)
def test_render_chat_assistant_html_neutralizes_hostile_html(payload: str) -> None:
    _assert_inert(render_chat_assistant_html(f"Here is your summary.\n\n{payload}\n"))


def test_render_markdown_neutralizes_payload_inside_centered_block() -> None:
    out = render_markdown(":::centered\n<img src=x onerror=alert(1)>\n:::")
    _assert_inert(out)
    assert 'class="text-center"' in out


def test_script_content_is_removed_not_shown() -> None:
    out = render_markdown("<script>alert('pwned')</script>\n\nSafe text.")
    assert "alert(" not in out
    assert "Safe text." in out


# --- Formatting findajob relies on must survive ---------------------------


def test_safe_markdown_formatting_survives() -> None:
    md = (
        "# Title\n\n"
        "Some **bold**, *em* and `code`.\n\n"
        "- one\n- two\n\n"
        "Between lists.\n\n"
        "1. first\n2. second\n\n"
        "> quoted\n\n"
        "```python\nprint('hi')\n```\n\n"
        "| Left | Right |\n|:-----|------:|\n| a | b |\n\n"
        "<details><summary>More</summary>\n\nHidden body.\n\n</details>\n\n"
        "---\n\n"
        "![alt text](https://example.com/pic.png)\n"
    )
    out = render_markdown(md)
    for fragment in (
        "<h1>Title</h1>",
        "<strong>bold</strong>",
        "<em>em</em>",
        "<code>code</code>",
        "<li>one</li>",
        "<ol>",
        "<blockquote>",
        "<pre><code>print(",
        "<table>",
        'style="text-align:left"',
        'style="text-align:right"',
        "<details>",
        "<summary>More</summary>",
        "<hr>",
        'src="https://example.com/pic.png"',
        'alt="alt text"',
    ):
        assert fragment in out, f"{fragment!r} missing from {out!r}"
    _assert_inert(out)


def test_external_link_keeps_new_tab_and_noopener() -> None:
    out = render_markdown("[site](https://example.com/page)")
    assert 'href="https://example.com/page"' in out
    assert 'target="_blank"' in out
    assert 'rel="noopener noreferrer"' in out


def test_input_supplied_target_cannot_drop_noopener() -> None:
    out = render_markdown('<a href="https://example.com/" target="_blank" rel="opener">x</a>')
    assert 'rel="noopener noreferrer"' in out
    assert 'opener"' not in out.replace('rel="noopener noreferrer"', "")


def test_mailto_and_relative_links_survive() -> None:
    out = render_markdown("[mail](mailto:someone@example.com) [rel](/board/) [frag](#top)")
    assert 'href="mailto:someone@example.com"' in out
    assert 'href="/board/"' in out
    assert 'href="#top"' in out


def test_docs_mode_keeps_heading_ids_for_in_page_anchors() -> None:
    out = render_markdown("## Applied\n\n[jump](#applied)\n", source="usage/README.md")
    assert 'id="applied"' in out
    assert 'href="#applied"' in out


def test_content_mode_drops_input_ids() -> None:
    out = render_markdown('<h2 id="initial-rows">x</h2><p id="y">p</p>')
    assert "id=" not in out


def test_chat_badge_classes_and_title_survive() -> None:
    out = render_chat_assistant_html("Saved.\n\n<<<FILE: profile.md>>>\nbody\n<<<END FILE: profile.md>>>\n")
    assert "captured-file" in out
    assert 'title="Captured for the parser"' in out
    assert "Captured: profile.md" in out
    _assert_inert(out)


# --- End to end through the job-description viewer -------------------------


def test_jd_viewer_serves_hostile_job_description_inert(tmp_path: Path) -> None:
    db = tmp_path / "pipeline.db"
    init_test_db(db)
    jd = (
        "About the role.\n\n"
        "<img src=x onerror=alert(document.cookie)>\n\n"
        "<p onmouseover=\"fetch('/config/')\">Hover me</p>\n\n"
        '<a href="javascript:alert(1)">Apply here</a>\n'
    )
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO jobs (id, fingerprint, url, title, company, source, stage, raw_jd_text) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "jid-x",
            "fp-hostile",
            "https://example.com/job",
            '<svg onload="alert(1)">Engineer',
            "<img src=y onerror=alert(2)>Acme",
            "test",
            "scored",
            jd,
        ),
    )
    conn.commit()
    conn.close()
    companies = tmp_path / "companies"
    companies.mkdir()
    mark_complete(tmp_path)
    client = TestClient(create_app(companies_root=companies, db_path=db, base_root=tmp_path))

    r = client.get("/jobs/fp-hostile/jd")
    assert r.status_code == 200
    match = re.search(r'<div class="prose prose-slate max-w-none">(.*)</div>', r.text, re.S)
    assert match, "rendered JD block not found"
    _assert_inert(match.group(1))
    assert "About the role." in r.text
    assert "Hover me" in r.text
    assert "Apply here" in r.text
    assert "Acme" in r.text


class _AnchorCollector(HTMLParser):
    """Collects ``(href, text)`` for every ``<a>`` element."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.anchors: list[tuple[str | None, str]] = []
        self._open: list[str | None] | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            self._open = [dict(attrs).get("href")]
            self._text = []

    def handle_data(self, data: str) -> None:
        if self._open is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._open is not None:
            self.anchors.append((self._open[0], "".join(self._text).strip()))
            self._open = None


def _anchors(markup: str) -> list[tuple[str | None, str]]:
    parser = _AnchorCollector()
    parser.feed(markup)
    parser.close()
    return parser.anchors


# (title, url): a job-row title link renders an href only for http(s) URLs.
_NON_HTTP_TITLE_URLS: list[tuple[str, str]] = [
    ("Acme Widget Engineer JS", "javascript:alert(1)"),
    ("Acme Widget Engineer JS Mixed", "  JaVaScRiPt:alert(1)"),
    ("Acme Widget Engineer Data", "data:text/html,x"),
    ("Acme Widget Engineer File", "file:///etc/passwd"),
]


@pytest.mark.parametrize("path", ["/board/dashboard", "/board/dashboard/rows"])
def test_job_row_title_link_renders_href_only_for_http_urls(tmp_path: Path, path: str) -> None:
    db = tmp_path / "pipeline.db"
    init_test_db(db)
    rows = [*_NON_HTTP_TITLE_URLS, ("Acme Widget Engineer Https", "https://example.com/job")]
    conn = sqlite3.connect(db)
    for i, (title, url) in enumerate(rows):
        conn.execute(
            "INSERT INTO jobs (id, fingerprint, url, title, company, source, stage, relevance_score) "
            "VALUES (?, ?, ?, ?, ?, 'test', 'scored', 8)",
            (f"jid-{i}", f"fp-link-{i}", url, title, "Acme Widgets"),
        )
    conn.commit()
    conn.close()
    companies = tmp_path / "companies"
    companies.mkdir()
    mark_complete(tmp_path)
    client = TestClient(create_app(companies_root=companies, db_path=db, base_root=tmp_path))

    r = client.get(path, headers={"HX-Request": "true"})
    assert r.status_code == 200
    anchors = _anchors(r.text)

    # Every row rendered, so the absence checks below cannot pass vacuously.
    for title, _url in rows:
        assert title in r.text, f"row {title!r} did not render"

    # A non-http(s) URL leaves the title as plain text: no anchor carries it.
    for title, _url in _NON_HTTP_TITLE_URLS:
        assert not [a for a in anchors if a[1] == title], f"{title!r} rendered as a link"
    # No href anywhere on the page uses a scheme other than http(s).
    for href, text in anchors:
        if href is not None:
            assert _scheme(href) in (None, "http", "https"), f"href={href!r} on {text!r}"
    raw = r.text.lower()
    for needle in ("javascript:", "data:text", "file:///"):
        assert needle not in raw, f"{needle!r} reached the page"

    # An http(s) URL still links.
    assert ("https://example.com/job", "Acme Widget Engineer Https") in anchors
