"""Pandoc wrapper for rendering prep markdown into the reference-doc-themed .docx.

Extracted from `findajob.prep.orchestrator` in #210. The same render pipeline
drives both the prep flow (briefing, resume, cover) and the in-browser
edit-and-save flow under `/materials/{fp}/files/{name}` — keeping the pandoc
invocation in one place avoids drift between the two callers.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from findajob.paths import BASE, PANDOC


def render_md_to_docx(
    md_path: str | Path,
    docx_path: str | Path,
    *,
    has_yaml_frontmatter: bool = False,
) -> None:
    """Render a markdown file to .docx using the prep reference theme.

    Args:
        md_path: Source markdown file path.
        docx_path: Destination .docx; overwritten if present.
        has_yaml_frontmatter: When True, pass ``-f markdown-yaml_metadata_block``
            so pandoc parses YAML frontmatter and omits it from the body
            (used for briefings).

    Raises:
        subprocess.CalledProcessError: pandoc returned non-zero.
    """
    cmd: list[str] = [PANDOC]
    if has_yaml_frontmatter:
        cmd += ["-f", "markdown-yaml_metadata_block"]
    cmd += [
        str(md_path),
        "--lua-filter",
        f"{BASE}/config/strip-bookmarks.lua",
        "--reference-doc",
        f"{BASE}/config/reference.docx",
        "-o",
        str(docx_path),
    ]
    subprocess.run(
        cmd,
        check=True,
        capture_output=True,
    )
