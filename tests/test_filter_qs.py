"""Tests for filter_qs_with() (findajob.web.helpers).

The helper re-encodes a querystring with one key set to a new value,
preserving all other params. Used by the density toggle to keep active
filters/sort across page loads.

Behavioral note: the function ALWAYS appends (key, value), so passing an
empty value yields ``key=`` (an empty param) rather than removing the
key. Pre-existing blank params in the input are dropped, because the
parse uses ``keep_blank_values=False``. These tests pin the real
behavior, not the (slightly off) phrasing in issue #885.
"""

from __future__ import annotations

import pytest

from findajob.web.helpers import filter_qs_with


@pytest.mark.parametrize(
    "existing,key,value,expected",
    [
        # Preserve unrelated params while updating the target key.
        ("a=1&b=2", "b", "3", "a=1&b=3"),
        # Updated key is re-appended at the end; survivors keep order.
        ("sort=name&desc=1", "sort", "date", "desc=1&sort=date"),
        # Adding a brand-new key to an existing querystring.
        ("a=1", "b", "2", "a=1&b=2"),
        # Empty querystring -> just the new pair.
        ("", "x", "1", "x=1"),
        # Empty value is appended as key= (NOT removed).
        ("a=1", "b", "", "a=1&b="),
        # Duplicate occurrences of the TARGET key collapse to one.
        ("a=1&a=2", "a", "9", "a=9"),
        # Duplicate occurrences of an UNRELATED key are preserved.
        ("a=1&a=2", "b", "3", "a=1&a=2&b=3"),
        # Pre-existing blank param is dropped (keep_blank_values=False).
        ("a=1&b=", "c", "2", "a=1&c=2"),
    ],
)
def test_filter_qs_with(existing: str, key: str, value: str, expected: str) -> None:
    assert filter_qs_with(existing, key, value) == expected


def test_special_characters_are_url_encoded() -> None:
    # Space -> '+', '&' -> '%26' (urlencode uses quote_plus by default).
    assert filter_qs_with("", "q", "a b&c") == "q=a+b%26c"


def test_encoded_input_survivors_are_preserved() -> None:
    # An incoming encoded value round-trips through parse + re-encode.
    result = filter_qs_with("q=a+b%26c", "page", "2")
    assert result == "q=a+b%26c&page=2"
