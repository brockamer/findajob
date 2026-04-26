from pathlib import Path

import pytest

from findajob.discoverer.parser import (
    CompanyEntry,
    DiscoveryParseError,
    parse_markdown,
)

FIXTURES = Path(__file__).parent.parent / "fixtures" / "discoverer"


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_valid_three_clusters_parses_to_four_entries() -> None:
    result = parse_markdown(_read("valid_three_clusters.md"))
    assert len(result.companies) == 4
    by_cluster = {c.name: c.cluster for c in result.companies}
    assert by_cluster["Alpha Co"] == "direct"
    assert by_cluster["Beta Inc"] == "direct"
    assert by_cluster["Gamma LLC"] == "adjacency"
    assert by_cluster["Delta Org"] == "cross_industry"


def test_valid_three_clusters_resolves_per_row_citations() -> None:
    result = parse_markdown(_read("valid_three_clusters.md"))
    alpha = next(c for c in result.companies if c.name == "Alpha Co")
    assert alpha.citations == (
        "https://alpha.example.com/careers",
        "https://alpha.example.com/news",
    )
    delta = next(c for c in result.companies if c.name == "Delta Org")
    assert delta.citations == (
        "https://delta.example.org/work",
        "https://delta.example.org/team",
    )


def test_valid_three_clusters_extracts_channels() -> None:
    result = parse_markdown(_read("valid_three_clusters.md"))
    by_channel = {c.name: c.channel for c in result.companies}
    assert by_channel["Alpha Co"] == "greenhouse"
    assert by_channel["Beta Inc"] == "ashby"
    assert by_channel["Gamma LLC"] == "lever"
    assert by_channel["Delta Org"] == "in_house"


def test_valid_two_clusters_passes_minimum_gates() -> None:
    result = parse_markdown(_read("valid_two_clusters.md"))
    assert len(result.companies) == 3
    clusters = {c.cluster for c in result.companies}
    assert clusters == {"direct", "adjacency"}


def test_valid_unknown_channel_is_accepted() -> None:
    result = parse_markdown(_read("valid_unknown_channel.md"))
    alpha = next(c for c in result.companies if c.name == "Alpha Co")
    assert alpha.channel == "unknown"


def test_valid_with_extra_whitespace_in_references_resolves_correctly() -> None:
    result = parse_markdown(_read("valid_with_extra_whitespace.md"))
    by_name = {c.name: c.citations for c in result.companies}
    assert by_name["Alpha Co"] == ("https://alpha.example.com",)
    assert by_name["Beta Inc"] == ("https://beta.example.com",)
    assert by_name["Gamma LLC"] == ("https://gamma.example.com",)


def test_invalid_one_cluster_raises_with_clear_message() -> None:
    with pytest.raises(DiscoveryParseError) as excinfo:
        parse_markdown(_read("invalid_one_cluster.md"))
    assert "at least 2 clusters" in str(excinfo.value).lower()


def test_invalid_two_companies_raises_with_clear_message() -> None:
    with pytest.raises(DiscoveryParseError) as excinfo:
        parse_markdown(_read("invalid_two_companies.md"))
    assert "at least 3 companies" in str(excinfo.value).lower()


def test_invalid_missing_channel_raises_with_clear_message() -> None:
    with pytest.raises(DiscoveryParseError) as excinfo:
        parse_markdown(_read("invalid_missing_channel.md"))
    msg = str(excinfo.value).lower()
    assert "channel" in msg and "alpha co" in msg


def test_company_entry_is_frozen() -> None:
    entry = CompanyEntry(
        name="X",
        cluster="direct",
        channel="greenhouse",
        reasoning="r",
        citations=("u",),
    )
    with pytest.raises((AttributeError, Exception)):
        entry.name = "Y"  # type: ignore[misc]


def test_parse_markdown_returns_clean_markdown() -> None:
    md = _read("valid_three_clusters.md")
    result = parse_markdown(md)
    # Clean markdown is the input minus any think-block residue.
    # For valid input, it equals the input (modulo strip).
    assert result.markdown_clean.strip() == md.strip()


def test_valid_no_citations_rows_parse_with_empty_citations() -> None:
    """Citations clause is OPTIONAL per row (over-strict citation
    requirement caused real-API smokes to refuse valid recommendations
    when the model couldn't confirm a URL). Rows without `Citations: [N]`
    must parse successfully with `citations=()`."""
    result = parse_markdown(_read("valid_no_citations.md"))
    assert len(result.companies) == 3
    by_name = {c.name: c for c in result.companies}
    # Two rows have no Citations clause
    assert by_name["Alpha Co"].citations == ()
    assert by_name["Beta Inc"].citations == ()
    # One row keeps its citation
    assert by_name["Gamma LLC"].citations == ("https://gamma.example.com/about",)
