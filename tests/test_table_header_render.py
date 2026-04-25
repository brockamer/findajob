from __future__ import annotations

from findajob.web.filters import ColumnSpec, Kind, ParsedFilters


def test_table_header_renders_filter_row(tmp_path) -> None:
    from findajob.web.app import create_app

    # create_app() requires companies_root + db_path kwargs.
    # Matches the pattern from tests/test_active_filters_render.py.
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
        sort="relevance_score",
        desc=True,
    )

    class _StubReq:
        class url:
            path = "/board/dashboard"

    rendered = templates.get_template("_table_header.html").render(
        request=_StubReq,
        specs=specs,
        parsed=parsed,
        visible={"title", "relevance_score", "stage"},
        leading_cols=[("Status", ""), ("Reject", "")],
    )
    # Sort link includes column name and a flip-direction querystring.
    assert "?sort=relevance_score&desc=0" in rendered
    # Active filter shows ● indicator on title and relevance_score columns.
    assert rendered.count("●") >= 2
    # Inline TEXT input present and pre-filled.
    assert 'name="title"' in rendered
    assert 'value="director"' in rendered
    # Range inputs present for SCORE.
    assert 'name="relevance_score_min"' in rendered
    assert 'name="relevance_score_max"' in rendered
    # ENUM popover hidden input present.
    assert 'name="stage"' in rendered and "data-filter-input" in rendered
    # HTMX wiring present.
    assert 'hx-get="/board/dashboard/rows"' in rendered
    assert 'hx-push-url="true"' in rendered
