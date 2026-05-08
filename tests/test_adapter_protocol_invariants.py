"""Bidirectional invariants over the JobSourceAdapter contract:

- #512 — every entry in REGISTERED_ADAPTERS satisfies the Protocol
  (runtime_checkable isinstance + class-attr shape check).
- #516 — every concrete adapter class discovered in the
  findajob.fetchers.adapters package is in REGISTERED_ADAPTERS.

Together these catch both directions of contract drift: a registered
class that doesn't actually meet the Protocol, and an adapter class
that meets the Protocol but was never registered.

Discovery for #516 walks the package via pkgutil rather than
`JobSourceAdapter.__subclasses__()`. The Protocol uses
`runtime_checkable` and is not a class adapters inherit from
(adapters are duck-typed) — `__subclasses__()` would be empty.
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil
from typing import Any

import pytest

import findajob.fetchers.adapters as adapters_pkg
from findajob.fetchers.adapters.base import JobSourceAdapter
from findajob.fetchers.adapters.registry import REGISTERED_ADAPTERS

_PROTOCOL_ATTRS = ("name", "display_name", "source_label", "required_env_vars")
_PROTOCOL_METHODS = ("is_configured", "fetch", "live_test")

# Modules in the adapters package that aren't adapter implementations.
_NON_ADAPTER_MODULES = {
    "findajob.fetchers.adapters.base",
    "findajob.fetchers.adapters.registry",
    "findajob.fetchers.adapters.curation",
    "findajob.fetchers.adapters._keys",
    "findajob.fetchers.adapters._locations",
}


def _looks_like_adapter(cls: type[Any]) -> bool:
    """Structural check: class has every Protocol attr and method.

    Avoids isinstance() because that requires instantiation, and
    instantiation may pull in env-dependent state for some adapters."""
    return all(hasattr(cls, a) for a in _PROTOCOL_ATTRS) and all(
        callable(getattr(cls, m, None)) for m in _PROTOCOL_METHODS
    )


def _discover_adapter_classes() -> list[type[Any]]:
    """Walk every module in findajob.fetchers.adapters and return classes
    defined there that match the adapter shape."""
    found: list[type[Any]] = []
    for _finder, modname, _ispkg in pkgutil.iter_modules(adapters_pkg.__path__, prefix=adapters_pkg.__name__ + "."):
        if modname in _NON_ADAPTER_MODULES:
            continue
        mod = importlib.import_module(modname)
        for _name, cls in inspect.getmembers(mod, inspect.isclass):
            # Only classes defined IN this module, not imported into it.
            if cls.__module__ != mod.__name__:
                continue
            if _looks_like_adapter(cls):
                found.append(cls)
    return found


# ── #512: registry → Protocol direction ──────────────────────────────────


@pytest.mark.parametrize("adapter_cls", REGISTERED_ADAPTERS, ids=lambda c: c.__name__)
class TestRegisteredAdapterConformance:
    def test_instantiates_without_args(self, adapter_cls: type[Any]) -> None:
        """Every registered adapter constructs with no args. iter_configured_adapters
        and the live-test flow both rely on this."""
        adapter_cls()

    def test_satisfies_runtime_protocol(self, adapter_cls: type[Any]) -> None:
        """isinstance against the runtime_checkable Protocol — all required
        attrs and methods present at instance level."""
        instance = adapter_cls()
        assert isinstance(instance, JobSourceAdapter)

    def test_required_class_attrs_are_populated(self, adapter_cls: type[Any]) -> None:
        """The four declared class attrs must be present, correctly typed, and
        non-empty. Empty strings or wrong types break source_label-keyed DB
        rows and active_sources.txt name lookups."""
        assert isinstance(adapter_cls.name, str) and adapter_cls.name
        assert isinstance(adapter_cls.display_name, str) and adapter_cls.display_name
        assert isinstance(adapter_cls.source_label, str) and adapter_cls.source_label
        assert isinstance(adapter_cls.required_env_vars, tuple)
        assert all(isinstance(v, str) and v for v in adapter_cls.required_env_vars)


def test_registered_adapter_names_are_unique() -> None:
    """The `name` attr is the active_sources.txt lookup key — duplicates would
    let one adapter shadow another silently."""
    names = [cls.name for cls in REGISTERED_ADAPTERS]
    assert len(names) == len(set(names)), f"duplicate adapter names: {names}"


# ── #516: discovery → registry direction ─────────────────────────────────


def test_every_discovered_adapter_is_registered() -> None:
    """Catches the 'wrote it, forgot to register' failure mode: an adapter
    class that satisfies the Protocol exists in the package but was never
    added to REGISTERED_ADAPTERS, so triage never calls it."""
    discovered = set(_discover_adapter_classes())
    registered = set(REGISTERED_ADAPTERS)
    orphans = discovered - registered
    assert not orphans, (
        f"Adapter classes discovered in findajob.fetchers.adapters but "
        f"missing from REGISTERED_ADAPTERS: {sorted(c.__name__ for c in orphans)}"
    )


def test_every_registered_adapter_is_discoverable() -> None:
    """Inverse direction: a registered class that the discovery walk can't find
    means either it was moved out of the package or imported from elsewhere.
    Either way, the registry entry would resolve to a class no one expects."""
    discovered = set(_discover_adapter_classes())
    registered = set(REGISTERED_ADAPTERS)
    missing = registered - discovered
    assert not missing, (
        f"Adapter classes in REGISTERED_ADAPTERS but not found by package walk: {sorted(c.__name__ for c in missing)}"
    )
