#!/usr/bin/env python3
"""scrub_embedding_client.py — one-time / idempotent entrypoint helper.

Removes all RAG / embedding infrastructure from the aichat-ng config.yaml:

  1. Top-level ``rag_embedding_model:`` line
  2. The ``gemini-embed`` client block (+ its leading comment lines) from
     the ``clients:`` list
  3. The on-disk RAG index ``<config-dir>/rags/job_search_rag.yaml``

Runs from ``ops/entrypoint.sh`` **before** supercronic starts, as the
runtime ``findajob`` user.  Fail-open on every error — print one diagnostic
line to stderr (SKIPPED prefix) and exit 0.  This is in the boot path; a
crash here bricks the container, which is worse than skipping a scrub.

Atomic writes: edits are written to ``<file>.tmp``, then ``os.replace()``
to avoid leaving a half-written config on crash.

Stdlib only (pathlib, os, sys, shutil) — no PyYAML.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

PREFIX = "scrub_embedding_client"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _read_text(path: Path) -> list[str] | None:
    """Return file lines (with line endings) or None if unreadable."""
    try:
        return path.read_text(encoding="utf-8").splitlines(keepends=True)
    except UnicodeDecodeError:
        print(
            f"{PREFIX}: SKIPPED — {path} contains non-UTF-8 bytes, leaving unchanged",
            file=sys.stderr,
        )
        return None
    except OSError as exc:
        print(
            f"{PREFIX}: SKIPPED — could not read {path}: {exc}",
            file=sys.stderr,
        )
        return None


def _write_atomic(path: Path, lines: list[str]) -> None:
    """Atomically write *lines* to *path* via a .tmp sibling."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text("".join(lines), encoding="utf-8")
    os.replace(tmp, path)


def _looks_parseable(lines: list[str]) -> bool:
    """
    Heuristic sanity check: at least one top-level key at column 0.
    A file with no ``key: value`` lines at column 0 is considered unparseable.
    """
    for line in lines:
        stripped = line.rstrip("\n")
        if stripped and not stripped[0].isspace() and ":" in stripped:
            return True
    return False


# ---------------------------------------------------------------------------
# Operation 1: remove ``rag_embedding_model:`` top-level key
# ---------------------------------------------------------------------------


def remove_rag_embedding_model(lines: list[str]) -> tuple[list[str], bool]:
    """Return (new_lines, changed)."""
    out: list[str] = []
    changed = False
    for line in lines:
        if line.lstrip() == line and line.startswith("rag_embedding_model:"):
            changed = True
            continue  # drop this line
        out.append(line)
    return out, changed


# ---------------------------------------------------------------------------
# Operation 2: remove the ``gemini-embed`` client block + leading comments
# ---------------------------------------------------------------------------


def _find_client_block_bounds(lines: list[str], start: int) -> int:
    """Return the exclusive end index of the client block starting at *start*.

    A client block extends from its ``  - type:`` line through (but not
    including) the next ``  - type:`` line at the same indent level, or EOF.
    """
    # Start scanning from the line AFTER the opening ``  - type:`` line.
    for i in range(start + 1, len(lines)):
        stripped = lines[i]
        # Next client starts with exactly two spaces then "- type:"
        if stripped.startswith("  - type:") or stripped.startswith("  -\ttype:"):
            return i
        # A line at column 0 (new top-level key or EOF sentinel) also ends the block
        if stripped and not stripped[0].isspace() and stripped[0] != "#":
            return i
    return len(lines)


