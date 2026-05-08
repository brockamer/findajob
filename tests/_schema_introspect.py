"""Schema introspection helper for baseline/legacy schema tests.

Returns a deterministic, JSON-serializable representation of an open SQLite
connection's schema. Captures both:

- Structured PRAGMA output (``table_info`` + ``foreign_key_list``) — easy
  to diff column-by-column when a structural change lands.
- Normalized ``sqlite_master.sql`` text per object — covers ``CHECK``
  constraints and partial-index ``WHERE`` clauses that PRAGMA omits.

Used by ``tests/test_schema_baseline_fresh.py`` (#513) and
``tests/test_schema_baseline_legacy.py`` (#514).
"""

from __future__ import annotations

import re
import sqlite3
from typing import Any

_WHITESPACE_RE = re.compile(r"\s+")
_LINE_COMMENT_RE = re.compile(r"--[^\n]*")
_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_PUNCT_PADDING_RE = re.compile(r"\s*([(),;])\s*")


def _normalize_sql(sql: str | None) -> str | None:
    """Strip SQL comments + normalize whitespace; preserve everything structural.

    SQL comments aren't part of the schema contract — a fresh ``CREATE
    TABLE`` block with inline ``-- explanatory`` comments and a legacy
    table reshaped via ``ALTER TABLE ADD COLUMN`` (which strips comments
    and chooses its own whitespace style) must compare equal. We:

    - strip ``--`` line comments and ``/* */`` block comments
    - remove whitespace adjacent to ``(``, ``)``, ``,``, ``;`` so punctuation
      style differences don't trigger false-positive diffs
    - collapse remaining whitespace runs to a single space.

    Limitation: the punctuation-padding rule operates on raw text, so a
    string-literal default containing one of ``(),;`` (e.g.
    ``DEFAULT 'a, b'``) would have its internal whitespace mangled. The
    current schema has no such defaults; revisit this if a future column
    adds one. Detecting string literals up-front (``r"'(?:[^']|'')*'"``)
    is the principled fix.
    """
    if sql is None:
        return None
    sql = _BLOCK_COMMENT_RE.sub(" ", sql)
    sql = _LINE_COMMENT_RE.sub(" ", sql)
    sql = _PUNCT_PADDING_RE.sub(r"\1", sql)
    return _WHITESPACE_RE.sub(" ", sql).strip()


def introspect(conn: sqlite3.Connection) -> dict[str, Any]:
    """Return a deterministic dict describing the schema of ``conn``.

    The returned shape is ``{"objects": [...]}`` — sorted, JSON-serializable.
    SQLite version is intentionally **not** captured: it is metadata about
    the runtime, not the schema, and its inclusion in earlier drafts caused
    CI / local SQLite-version skew to false-positive the equality check.
    Any genuine change in how SQLite stores ``sqlite_master.sql`` between
    versions still surfaces — through the ``sql`` field of the affected
    objects, which is the actual contract.
    """
    objects: list[dict[str, Any]] = []
    rows = conn.execute(
        "SELECT type, name, tbl_name, sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
    ).fetchall()
    for type_, name, tbl_name, sql in rows:
        obj: dict[str, Any] = {
            "type": type_,
            "name": name,
            "tbl_name": tbl_name,
            "sql": _normalize_sql(sql),
        }
        if type_ == "table":
            obj["columns"] = [
                {
                    "cid": cid,
                    "name": cname,
                    "type": ctype,
                    "notnull": cnotnull,
                    "dflt_value": cdflt,
                    "pk": cpk,
                }
                for cid, cname, ctype, cnotnull, cdflt, cpk in conn.execute(f'PRAGMA table_info("{name}")').fetchall()
            ]
            obj["foreign_keys"] = [
                {
                    "id": fid,
                    "seq": fseq,
                    "table": ftable,
                    "from": ffrom,
                    "to": fto,
                    "on_update": fon_update,
                    "on_delete": fon_delete,
                    "match": fmatch,
                }
                for (
                    fid,
                    fseq,
                    ftable,
                    ffrom,
                    fto,
                    fon_update,
                    fon_delete,
                    fmatch,
                ) in conn.execute(f'PRAGMA foreign_key_list("{name}")').fetchall()
            ]
        objects.append(obj)
    return {"objects": objects}


def diff_summary(actual: dict[str, Any], expected: dict[str, Any]) -> str:
    """Produce a readable summary of where two introspection dicts differ.

    Returns the empty string when they match. Otherwise lists, in order:
    added objects, removed objects, changed objects (with a per-field
    breakdown).
    """
    lines: list[str] = []
    actual_by_key = {(o["type"], o["name"]): o for o in actual.get("objects", [])}
    expected_by_key = {(o["type"], o["name"]): o for o in expected.get("objects", [])}
    added = sorted(actual_by_key.keys() - expected_by_key.keys())
    removed = sorted(expected_by_key.keys() - actual_by_key.keys())
    common = sorted(actual_by_key.keys() & expected_by_key.keys())
    for key in added:
        lines.append(f"+ added {key[0]} {key[1]!r}")
    for key in removed:
        lines.append(f"- removed {key[0]} {key[1]!r}")
    for key in common:
        a, e = actual_by_key[key], expected_by_key[key]
        if a == e:
            continue
        lines.append(f"~ changed {key[0]} {key[1]!r}:")
        for field in sorted(set(a) | set(e)):
            if a.get(field) != e.get(field):
                lines.append(f"    {field}: actual={a.get(field)!r} expected={e.get(field)!r}")
    return "\n".join(lines)
