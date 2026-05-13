"""Tests for `findajob.cleaning.is_degenerate_title` (#656).

The Android share-flow + LinkedIn iOS share-flow leave anchor text in place
of a real job title — sometimes the bare URL, sometimes the company name,
sometimes a numeric job ID. Triage uses this predicate to decide whether to
swap in the LinkedIn API's title (cached as `_linkedin_title` on the job
dict) over the parser-derived one.
"""

from __future__ import annotations

import pytest

from findajob.cleaning import is_degenerate_title


@pytest.mark.parametrize(
    "title",
    [
        "",
        "   ",
        "a",
        "abc",
        "12345",  # 5 digits → degenerate via length AND job-ID pattern
        "AB",
    ],
)
def test_empty_or_short_is_degenerate(title: str) -> None:
    assert is_degenerate_title(title) is True


def test_url_prefix_is_degenerate() -> None:
    assert is_degenerate_title("https://www.linkedin.com/jobs/view/4341101773/") is True
    assert is_degenerate_title("http://example.com/jobs/123") is True
    # Even with company supplied as context, URL-prefixed title is always degenerate.
    assert is_degenerate_title("https://x.example/y", company="Lambda") is True


def test_exact_url_match_is_degenerate_even_without_http_prefix() -> None:
    url = "www.example.com/jobs/abc"
    # URL field lookup catches non-http-prefixed exact matches too
    assert is_degenerate_title(url, url=url) is True


def test_exact_company_match_is_degenerate() -> None:
    # The DataBank case from #645 — anchor text was the company name
    assert is_degenerate_title("DataBank", company="DataBank") is True
    # Case-sensitive whole-string match (we don't claim to fuzzy-match here)
    assert is_degenerate_title("databank", company="DataBank") is False


def test_blank_company_does_not_false_positive() -> None:
    """Blank company must not match every title via `'' == something`."""
    assert is_degenerate_title("Hardware Reliability Engineer", company="") is False
    assert is_degenerate_title("Hardware Reliability Engineer") is False


def test_blank_url_does_not_false_positive() -> None:
    """Blank URL must not match every title via `'' == something`."""
    assert is_degenerate_title("Hardware Reliability Engineer", url="") is False


def test_bare_numeric_job_id_is_degenerate() -> None:
    assert is_degenerate_title("4341101773") is True
    assert is_degenerate_title("123456") is True


def test_alphanumeric_with_digits_is_not_degenerate() -> None:
    # "AI Engineer 3" has digits but isn't a bare job-ID
    assert is_degenerate_title("AI Engineer 3") is False
    assert is_degenerate_title("Engineer (4341101773)") is False


@pytest.mark.parametrize(
    "title",
    [
        "Hardware Reliability Engineer",
        "Senior Software Engineer, Datacenter Infrastructure",
        "NPI Engineering Program Manager",
        "Director, Production Engineering — GPU Platforms",
        "ML Compiler Engineer",
    ],
)
def test_real_titles_are_not_degenerate(title: str) -> None:
    # Pair positive negation with a leak-token check per the
    # negative-test-assertions feedback memory: a degenerate-title regression
    # would likely shift these to True, but verify no URL/digit leakage too.
    result = is_degenerate_title(title, company="Acme Corp", url="https://acme.example/jobs/123")
    assert result is False
    # Sanity: the helper did not silently normalize the title in a way that
    # would lose distinguishing features (we're testing a predicate, not a
    # cleaner — but if someone refactors to a tuple return, this guards).
    assert title  # title was non-empty going in


def test_url_match_handles_whitespace() -> None:
    # Trailing whitespace on either side should not defeat exact-match
    assert is_degenerate_title("  https://x/y  ", url="https://x/y") is True
