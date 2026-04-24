"""Server-side Markdown → HTML rendering shared by the materials and docs viewers."""

from __future__ import annotations

import re

import markdown as md_lib

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
