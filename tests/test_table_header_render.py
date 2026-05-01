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
    # Sort link is anchored to the canonical page path so a post-filter
    # /rows URL bar (#340) doesn't poison relative resolution.
    assert "/board/dashboard?sort=relevance_score&desc=0" in rendered
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


def test_enum_hidden_input_has_htmx_change_trigger(tmp_path) -> None:
    """ENUM popover Apply commits to a hidden input via htmx.trigger(_, 'change');
    that hidden input must carry hx-trigger='change' or the request never fires.
    Regression for the ENUM filter no-op bug."""
    from findajob.web.app import create_app

    app = create_app(
        companies_root=tmp_path / "companies",
        db_path=tmp_path / "pipeline.db",
    )
    templates = app.state.templates
    specs = (
        ColumnSpec(
            name="stage",
            label="Stage",
            kind=Kind.ENUM,
            enum_values=("applied", "interview", "offer"),
        ),
    )
    parsed = ParsedFilters()

    class _StubReq:
        class url:
            path = "/board/applied"

    rendered = templates.get_template("_table_header.html").render(
        request=_StubReq, specs=specs, parsed=parsed, visible={"stage"}, leading_cols=[]
    )
    # Find the hidden input for stage and assert it carries htmx attrs.
    hidden_marker = 'data-popover-target="stage"'
    assert hidden_marker in rendered
    # Slice the input element and check inside it.
    start = rendered.index(hidden_marker)
    chunk = rendered[max(0, start - 400) : start + 400]
    assert 'hx-trigger="change"' in chunk
    assert 'hx-get="/board/applied/rows"' in chunk


def test_sort_link_uses_canonical_page_path_when_on_rows_endpoint(tmp_path) -> None:
    """#340 — after a filter input fires hx-push-url='true', the address bar
    sits on /board/dashboard/rows. A subsequent sort click on a relative
    href would resolve to the rows endpoint and return raw <tr> rows that
    hx-boost dumps into <body> ('all rows expand'). Sort links must use the
    canonical page path so resolution works regardless of the URL bar."""
    from findajob.web.app import create_app

    app = create_app(
        companies_root=tmp_path / "companies",
        db_path=tmp_path / "pipeline.db",
    )
    templates = app.state.templates

    specs = (ColumnSpec(name="company", label="Company", kind=Kind.TEXT),)
    parsed = ParsedFilters()

    class _StubReq:
        class url:
            path = "/board/dashboard/rows"

    rendered = templates.get_template("_table_header.html").render(
        request=_StubReq, specs=specs, parsed=parsed, visible={"company"}, leading_cols=[]
    )
    # Sort anchor must point at /board/dashboard, not /board/dashboard/rows.
    assert 'href="/board/dashboard?sort=company' in rendered
    assert 'href="/board/dashboard/rows?sort=company' not in rendered
    # Filter inputs still target the rows fragment endpoint.
    assert 'hx-get="/board/dashboard/rows"' in rendered
