"""Per-column filter framework for the board tabs.

See docs/superpowers/specs/2026-04-25-board-filter-framework-design.md.
"""

from findajob.web.filters.spec import ColumnSpec, Kind, validate_specs

__all__ = ["ColumnSpec", "Kind", "validate_specs"]
