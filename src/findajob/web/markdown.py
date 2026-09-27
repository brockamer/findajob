"""Server-side Markdown → HTML rendering shared by the materials and docs viewers.

Everything rendered here is untrusted: scraped job titles and descriptions,
email bodies, LLM-written materials and briefings, and onboarding chat
replies. Templates emit the result with ``| safe``, so the final step of
every renderer is an allowlist sanitizer (nh3). Python-Markdown passes raw
HTML through, so the sanitizer is the only thing standing between that input
and script running in the operator's authenticated origin. Keep it last.
"""

from __future__ import annotations

import html as _html_stdlib
import re

import markdown as md_lib
import nh3

from findajob.onboarding.parser import BLOCK_RE as _FILE_BLOCK_RE

_CENTERED_BLOCK_RE = re.compile(r":::centered\n([\s\S]*?)\n:::")
_LANG_CLASS_RE = re.compile(r'(<code[^>]*?) class="language-[^"]*"')
_ANCHOR_OPEN_RE = re.compile(r"<a\s+([^>]*?)>", re.IGNORECASE)
_HREF_ATTR_RE = re.compile(r'href="([^"]+)"', re.IGNORECASE)
_IMG_OPEN_RE = re.compile(r"<img\s+([^>]*?)>", re.IGNORECASE)
_SRC_ATTR_RE = re.compile(r'src="([^"]+)"', re.IGNORECASE)
_EXTERNAL_SCHEMES = ("http://", "https://", "mailto:")
_NON_RELATIVE_SRC_PREFIXES = ("http://", "https://", "data:", "//", "/", "#")

# Class tokens on the chat "captured file" badge. The badge is built here and
# then passes through the sanitizer, so the allowlist below is derived from
# this one tuple and the two cannot drift.
_BADGE_CLASSES = (
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
)


def _build_cleaner(*, heading_ids: bool) -> nh3.Cleaner:
    """Allowlist sanitizer for rendered Markdown.

    Tags: nh3's default set, which already excludes script, style, iframe,
    svg, math, object, embed, form, input, base, meta and link. Attributes:
    nh3's defaults (no event handlers, no ``style``, no ``class``) plus the few
    that findajob's own output needs. URLs: http, https, mailto and relative
    only, so ``javascript:`` and ``data:`` are dropped wherever they appear.
    Every link gets ``rel="noopener noreferrer"``, so input cannot pair
    ``target="_blank"`` with an opener.
    """
    attributes = {tag: set(attrs) for tag, attrs in nh3.ALLOWED_ATTRIBUTES.items()}
    # Python-Markdown's `tables` extension writes column alignment as inline
    # style; `filter_style_properties` keeps text-align and nothing else.
    attributes.setdefault("th", set()).add("style")
    attributes.setdefault("td", set()).add("style")
    attributes.setdefault("span", set()).add("title")
    if heading_ids:
        # Docs pages link to `#section` anchors that the `toc` extension
        # writes as heading ids. Content pages get no ids at all: an id from
        # a scraped page could shadow one the page's own scripts look up.
        for level in range(1, 7):
            attributes.setdefault(f"h{level}", set()).add("id")
    return nh3.Cleaner(
        attributes=attributes,
        url_schemes={"http", "https", "mailto"},
        link_rel="noopener noreferrer",
        tag_attribute_values={"a": {"target": {"_blank"}}},
        allowed_classes={"div": {"text-center"}, "span": set(_BADGE_CLASSES)},
        filter_style_properties={"text-align"},
    )


_CONTENT_CLEANER = _build_cleaner(heading_ids=False)
_DOCS_CLEANER = _build_cleaner(heading_ids=True)


