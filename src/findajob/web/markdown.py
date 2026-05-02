"""Server-side Markdown → HTML rendering shared by the materials and docs viewers."""

from __future__ import annotations

import html as _html_stdlib
import re

import markdown as md_lib

from findajob.onboarding.parser import BLOCK_RE as _FILE_BLOCK_RE

_CENTERED_BLOCK_RE = re.compile(r":::centered\n([\s\S]*?)\n:::")
_LANG_CLASS_RE = re.compile(r'(<code[^>]*?) class="language-[^"]*"')
_SCRIPT_RE = re.compile(r"<(/?script)", re.IGNORECASE)
_ANCHOR_OPEN_RE = re.compile(r"<a\s+([^>]*?)>", re.IGNORECASE)
_HREF_ATTR_RE = re.compile(r'href="([^"]+)"', re.IGNORECASE)
_EXTERNAL_SCHEMES = ("http://", "https://", "mailto:")


def render_markdown(text: str, *, source: str = "") -> str:
    """Render Markdown to HTML with findajob-specific post-processing.

    Post-processing steps applied after Python-Markdown runs:
    - `:::centered` fenced blocks → centered divs (pre-parse).
    - Language class attributes stripped from fenced code (``` blocks).
    - Raw `<script>` tags neutralized.
    - External links (http/https/mailto) get `target="_blank" rel="noopener noreferrer"`.
    - `.md` links are rewritten to `/docs/<slug>` when `source` is a
      docs-relative path (e.g., "setup/README.md"); when `source` is empty
      (the materials use case), `.md` links are left untouched.
    """
    text = _CENTERED_BLOCK_RE.sub(
        lambda m: f'<div class="text-center" markdown="1">\n{m.group(1)}\n</div>',
        text,
    )
    # `toc` adds `id=` attributes to headings so in-page `#section` anchors
    # resolve. Enable it only for docs (where `source` is set) to keep the
    # materials viewer's output byte-identical to its pre-refactor form.
    extensions = ["fenced_code", "tables", "md_in_html"]
    if source:
        extensions.append("toc")
    html = md_lib.markdown(text, extensions=extensions, output_format="html")
    html = _LANG_CLASS_RE.sub(r"\1", html)
    html = _SCRIPT_RE.sub(r"&lt;\1", html)
    html = _ANCHOR_OPEN_RE.sub(lambda m: _rewrite_anchor(m, source=source), html)
    return html


def _rewrite_anchor(match: re.Match[str], *, source: str) -> str:
    attrs = match.group(1)
    href_match = _HREF_ATTR_RE.search(attrs)
    if not href_match:
        return match.group(0)
    href = href_match.group(1)
    new_href, is_external = _transform_href(href, source=source)
    new_attrs = attrs[: href_match.start()] + f'href="{new_href}"' + attrs[href_match.end() :]
    if is_external and "target=" not in new_attrs.lower():
        new_attrs = new_attrs.rstrip() + ' target="_blank" rel="noopener noreferrer"'
    return f"<a {new_attrs}>"


def _transform_href(href: str, *, source: str) -> tuple[str, bool]:
    if href.lower().startswith(_EXTERNAL_SCHEMES):
        return href, True
    if href.startswith("#") or not source:
        return href, False
    path_part, fragment = (href.split("#", 1) + [""])[:2]
    if not path_part.endswith(".md"):
        return href, False
    source_dir = "/".join(source.split("/")[:-1])
    combined = f"{source_dir}/{path_part}" if source_dir else path_part
    parts: list[str] = []
    for seg in combined.split("/"):
        if seg == "..":
            if parts:
                parts.pop()
        elif seg and seg != ".":
            parts.append(seg)
    slug = "/".join(parts)[: -len(".md")]
    if slug.endswith("/README"):
        slug = slug[: -len("/README")]
    elif slug == "README":
        slug = ""
    new_href = f"/docs/{slug}" if slug else "/docs/"
    return (f"{new_href}#{fragment}" if fragment else new_href), False


def render_chat_assistant_html(text: str) -> str:
    """Render an onboarding assistant chat turn to safe HTML.

    Two-step process:
    1. Replace ``<<<FILE: name>>> ... <<<END FILE: name>>>`` blocks with an
       inline badge span so the multi-KB emission blocks don't clog the chat.
       Uses the same regex compiled in :mod:`findajob.onboarding.parser`
       (``BLOCK_RE``) so the render-side and parse-side patterns can never
       drift. **Note:** badging is render-only — the parser reads the raw
       stored transcript from ``session.history``, not rendered HTML, so this
       does not affect emission detection.
    2. Pass the result through ``markdown.markdown`` with ``fenced_code``,
       ``tables``, and ``md_in_html`` extensions.

    The ``_SCRIPT_RE`` neutralization from :func:`render_markdown` is applied
    to the output as a defense-in-depth measure. The docs-rewriting and
    external-link-rewriting paths are skipped — they don't apply to chat.
    """

    def _badge(match: re.Match[str]) -> str:
        name = match.group("name").strip()
        safe_name = _html_stdlib.escape(name)
        return (
            '<span class="captured-file inline-flex items-center gap-1 px-2 py-0.5'
            " rounded bg-amber-50 border border-amber-200 text-amber-900 text-xs"
            ' font-mono" title="Captured for the parser">'
            f"\U0001f4c4 Captured: {safe_name}</span>"
        )

    text = _FILE_BLOCK_RE.sub(_badge, text)
    html = md_lib.markdown(text, extensions=["fenced_code", "tables", "md_in_html"], output_format="html")
    html = _SCRIPT_RE.sub(r"&lt;\1", html)
    return html
