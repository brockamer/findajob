"""Bounded tail of pipeline.jsonl. Yields decoded events newest-first.

Reads at most `max_bytes` from the end of the file so a long-running
stack with a multi-megabyte log does not block the dashboard render.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Iterator
from pathlib import Path

logger = logging.getLogger(__name__)


def tail_events(path: Path, *, max_bytes: int = 1_048_576) -> Iterator[dict]:
    """Yield decoded JSON events from the last ~`max_bytes` of `path`,
    newest first.

    Returns an empty iterator when the file is missing, empty, or
    unreadable (e.g. a bind mount mid-remount, or a stack whose log
    file landed at a restrictive mode). Skips malformed lines with a
    single WARNING log per occurrence. When the tail buffer cuts
    mid-line, the partial first line is discarded so every yielded
    value is valid JSON.
    """
    # Catch OSError (parent of FileNotFoundError AND PermissionError) so
    # a foreign-uid / chmod-stripped log file degrades to "no events"
    # rather than 500-ing the dashboard. The bounded-tail design exists
    # exactly to keep this path defensive.
    try:
        size = os.path.getsize(path)
    except OSError:
        logger.warning("jsonl_tail: cannot stat %s", path)
        return
    if size == 0:
        return

    read_len = min(size, max_bytes)
    try:
        with open(path, "rb") as f:
            f.seek(size - read_len)
            chunk = f.read(read_len)
    except OSError:
        logger.warning("jsonl_tail: cannot open %s", path)
        return

    text = chunk.decode("utf-8", errors="replace")
    lines = text.splitlines()
    # If we sought past the start of the file, the first line is likely
    # a partial — drop it so we never emit half-decoded JSON.
    if read_len < size and lines:
        lines = lines[1:]

    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            logger.warning("jsonl_tail: malformed line in %s", path)
