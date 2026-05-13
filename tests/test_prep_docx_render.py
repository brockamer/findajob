"""Tests for findajob.prep.docx_render — pandoc wrapper helper (#210)."""

from __future__ import annotations

import subprocess
from unittest.mock import patch

import pytest

from findajob.paths import BASE, PANDOC
from findajob.prep.docx_render import render_md_to_docx


def test_default_invocation_passes_lua_filter_and_reference_doc(tmp_path):
    md = tmp_path / "in.md"
    md.write_text("# Hello\n")
    docx = tmp_path / "out.docx"

    with patch("findajob.prep.docx_render.subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0)
        render_md_to_docx(md, docx)

    cmd = mock_run.call_args.args[0]
    kwargs = mock_run.call_args.kwargs
    assert cmd[0] == PANDOC
    assert str(md) in cmd
    assert str(docx) in cmd
    assert "--lua-filter" in cmd
    assert f"{BASE}/config/strip-bookmarks.lua" in cmd
    assert "--reference-doc" in cmd
    assert f"{BASE}/config/reference.docx" in cmd
    assert cmd[-2:] == ["-o", str(docx)]
    assert "-f" not in cmd
    assert kwargs.get("check") is True
    assert kwargs.get("capture_output") is True


def test_yaml_frontmatter_mode_prepends_format_flag(tmp_path):
    md = tmp_path / "briefing.md"
    md.write_text("---\ntitle: x\n---\n\n# Body\n")
    docx = tmp_path / "briefing.docx"

    with patch("findajob.prep.docx_render.subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0)
        render_md_to_docx(md, docx, has_yaml_frontmatter=True)

    cmd = mock_run.call_args.args[0]
    f_idx = cmd.index("-f")
    assert cmd[f_idx + 1] == "markdown-yaml_metadata_block"
    assert cmd.index(str(md)) > f_idx


def test_pandoc_failure_propagates(tmp_path):
    md = tmp_path / "in.md"
    md.write_text("# x")
    docx = tmp_path / "out.docx"

    with patch("findajob.prep.docx_render.subprocess.run") as mock_run:
        mock_run.side_effect = subprocess.CalledProcessError(returncode=1, cmd=[PANDOC], stderr=b"oops")
        with pytest.raises(subprocess.CalledProcessError) as exc_info:
            render_md_to_docx(md, docx)
        assert exc_info.value.returncode == 1


def test_accepts_str_or_path(tmp_path):
    md = tmp_path / "in.md"
    md.write_text("# x")
    docx_str = str(tmp_path / "out.docx")

    with patch("findajob.prep.docx_render.subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0)
        render_md_to_docx(str(md), docx_str)
        render_md_to_docx(md, tmp_path / "also.docx")

    for call in mock_run.call_args_list:
        cmd = call.args[0]
        assert all(isinstance(a, str) for a in cmd), f"pandoc args must all be str: {cmd}"
