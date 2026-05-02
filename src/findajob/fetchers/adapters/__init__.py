"""findajob.fetchers.adapters — pluggable JobSourceAdapter framework (#408)."""

from .base import JobSourceAdapter, LiveTestResult, QueryResult
from .registry import REGISTERED_ADAPTERS, iter_configured_adapters

__all__ = (
    "JobSourceAdapter",
    "LiveTestResult",
    "QueryResult",
    "REGISTERED_ADAPTERS",
    "iter_configured_adapters",
)