def render_markdown(text: str, *, source: str = "") -> str:
    """Render Markdown to HTML with findajob-specific post-processing.

    Post-processing steps applied after Python-Markdown runs:
    - `:::centered` fenced blocks → centered divs (pre-parse).
    - Language class attributes stripped from fenced code (``` blocks).
    - The result is sanitized last (see the module docstring): anything
      outside the allowlist, including every event handler and every
      `javascript:` / `data:` URL, is removed.
    - External links (http/https/mailto) get `target="_blank" rel="noopener noreferrer"`.
    - `.md` links are rewritten to `/docs/<slug>` when `source` is a
      docs-relative path (e.g., "setup/README.md"); when `source` is empty
      (the materials use case), `.md` links are left untouched.
    - Relative `<img src>` is rewritten to `/docs/<docs-relative-path>` under
      the same `source` gate, so embedded screenshots resolve through the
      viewer instead of against the page URL (#1053).
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
    html = _ANCHOR_OPEN_RE.sub(lambda m: _rewrite_anchor(m, source=source), html)
    if source:
        html = _IMG_OPEN_RE.sub(lambda m: _rewrite_img(m, source=source), html)
    return (_DOCS_CLEANER if source else _CONTENT_CLEANER).clean(html)


def _rewrite_anchor(match: re.Match[str], *, source: str) -> str:
    attrs = match.group(1)
    href_match = _HREF_ATTR_RE.search(attrs)
    if not href_match:
        return match.group(0)
    href = href_match.group(1)
    new_href, is_external = _transform_href(href, source=source)
    new_attrs = attrs[: href_match.start()] + f'href="{new_href}"' + attrs[href_match.end() :]
    if is_external and "target=" not in new_attrs.lower():
        # The sanitizer adds rel="noopener noreferrer" to every link.
        new_attrs = new_attrs.rstrip() + ' target="_blank"'
    return f"<a {new_attrs}>"


def _rewrite_img(match: re.Match[str], *, source: str) -> str:
    attrs = match.group(1)
    src_match = _SRC_ATTR_RE.search(attrs)
    if not src_match:
        return match.group(0)
    src = src_match.group(1)
    if src.lower().startswith(_NON_RELATIVE_SRC_PREFIXES):
        return match.group(0)
    resolved = _resolve_against_source(src, source=source)
    if not resolved:
        return match.group(0)
    new_attrs = attrs[: src_match.start()] + f'src="/docs/{resolved}"' + attrs[src_match.end() :]
    return f"<img {new_attrs}>"


def _resolve_against_source(path_part: str, *, source: str) -> str:
    """Normalize a doc-relative path against `source`'s directory.

    `source` is the docs-relative path of the *file* being rendered, which is
    not always the slug's path ("getting-started" renders
    "getting-started/README.md"), so relative links must resolve against the
    file's directory.
    """
    source_dir = "/".join(source.split("/")[:-1])
    combined = f"{source_dir}/{path_part}" if source_dir else path_part
    parts: list[str] = []
    for seg in combined.split("/"):
        if seg == "..":
            if parts:
                parts.pop()
        elif seg and seg != ".":
            parts.append(seg)
    return "/".join(parts)


def _transform_href(href: str, *, source: str) -> tuple[str, bool]:
    if href.lower().startswith(_EXTERNAL_SCHEMES):
        return href, True
    if href.startswith("#") or not source:
        return href, False
    path_part, fragment = (href.split("#", 1) + [""])[:2]
    if not path_part.endswith(".md"):
        return href, False
    slug = _resolve_against_source(path_part, source=source)[: -len(".md")]
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

    The output passes through the same allowlist sanitizer as
    :func:`render_markdown`; the client inserts it as HTML (see
    ``static/onboarding-stream.js``), so this is the only place it is made
    safe. The docs-rewriting and external-link-rewriting paths are skipped —
    they don't apply to chat.
    """

    def _badge(match: re.Match[str]) -> str:
        name = match.group("name").strip()
        safe_name = _html_stdlib.escape(name)
        return (
            f'<span class="{" ".join(_BADGE_CLASSES)}" title="Captured for the parser">'
            f"\U0001f4c4 Captured: {safe_name}</span>"
        )

    text = _FILE_BLOCK_RE.sub(_badge, text)
    html = md_lib.markdown(text, extensions=["fenced_code", "tables", "md_in_html"], output_format="html")
    return _CONTENT_CLEANER.clean(html)
