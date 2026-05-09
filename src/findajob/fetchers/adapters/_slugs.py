"""Shared `feed_urls.txt` slug parser (#410.2).

Used by `AshbyAdapter` and `LeverAdapter` (and `fetch_lever_jobs` until
#410.3 migrates Lever). Greenhouse has its own slug-parser inlined as a
private adapter method because its URL convention is simpler — this
helper exists for the Ashby/Lever shape with optional inline
`# Display Name` comments.

Underscore-prefix module name matches the `_keys.py` / `_locations.py`
convention: implementation detail of the adapters package, not part of
its public API.
"""

from __future__ import annotations

import re


def _parse_feed_slugs(feed_urls_path: str, slug_regex: re.Pattern) -> list[tuple[str, str]]:
    """Extract (slug, display_name) tuples from feed_urls.txt for a URL pattern.

    Inline comments like `https://jobs.lever.co/zoox  # Zoox` are recognized
    as display-name overrides. Without a comment, the display name defaults
    to the slug titlecased (best-effort — multi-word slugs still won't split).

    De-duplicates by slug; first occurrence wins.

    Args:
        feed_urls_path: path to feed_urls.txt
        slug_regex: compiled regex with one capture group for the slug
    Returns:
        list of (slug, display_name) tuples
    """
    try:
        with open(feed_urls_path) as f:
            lines = [line.rstrip("\n") for line in f]
    except FileNotFoundError:
        return []

    results: list[tuple[str, str]] = []
    seen: set[str] = set()
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "#" in line:
            url_part, _, comment = line.partition("#")
            url_part = url_part.strip()
            display = comment.strip() or None
        else:
            url_part = line
            display = None
        m = slug_regex.search(url_part)
        if not m:
            continue
        slug = m.group(1)
        if slug in seen:
            continue
        seen.add(slug)
        results.append((slug, display or slug.title()))
    return results
