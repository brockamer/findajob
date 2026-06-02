"""Tests for the bounded JSONL tail reader (findajob.jsonl_tail).

Exercises tail_events() against real temporary files (no I/O mocking):
newest-first decode, the max_bytes overflow path that drops a partial
first line, malformed-line skipping, the two OSError branches
(missing-file stat failure + directory open failure), and UTF-8
error='replace' tolerance.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from findajob.jsonl_tail import tail_events


def _write(path: Path, lines: list[str]) -> None:
    path.write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")


def test_yields_decoded_events_newest_first(tmp_path: Path) -> None:
    p = tmp_path / "events.jsonl"
    _write(p, ['{"i": 0}', '{"i": 1}', '{"i": 2}'])

    events = list(tail_events(p))

    assert events == [{"i": 2}, {"i": 1}, {"i": 0}]


def test_empty_file_yields_nothing(tmp_path: Path) -> None:
    p = tmp_path / "empty.jsonl"
    p.write_bytes(b"")

    assert list(tail_events(p)) == []


def test_overflow_drops_partial_first_line(tmp_path: Path) -> None:
    # First line is long padding; max_bytes is small enough that the tail
    # buffer starts mid-way through it. read_len < size, so the partial
    # first line must be discarded — only the two whole trailing lines
    # survive, newest-first.
    p = tmp_path / "big.jsonl"
    long_first = '{"i": 0, "pad": "' + ("a" * 500) + '"}'
    _write(p, [long_first, '{"i": 1}', '{"i": 2}'])

    events = list(tail_events(p, max_bytes=100))

    assert events == [{"i": 2}, {"i": 1}]
    # The padded first line was never emitted (it was the partial).
    assert all(e.get("i") != 0 for e in events)


def test_malformed_lines_are_skipped_with_warning(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    p = tmp_path / "mixed.jsonl"
    _write(p, ['{"ok": 1}', "this is not json", '{"ok": 2}'])

    with caplog.at_level(logging.WARNING, logger="findajob.jsonl_tail"):
        events = list(tail_events(p))

    assert events == [{"ok": 2}, {"ok": 1}]
    assert any("malformed line" in r.message for r in caplog.records)


def test_blank_lines_are_skipped(tmp_path: Path) -> None:
    p = tmp_path / "blanks.jsonl"
    p.write_text('{"a": 1}\n\n   \n{"a": 2}\n', encoding="utf-8")

    assert list(tail_events(p)) == [{"a": 2}, {"a": 1}]


def test_missing_file_yields_nothing(tmp_path: Path) -> None:
    # os.path.getsize raises FileNotFoundError (an OSError) -> empty.
    missing = tmp_path / "does_not_exist.jsonl"

    assert list(tail_events(missing)) == []


def test_unreadable_file_yields_nothing(tmp_path: Path) -> None:
    # A directory stats fine but open(..., "rb") raises IsADirectoryError
    # (an OSError) -> empty. Exercises the "cannot open" branch without
    # chmod games that break under root.
    a_dir = tmp_path / "a_directory"
    a_dir.mkdir()

    assert list(tail_events(a_dir)) == []


def test_invalid_utf8_decoded_with_replacement(tmp_path: Path) -> None:
    # Raw invalid UTF-8 bytes inside a JSON string value. errors="replace"
    # turns them into U+FFFD, leaving structurally valid JSON that decodes.
    p = tmp_path / "badbytes.jsonl"
    p.write_bytes(b'{"k": "\xff\xfe"}\n')

    events = list(tail_events(p))

    assert len(events) == 1
    assert events[0]["k"] == "��"
