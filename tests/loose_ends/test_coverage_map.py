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


def test_handles_annotated_editable_categories_with_wildcard(tmp_path: Path) -> None:
    """Reproduces production shape: EDITABLE_CATEGORIES is type-annotated
    (ast.AnnAssign, not ast.Assign) and contains both list values AND
    string-wildcard values like 'config/roles/*.md'. Both must register.

    This test would have caught the original Task 2 bug where the plan-prescribed
    AST walker only handled ast.Assign and only handled list values, silently
    extracting zero paths from the real config_files.py. Per feedback memory
    `feedback_test_real_codepath_when_extracting`: test fixtures must shape-match
    production, not just the plan's prescribed code.
    """
    web = tmp_path / "src" / "findajob" / "web"
    web.mkdir(parents=True)
    (web / "config_files.py").write_text(
        "EDITABLE_CATEGORIES: dict[str, list[str] | str] = {\n"
        '    "Search config": ["config/feed_urls.txt"],\n'
        '    "Role prompts": "config/roles/*.md",\n'
        "}\n"
    )
    # Drop a concrete file under config/roles/ so the wildcard expansion
    # has something to match.
    roles_dir = tmp_path / "config" / "roles"
    roles_dir.mkdir(parents=True)
    (roles_dir / "data_processor.md").write_text("# role prompt body\n")

    result = walk_coverage_map(repo_root=tmp_path)

    # The list-value entry must register.
    assert "config/feed_urls.txt" in result
    # The wildcard-value entry must glob-expand and register the concrete file.
    assert "config/roles/data_processor.md" in result
    # Both must show EDITABLE_CATEGORIES as the source.
    assert any(r.source == "EDITABLE_CATEGORIES" for r in result["config/feed_urls.txt"])
    assert any(r.source == "EDITABLE_CATEGORIES" for r in result["config/roles/data_processor.md"])
