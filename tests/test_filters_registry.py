"""Tests for web/filters/registry.py — source enum and per-tab ColumnSpec lists."""

from __future__ import annotations

from findajob.web.filters import registry as reg


def test_jsearch_in_source_values() -> None:
    """'jsearch' must appear in _SOURCE_VALUES for the board filter dropdown. (#408 / closes #310)"""
    assert "jsearch" in reg._SOURCE_VALUES
