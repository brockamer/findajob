from __future__ import annotations

from findajob.web.filters import ColumnSpec, Kind, ParsedFilters


def test_active_filters_renders_chip_strip(tmp_path) -> None:
    from findajob.web.app import create_app

    app = create_app(
        companies_root=tmp_path / "companies",
        db_path=tmp_path / "pipeline.db",
    )
    templates = app.state.templates

    specs = (
        ColumnSpec(name="title", label="Title", kind=Kind.TEXT),
        ColumnSpec(name="relevance_score", label="Rel", kind=Kind.SCORE),
        ColumnSpec(
            name="stage",
            label="Stage",
            kind=Kind.ENUM,
            enum_values=("scored", "applied"),
        ),
    )
    parsed = ParsedFilters(
        text={"title": "director"},
        numeric_range={"relevance_score": (5, 10)},
        enum={"stage": ("scored",)},
    )

    class _StubReq:
        class url:
            path = "/board/dashboard"

    rendered = templates.get_template("_active_filters.html").render(
        request=_StubReq,
        parsed=parsed,
        specs=specs,
    )
    assert "Title:" in rendered
    assert "director" in rendered
    assert "Rel:" in rendered
    assert "5–10" in rendered
    assert "Stage:" in rendered
    assert "scored" in rendered
    assert "Clear all" in rendered
    assert "Copy link" in rendered
