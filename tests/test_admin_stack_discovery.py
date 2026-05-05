"""Tests for findajob.admin.stack_discovery.discover_stacks."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from findajob.admin.stack_discovery import StackPath, discover_stacks


def _make_stack(root: Path, handle: str, *, with_state: bool = True) -> Path:
    stack = root / f"findajob-{handle}"
    if with_state:
        (stack / "state" / "data").mkdir(parents=True)
        (stack / "state" / "logs").mkdir(parents=True)
    else:
        stack.mkdir()
    return stack


def test_empty_root_returns_empty(tmp_path: Path) -> None:
    assert discover_stacks(tmp_path) == []


def test_missing_root_returns_empty(tmp_path: Path) -> None:
    assert discover_stacks(tmp_path / "nope") == []


def test_finds_findajob_dirs_only(tmp_path: Path) -> None:
    _make_stack(tmp_path, "alice")
    _make_stack(tmp_path, "dave")
    (tmp_path / "dozzle").mkdir()
    (tmp_path / "archivebox").mkdir()
    (tmp_path / "watchtower").mkdir()
    out = discover_stacks(tmp_path)
    assert [s.handle for s in out] == ["alice", "dave"]


def test_returns_sorted_by_handle(tmp_path: Path) -> None:
    for h in ("tango", "dave", "alice", "papa"):
        _make_stack(tmp_path, h)
    out = discover_stacks(tmp_path)
    assert [s.handle for s in out] == ["alice", "dave", "papa", "tango"]


def test_skips_findajob_dir_missing_state(tmp_path: Path) -> None:
    _make_stack(tmp_path, "alice")
    _make_stack(tmp_path, "broken", with_state=False)
    out = discover_stacks(tmp_path)
    assert [s.handle for s in out] == ["alice"]


def test_paths_resolve_to_state_subdirs(tmp_path: Path) -> None:
    _make_stack(tmp_path, "alice")
    out = discover_stacks(tmp_path)
    assert len(out) == 1
    s = out[0]
    assert s.root == tmp_path / "findajob-alice"
    assert s.db_path == tmp_path / "findajob-alice" / "state" / "data" / "pipeline.db"
    assert s.jsonl_path == tmp_path / "findajob-alice" / "state" / "logs" / "pipeline.jsonl"


@pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses chmod 0o000")
def test_unreadable_root_returns_empty(tmp_path: Path) -> None:
    """A stacks_root with restrictive perms (foreign-uid bind mount,
    chmod-stripped parent) yields [] rather than 500-ing the dashboard.
    """
    root = tmp_path / "stacks"
    root.mkdir()
    _make_stack(root, "alice")
    root.chmod(0o000)
    try:
        assert discover_stacks(root) == []
    finally:
        root.chmod(0o755)  # let pytest clean up


def test_stackpath_is_frozen_dataclass(tmp_path: Path) -> None:
    _make_stack(tmp_path, "alice")
    s = discover_stacks(tmp_path)[0]
    assert isinstance(s, StackPath)
    # Frozen dataclasses raise on mutation.
    import dataclasses

    assert dataclasses.is_dataclass(s)