def remove_gemini_embed_client(lines: list[str]) -> tuple[list[str], bool]:
    """Return (new_lines, changed).

    Removes the ``gemini-embed`` client block *and* any contiguous leading
    comment lines (``  #``) immediately above it.  All other clients survive
    untouched.
    """
    # Find all client-block start positions (``  - type:`` at col 0+2sp)
    client_starts: list[int] = []
    for i, line in enumerate(lines):
        if line.startswith("  - type:") or line.startswith("  -\ttype:"):
            client_starts.append(i)

    # For each block, determine its extent and check whether it's gemini-embed
    target_start: int | None = None
    target_end: int | None = None

    for cs in client_starts:
        block_end = _find_client_block_bounds(lines, cs)
        block_lines = lines[cs:block_end]
        # Check for ``  name: gemini-embed`` scoped within this block only
        is_gemini_embed = any(ln.strip() == "name: gemini-embed" for ln in block_lines)
        if is_gemini_embed:
            target_start = cs
            target_end = block_end
            break

    if target_start is None:
        return lines, False  # gemini-embed block not found

    # Walk backwards from target_start to collect contiguous leading comment lines
    # (lines starting with ``  #`` immediately above the ``  - type:`` line).
    comment_start = target_start
    i = target_start - 1
    while i >= 0:
        line = lines[i]
        # Accept lines that are:
        #   - pure whitespace (blank lines between comment and block), OR
        #   - indented comments starting with ``  #``
        # Stop at anything else (previous client's content, top-level keys, etc.)
        if line.strip() == "":
            # A blank line stops the backward walk — don't eat it.
            break
        if line.startswith("  #"):
            comment_start = i
            i -= 1
        else:
            break

    # Build output, skipping lines [comment_start, target_end)
    out = lines[:comment_start] + lines[target_end:]

    # Trim any trailing blank lines that were left between the previous client
    # and whatever follows, keeping exactly one blank line if the previous
    # client block had a blank separator.  We only strip truly empty lines
    # at the join point (comment_start - 1) so we don't disturb other spacing.
    # NOTE: we intentionally do NOT collapse extra blank lines here — the
    # caller only wants the gemini-embed block gone; surrounding whitespace
    # is the operator's to manage.

    return out, True


# ---------------------------------------------------------------------------
# Operation 3: remove the RAG index yaml
# ---------------------------------------------------------------------------


def remove_rag_index(config_dir: Path) -> bool:
    """Delete ``<config_dir>/rags/job_search_rag.yaml``.  Return True if deleted."""
    rag_path = config_dir / "rags" / "job_search_rag.yaml"
    if not rag_path.exists():
        return False
    try:
        rag_path.unlink()
        return True
    except OSError as exc:
        print(
            f"{PREFIX}: SKIPPED — could not remove {rag_path}: {exc}",
            file=sys.stderr,
        )
        return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def scrub(config_dir: Path) -> int:
    """Run all three scrub operations.  Always returns 0 (fail-open)."""
    config_path = config_dir / "config.yaml"

    did_something = False

    # --- config.yaml operations ---
    if not config_path.exists():
        print(
            f"{PREFIX}: SKIPPED — {config_path} not found",
            file=sys.stderr,
        )
    else:
        lines = _read_text(config_path)
        if lines is None:
            # _read_text already printed the SKIPPED message
            pass
        elif not _looks_parseable(lines):
            print(
                f"{PREFIX}: SKIPPED — {config_path} unparseable, leaving unchanged",
                file=sys.stderr,
            )
        else:
            modified = False

            # Op 1: remove rag_embedding_model
            lines, changed1 = remove_rag_embedding_model(lines)
            if changed1:
                modified = True
                did_something = True
                print(f"{PREFIX}: removed rag_embedding_model setting from {config_path}", file=sys.stderr)

            # Op 2: remove gemini-embed client block
            lines, changed2 = remove_gemini_embed_client(lines)
            if changed2:
                modified = True
                did_something = True
                print(f"{PREFIX}: removed gemini-embed client from {config_path}", file=sys.stderr)

            if modified:
                _write_atomic(config_path, lines)

    # --- Op 3: remove RAG index ---
    rag_path = config_dir / "rags" / "job_search_rag.yaml"
    if remove_rag_index(config_dir):
        did_something = True
        print(f"{PREFIX}: removed rag index {rag_path}", file=sys.stderr)

    if not did_something:
        print(f"{PREFIX}: no-op (nothing to remove)", file=sys.stderr)

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Scrub embedding/RAG infrastructure from aichat-ng config.")
    parser.add_argument(
        "--config-dir",
        default=os.path.join(os.environ.get("HOME", "/root"), ".config", "aichat_ng"),
        help="Path to aichat-ng config dir (default: $HOME/.config/aichat_ng)",
    )
    args = parser.parse_args()
    config_dir = Path(args.config_dir)
    return scrub(config_dir)


if __name__ == "__main__":
    sys.exit(main())
