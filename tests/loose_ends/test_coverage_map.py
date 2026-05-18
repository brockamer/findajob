"""Tests for findajob.loose_ends.coverage_map (#572)."""

from pathlib import Path

from findajob.loose_ends.coverage_map import SurfaceRef, walk_coverage_map


def test_reads_editable_categories_from_config_files_module(tmp_path: Path) -> None:
    """Paths in EDITABLE_CATEGORIES register as covered by the /config/ editor."""
    web = tmp_path / "src" / "findajob" / "web"
    web.mkdir(parents=True)
    (web / "config_files.py").write_text(
        "EDITABLE_CATEGORIES = {\n"
        '    "Search config": [\n'
        '        "config/feed_urls.txt",\n'
        '        "config/prefilter_rules.yaml",\n'
        "    ],\n"
        "}\n"
    )
    result = walk_coverage_map(repo_root=tmp_path)
    assert "config/feed_urls.txt" in result
    assert any(isinstance(r, SurfaceRef) and r.source == "EDITABLE_CATEGORIES" for r in result["config/feed_urls.txt"])


def test_detects_path_referenced_inside_route_module(tmp_path: Path) -> None:
    """A route module that string-literal-references config/foo.yaml covers it."""
    routes = tmp_path / "src" / "findajob" / "web" / "routes"
    routes.mkdir(parents=True)
    (routes / "settings_foo.py").write_text(
        "from fastapi import APIRouter\n"
        "router = APIRouter()\n"
        '@router.post("/settings/foo/save")\n'
        "def save():\n"
        '    return open("config/foo.yaml").read()\n'
    )
    result = walk_coverage_map(repo_root=tmp_path)
    assert "config/foo.yaml" in result
    assert any(r.source == "route:settings_foo" for r in result["config/foo.yaml"])
