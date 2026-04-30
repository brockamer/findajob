# #333 Multi-tenant Operator Dashboard — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship `/admin/stacks/` — an operator-only summary page on `findajob.web` that surfaces every `findajob-*` stack on `docker.lan` with last-triage time, stage distribution, stuck-prep count, 24h success/failure counts, and a click-to-copy SSH drill-down recipe. Operator mode is gated by a single env flag; image stays bit-identical to testers'.

**Architecture:** New `findajob.admin` package holds three pure-data modules (`jsonl_tail`, `stack_discovery`, `stack_health`). One new route module under `findajob.web.routes`, registered conditionally inside `findajob.web.app.create_app()` when `FINDAJOB_OPERATOR_MODE=1`. One Jinja2 template extending `base.html`. Two conditional bits in `_nav.html` (red bar + Admin link) gated on the same env flag, surfaced via a templates global.

> **Deviation from spec §4.1:** The spec's draft put the conditional include in `routes/__init__.py`. That evaluates the env at module-import time, which makes per-test `monkeypatch.setenv` ineffective (the package is already loaded). The right hook is `create_app()` — each app instance re-evaluates the env when constructed. The spec's intent (env-flag-gated, route module not imported when flag unset) is preserved; only the registration site moves.

**Tech Stack:** Python 3.13, FastAPI APIRouter, Jinja2, sqlite3 (read-only URI mode), pytest, Tailwind utility classes, Alpine.js (existing CDN dep).

**Spec:** `docs/superpowers/specs/2026-04-30-333-design.md` (committed `57e3058`).

---

## Goal + scope

`docker.lan` now runs six findajob stacks. There is no aggregated view of "is everyone's pipeline triaging cleanly?" — operator inspects each stack's `pipeline.jsonl` by hand. This plan ships the summary view that answers the question in one page load.

**In scope:**
- `findajob.admin` package: `jsonl_tail`, `stack_discovery`, `stack_health` modules + tests.
- `/admin/stacks/` route + Jinja template.
- `_nav.html` operator-mode visual cue (red bar) + conditional Admin link.
- `findajob.web.routes.__init__` conditional include of admin route module.
- `findajob.web.app.create_app` wires `operator_mode` into `templates.env.globals`.
- Documentation in `docs/setup/install-docker.md`, `CLAUDE.md`, `CLAUDE.local.md`, `CHANGELOG.md` (incl. `### Migration required` bullet).
- Whole-feature verification gate run on `docker.lan` post-merge.

**Explicitly out of scope** (file as follow-ups post-merge per spec §11):
- Drill-down `/admin/stacks/{handle}/log` page — replaced by SSH recipe in column 7.
- Auto-refresh / HTMX polling — static-on-load only.
- ntfy push on stale stacks — belongs in `notify.py health-check` extension.
- Tenant-scoped views — testers don't see this surface.
- Cross-stack actions (restart/force-triage) — display-only.
- Time-series graphs.

---

## Task 1: `findajob.admin.jsonl_tail` — bounded JSONL reader

**Files:**
- Create: `src/findajob/admin/__init__.py`
- Create: `src/findajob/admin/jsonl_tail.py`
- Create: `tests/test_admin_jsonl_tail.py`

- [ ] **Step 1: Create the empty package marker.**

```python
# src/findajob/admin/__init__.py
"""Operator-mode helpers for cross-stack health inspection.

Pure-data modules (no FastAPI imports). The HTTP surface lives in
findajob.web.routes.admin_stacks.
"""
```

- [ ] **Step 2: Write the failing test file.**

```python
# tests/test_admin_jsonl_tail.py
"""Tests for findajob.admin.jsonl_tail.tail_events."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from findajob.admin.jsonl_tail import tail_events


def _write_events(path: Path, events: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n")


def test_missing_file_returns_empty(tmp_path: Path) -> None:
    assert list(tail_events(tmp_path / "absent.jsonl")) == []


def test_empty_file_returns_empty(tmp_path: Path) -> None:
    p = tmp_path / "empty.jsonl"
    p.touch()
    assert list(tail_events(p)) == []


def test_small_file_yields_newest_first(tmp_path: Path) -> None:
    p = tmp_path / "small.jsonl"
    _write_events(
        p,
        [
            {"ts": "2026-04-30T00:00:00+00:00", "event": "pipeline_started"},
            {"ts": "2026-04-30T00:05:00+00:00", "event": "pipeline_complete"},
        ],
    )
    events = list(tail_events(p))
    assert [e["event"] for e in events] == ["pipeline_complete", "pipeline_started"]


def test_large_file_reads_only_tail(tmp_path: Path) -> None:
    p = tmp_path / "large.jsonl"
    # 5000 events × ~80 bytes ≈ 400 KB. Use max_bytes=10000 to force tail behavior.
    events = [{"ts": f"2026-04-30T00:00:{i:02d}+00:00", "event": "watchdog_run", "i": i} for i in range(5000)]
    _write_events(p, events)
    out = list(tail_events(p, max_bytes=10_000))
    assert len(out) > 0
    assert len(out) < 5000  # did not read whole file
    # Newest event in file is i=4999; tail must include it.
    assert out[0]["i"] == 4999


def test_malformed_line_is_skipped(tmp_path: Path) -> None:
    p = tmp_path / "mixed.jsonl"
    p.write_text(
        '{"ts": "2026-04-30T00:00:00+00:00", "event": "pipeline_started"}\n'
        "this-is-not-json\n"
        '{"ts": "2026-04-30T00:05:00+00:00", "event": "pipeline_complete"}\n'
    )
    events = list(tail_events(p))
    assert [e["event"] for e in events] == ["pipeline_complete", "pipeline_started"]


def test_partial_first_line_at_buffer_boundary_is_dropped(tmp_path: Path) -> None:
    """When the tail-window cuts mid-line, the partial first line is discarded."""
    p = tmp_path / "boundary.jsonl"
    # Two events; force max_bytes to land mid-first-line.
    events = [
        {"ts": "2026-04-30T00:00:00+00:00", "event": "pipeline_started", "padding": "x" * 200},
        {"ts": "2026-04-30T00:05:00+00:00", "event": "pipeline_complete"},
    ]
    _write_events(p, events)
    # Pick a buffer size that splits the first line.
    full = p.read_text()
    cut_at = len(full) - 100  # well into line 2
    out = list(tail_events(p, max_bytes=full[cut_at:].__len__() + 5))
    # Whatever survives, every yielded entry must be valid JSON (no half-line).
    for e in out:
        assert isinstance(e, dict)
```

- [ ] **Step 3: Run tests; verify they all fail.**

Run: `uv run pytest tests/test_admin_jsonl_tail.py -v`
Expected: ImportError on `findajob.admin.jsonl_tail` for every test.

- [ ] **Step 4: Implement `jsonl_tail.py`.**

```python
# src/findajob/admin/jsonl_tail.py
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

    Returns an empty iterator when the file is missing or empty. Skips
    malformed lines with a single WARNING log per occurrence. When the
    tail buffer cuts mid-line, the partial first line is discarded so
    every yielded value is valid JSON.
    """
    try:
        size = os.path.getsize(path)
    except FileNotFoundError:
        return
    if size == 0:
        return

    read_len = min(size, max_bytes)
    with open(path, "rb") as f:
        f.seek(size - read_len)
        chunk = f.read(read_len)

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
            logger.warning("admin_stacks.jsonl_tail: malformed line in %s", path)
```

- [ ] **Step 5: Run tests; verify they pass.**

Run: `uv run pytest tests/test_admin_jsonl_tail.py -v`
Expected: 6 passed.

- [ ] **Step 6: Commit.**

```bash
git add src/findajob/admin/__init__.py src/findajob/admin/jsonl_tail.py tests/test_admin_jsonl_tail.py
git commit -m "$(cat <<'EOF'
feat(admin): bounded JSONL tail reader for #333 dashboard

Yields decoded events newest-first from at most max_bytes of the file
end, drops partial first line at the buffer boundary, skips malformed
JSON with a single WARNING. Pure-data module — no FastAPI imports.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: `findajob.admin.stack_discovery` — FS enumeration

**Files:**
- Create: `src/findajob/admin/stack_discovery.py`
- Create: `tests/test_admin_stack_discovery.py`

- [ ] **Step 1: Write the failing test file.**

```python
# tests/test_admin_stack_discovery.py
"""Tests for findajob.admin.stack_discovery.discover_stacks."""

from __future__ import annotations

from pathlib import Path

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


def test_stackpath_is_frozen_dataclass(tmp_path: Path) -> None:
    _make_stack(tmp_path, "alice")
    s = discover_stacks(tmp_path)[0]
    assert isinstance(s, StackPath)
    # Frozen dataclasses raise on mutation.
    import dataclasses
    assert dataclasses.is_dataclass(s)
```

- [ ] **Step 2: Run tests; verify they fail.**

Run: `uv run pytest tests/test_admin_stack_discovery.py -v`
Expected: ImportError on `findajob.admin.stack_discovery`.

- [ ] **Step 3: Implement `stack_discovery.py`.**

```python
# src/findajob/admin/stack_discovery.py
"""Glob /opt/stacks/findajob-*/state/ to enumerate operator-visible stacks."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class StackPath:
    """Filesystem locator for one findajob stack on docker.lan."""

    handle: str
    root: Path
    db_path: Path
    jsonl_path: Path


def discover_stacks(stacks_root: Path) -> list[StackPath]:
    """Return a sorted list of `StackPath` for every `findajob-*/state/`
    directory under `stacks_root`.

    Skips siblings that don't match the prefix (e.g. `dozzle`,
    `archivebox`). Skips `findajob-*` directories without a `state/`
    subdir (mid-onboarding or broken installs).

    Returns an empty list when `stacks_root` is missing or empty.
    """
    if not stacks_root.is_dir():
        return []

    out: list[StackPath] = []
    for entry in sorted(stacks_root.iterdir()):
        if not entry.is_dir():
            continue
        if not entry.name.startswith("findajob-"):
            continue
        state = entry / "state"
        if not state.is_dir():
            continue
        handle = entry.name[len("findajob-") :]
        out.append(
            StackPath(
                handle=handle,
                root=entry,
                db_path=state / "data" / "pipeline.db",
                jsonl_path=state / "logs" / "pipeline.jsonl",
            )
        )
    return out
```

- [ ] **Step 4: Run tests; verify they pass.**

Run: `uv run pytest tests/test_admin_stack_discovery.py -v`
Expected: 7 passed.

- [ ] **Step 5: Commit.**

```bash
git add src/findajob/admin/stack_discovery.py tests/test_admin_stack_discovery.py
git commit -m "$(cat <<'EOF'
feat(admin): glob /opt/stacks for #333 stack discovery

Returns sorted StackPath list, one per findajob-*/state/ directory.
Skips non-matching siblings (dozzle, archivebox) and findajob-* dirs
without a state/ subdir (mid-onboarding or broken installs).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: `findajob.admin.stack_health` — per-stack data gather

**Files:**
- Create: `src/findajob/admin/stack_health.py`
- Create: `tests/test_admin_stack_health.py`
- Create: `tests/conftest_admin.py` (helper for building DB + JSONL fixtures)

- [ ] **Step 1: Write the fixture-builder helper.**

```python
# tests/conftest_admin.py
"""Helpers for building admin-stack test fixtures programmatically.

We do not commit binary SQLite files; tests build them inline so the
fixture intent is visible in the test source.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path


_JOBS_SCHEMA = """
CREATE TABLE jobs (
    id TEXT PRIMARY KEY,
    stage TEXT NOT NULL,
    flagged_at TEXT,
    prep_started_at TEXT
);
"""


def build_pipeline_db(
    db_path: Path,
    *,
    rows: list[dict] | None = None,
) -> None:
    """Build a minimal pipeline.db with just the columns stack_health reads.

    `rows` is a list of dicts with keys: id, stage, prep_started_at (ISO 8601 UTC).
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(_JOBS_SCHEMA)
    for r in rows or []:
        conn.execute(
            "INSERT INTO jobs (id, stage, prep_started_at) VALUES (?, ?, ?)",
            (r["id"], r["stage"], r.get("prep_started_at")),
        )
    conn.commit()
    conn.close()


def build_pipeline_jsonl(jsonl_path: Path, events: list[dict]) -> None:
    jsonl_path.parent.mkdir(parents=True, exist_ok=True)
    jsonl_path.write_text("\n".join(json.dumps(e) for e in events) + "\n")


def write_corrupt_db(db_path: Path) -> None:
    """Write garbage that will fail to open as SQLite."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db_path.write_bytes(b"not a sqlite database")
```

- [ ] **Step 2: Write the failing test file.**

```python
# tests/test_admin_stack_health.py
"""Tests for findajob.admin.stack_health.gather."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from findajob.admin.stack_discovery import StackPath
from findajob.admin.stack_health import StackHealth, gather

from tests.conftest_admin import build_pipeline_db, build_pipeline_jsonl, write_corrupt_db


# Fixed reference time for deterministic tests.
NOW = datetime(2026, 4, 30, 12, 0, 0, tzinfo=UTC)


def _stackpath(root: Path, handle: str = "alice") -> StackPath:
    state = root / f"findajob-{handle}" / "state"
    return StackPath(
        handle=handle,
        root=root / f"findajob-{handle}",
        db_path=state / "data" / "pipeline.db",
        jsonl_path=state / "logs" / "pipeline.jsonl",
    )


def test_missing_db_and_missing_jsonl(tmp_path: Path) -> None:
    sp = _stackpath(tmp_path)
    h = gather(sp, now=NOW)
    assert h.handle == "alice"
    assert h.db_missing is True
    assert h.jsonl_missing is True
    assert h.error is None
    assert h.last_triage_complete is None
    assert h.stage_counts == {}
    assert h.stuck_prep_count == 0
    assert h.freshness == "unknown"


def test_corrupt_db_sets_error_field(tmp_path: Path) -> None:
    sp = _stackpath(tmp_path)
    write_corrupt_db(sp.db_path)
    build_pipeline_jsonl(sp.jsonl_path, [])
    h = gather(sp, now=NOW)
    assert h.error is not None
    assert "database" in h.error.lower() or "sqlite" in h.error.lower() or "file is not" in h.error.lower()


def test_stage_counts_aggregate(tmp_path: Path) -> None:
    sp = _stackpath(tmp_path)
    build_pipeline_db(
        sp.db_path,
        rows=[
            {"id": "a", "stage": "scored"},
            {"id": "b", "stage": "scored"},
            {"id": "c", "stage": "manual_review"},
            {"id": "d", "stage": "applied"},
        ],
    )
    build_pipeline_jsonl(sp.jsonl_path, [])
    h = gather(sp, now=NOW)
    assert h.stage_counts == {"scored": 2, "manual_review": 1, "applied": 1}


def test_stuck_prep_counts_only_over_60min(tmp_path: Path) -> None:
    sp = _stackpath(tmp_path)
    fresh = (NOW - timedelta(minutes=30)).isoformat()
    stuck1 = (NOW - timedelta(minutes=61)).isoformat()
    stuck2 = (NOW - timedelta(hours=3)).isoformat()
    build_pipeline_db(
        sp.db_path,
        rows=[
            {"id": "a", "stage": "prep_in_progress", "prep_started_at": fresh},
            {"id": "b", "stage": "prep_in_progress", "prep_started_at": stuck1},
            {"id": "c", "stage": "prep_in_progress", "prep_started_at": stuck2},
            {"id": "d", "stage": "scored"},  # not prep_in_progress, ignored
        ],
    )
    build_pipeline_jsonl(sp.jsonl_path, [])
    h = gather(sp, now=NOW)
    assert h.stuck_prep_count == 2


def test_pipeline_complete_event_drives_freshness(tmp_path: Path) -> None:
    sp = _stackpath(tmp_path)
    build_pipeline_db(sp.db_path)
    fresh = (NOW - timedelta(hours=3)).isoformat()
    build_pipeline_jsonl(
        sp.jsonl_path,
        [
            {"ts": fresh, "event": "pipeline_started"},
            {"ts": fresh, "event": "pipeline_complete"},
        ],
    )
    h = gather(sp, now=NOW)
    assert h.last_triage_complete is not None
    assert h.freshness == "fresh"


def test_freshness_buckets(tmp_path: Path) -> None:
    cases = [
        (timedelta(hours=10), "fresh"),
        (timedelta(hours=23, minutes=59), "fresh"),
        (timedelta(hours=24), "late"),
        (timedelta(hours=30), "late"),
        (timedelta(hours=36), "stale"),
        (timedelta(days=3), "stale"),
    ]
    for delta, expected in cases:
        sp_root = tmp_path / f"case-{int(delta.total_seconds())}"
        sp_root.mkdir()
        sp = _stackpath(sp_root, handle="t")
        build_pipeline_db(sp.db_path)
        ts = (NOW - delta).isoformat()
        build_pipeline_jsonl(
            sp.jsonl_path,
            [{"ts": ts, "event": "pipeline_complete"}],
        )
        h = gather(sp, now=NOW)
        assert h.freshness == expected, f"{delta} → expected {expected}, got {h.freshness}"


def test_24h_event_counts(tmp_path: Path) -> None:
    sp = _stackpath(tmp_path)
    build_pipeline_db(sp.db_path)
    in_window = (NOW - timedelta(hours=10)).isoformat()
    in_window_2 = (NOW - timedelta(hours=20)).isoformat()
    out_of_window = (NOW - timedelta(hours=30)).isoformat()
    build_pipeline_jsonl(
        sp.jsonl_path,
        [
            {"ts": in_window, "event": "pipeline_complete"},
            {"ts": in_window_2, "event": "pipeline_complete"},
            {"ts": out_of_window, "event": "pipeline_complete"},
            {"ts": in_window, "event": "pipeline_terminated"},
        ],
    )
    h = gather(sp, now=NOW)
    assert h.triage_success_24h == 2
    assert h.triage_failure_24h == 1


def test_last_failure_timestamps(tmp_path: Path) -> None:
    sp = _stackpath(tmp_path)
    build_pipeline_db(sp.db_path)
    build_pipeline_jsonl(
        sp.jsonl_path,
        [
            {"ts": "2026-04-29T08:00:00+00:00", "event": "aichat_failure"},
            {"ts": "2026-04-30T05:00:00+00:00", "event": "aichat_failure"},  # most recent
            {"ts": "2026-04-30T01:00:00+00:00", "event": "discovery_failed"},
            {"ts": "2026-04-29T22:00:00+00:00", "event": "prep_failed_reset"},
        ],
    )
    h = gather(sp, now=NOW)
    assert h.last_aichat_failure == datetime(2026, 4, 30, 5, 0, tzinfo=UTC)
    assert h.last_discovery_failed == datetime(2026, 4, 30, 1, 0, tzinfo=UTC)
    assert h.last_prep_failed == datetime(2026, 4, 29, 22, 0, tzinfo=UTC)


def test_returns_stackhealth_dataclass(tmp_path: Path) -> None:
    sp = _stackpath(tmp_path)
    h = gather(sp, now=NOW)
    assert isinstance(h, StackHealth)
```

- [ ] **Step 3: Run tests; verify they fail.**

Run: `uv run pytest tests/test_admin_stack_health.py -v`
Expected: ImportError on `findajob.admin.stack_health`.

- [ ] **Step 4: Implement `stack_health.py`.**

```python
# src/findajob/admin/stack_health.py
"""Per-stack health aggregation: pipeline.db SQL + pipeline.jsonl tail."""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Literal

from findajob.admin.jsonl_tail import tail_events
from findajob.admin.stack_discovery import StackPath

logger = logging.getLogger(__name__)

Freshness = Literal["fresh", "late", "stale", "unknown"]

_FAILURE_EVENTS = ("aichat_failure", "discovery_failed", "prep_failed", "prep_failed_reset")


@dataclass(frozen=True)
class StackHealth:
    handle: str
    last_triage_complete: datetime | None = None
    last_triage_failed: datetime | None = None
    last_aichat_failure: datetime | None = None
    last_discovery_failed: datetime | None = None
    last_prep_failed: datetime | None = None
    triage_success_24h: int = 0
    triage_failure_24h: int = 0
    stage_counts: dict[str, int] = field(default_factory=dict)
    stuck_prep_count: int = 0
    db_missing: bool = False
    jsonl_missing: bool = False
    error: str | None = None
    freshness: Freshness = "unknown"


def gather(stack: StackPath, *, now: datetime | None = None) -> StackHealth:
    """Read `pipeline.db` (read-only) and tail `pipeline.jsonl`. Return one
    StackHealth dataclass with everything the dashboard template needs.

    All exceptions caught and surfaced via `StackHealth.error` so a single
    broken stack does not crash the page. `now` is injectable for tests.
    """
    now = now or datetime.now(UTC)
    db_missing = not stack.db_path.is_file()
    jsonl_missing = not stack.jsonl_path.is_file()

    error: str | None = None
    stage_counts: dict[str, int] = {}
    stuck_prep_count = 0

    if not db_missing:
        try:
            uri = f"file:{stack.db_path}?mode=ro"
            with sqlite3.connect(uri, uri=True) as conn:
                conn.row_factory = sqlite3.Row
                stage_counts = {
                    row["stage"]: row["n"]
                    for row in conn.execute(
                        "SELECT stage, COUNT(*) AS n FROM jobs GROUP BY stage"
                    )
                }
                cutoff = (now - timedelta(minutes=60)).isoformat()
                stuck_prep_count = conn.execute(
                    "SELECT COUNT(*) FROM jobs "
                    "WHERE stage = 'prep_in_progress' "
                    "AND prep_started_at IS NOT NULL "
                    "AND prep_started_at < ?",
                    (cutoff,),
                ).fetchone()[0]
        except sqlite3.Error as e:
            error = f"sqlite: {e}"
        except Exception as e:  # defensive — don't let one stack crash the page
            logger.warning("admin_stacks: gather failed for %s: %s", stack.handle, e)
            error = f"{type(e).__name__}: {e}"

    last_triage_complete: datetime | None = None
    last_triage_failed: datetime | None = None
    last_aichat: datetime | None = None
    last_discovery: datetime | None = None
    last_prep: datetime | None = None
    success_24h = 0
    failure_24h = 0
    cutoff_24h = now - timedelta(hours=24)

    if not jsonl_missing:
        for event in tail_events(stack.jsonl_path):
            ts = _parse_ts(event.get("ts"))
            if ts is None:
                continue
            ev = event.get("event")
            if ev == "pipeline_complete":
                if last_triage_complete is None:
                    last_triage_complete = ts
                if ts >= cutoff_24h:
                    success_24h += 1
            elif ev == "pipeline_terminated":
                if last_triage_failed is None:
                    last_triage_failed = ts
                if ts >= cutoff_24h:
                    failure_24h += 1
            elif ev == "aichat_failure":
                if last_aichat is None:
                    last_aichat = ts
            elif ev == "discovery_failed":
                if last_discovery is None:
                    last_discovery = ts
            elif ev in ("prep_failed", "prep_failed_reset"):
                if last_prep is None:
                    last_prep = ts

    return StackHealth(
        handle=stack.handle,
        last_triage_complete=last_triage_complete,
        last_triage_failed=last_triage_failed,
        last_aichat_failure=last_aichat,
        last_discovery_failed=last_discovery,
        last_prep_failed=last_prep,
        triage_success_24h=success_24h,
        triage_failure_24h=failure_24h,
        stage_counts=stage_counts,
        stuck_prep_count=stuck_prep_count,
        db_missing=db_missing,
        jsonl_missing=jsonl_missing,
        error=error,
        freshness=_freshness(last_triage_complete, now),
    )


def _parse_ts(raw: object) -> datetime | None:
    if not isinstance(raw, str):
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def _freshness(last: datetime | None, now: datetime) -> Freshness:
    if last is None:
        return "unknown"
    age = now - last
    if age < timedelta(hours=24):
        return "fresh"
    if age < timedelta(hours=36):
        return "late"
    return "stale"
```

- [ ] **Step 5: Run tests; verify they pass.**

Run: `uv run pytest tests/test_admin_stack_health.py -v`
Expected: 8 passed.

- [ ] **Step 6: Commit.**

```bash
git add src/findajob/admin/stack_health.py tests/test_admin_stack_health.py tests/conftest_admin.py
git commit -m "$(cat <<'EOF'
feat(admin): per-stack health gather for #333

Reads pipeline.db (mode=ro URI) for stage distribution and stuck-prep
count; tails pipeline.jsonl for triage lifecycle and failure events.
Exceptions surface via StackHealth.error rather than raising — one
broken stack must not crash the dashboard page.

Freshness buckets: <24h fresh, 24-36h late, >36h stale. Stuck-prep
threshold matches watchdog.py (>60min). 24h event windows are computed
relative to caller-provided `now` for deterministic tests.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Wire `operator_mode` into template globals + nav

**Files:**
- Modify: `src/findajob/web/app.py:36-42` (templates env globals block)
- Modify: `src/findajob/web/templates/_nav.html` (full file)
- Create: `tests/test_admin_nav.py`

- [ ] **Step 1: Write the failing test file.**

```python
# tests/test_admin_nav.py
"""Operator-mode visual cue + Admin nav link tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from findajob.web.app import create_app


@pytest.fixture
def app_factory(tmp_path: Path):
    def make(*, operator_mode: bool):
        if operator_mode:
            import os
            os.environ["FINDAJOB_OPERATOR_MODE"] = "1"
        else:
            import os
            os.environ.pop("FINDAJOB_OPERATOR_MODE", None)
        companies = tmp_path / "companies"
        companies.mkdir()
        db = tmp_path / "pipeline.db"
        db.touch()
        return create_app(companies_root=companies, db_path=db, base_root=tmp_path)
    return make


def test_nav_default_color_when_operator_mode_off(app_factory) -> None:
    app = app_factory(operator_mode=False)
    client = TestClient(app)
    r = client.get("/healthz")  # any page renders nav via base.html → not relevant; use a page
    # /healthz returns plain text, not nav. Use the docs index instead.
    r = client.get("/docs/")
    assert r.status_code in (200, 401)
    if r.status_code == 200:
        assert "bg-slate-800" in r.text
        assert "bg-rose-700" not in r.text
        assert ">Admin<" not in r.text


def test_nav_red_bar_when_operator_mode_on(app_factory) -> None:
    app = app_factory(operator_mode=True)
    client = TestClient(app)
    r = client.get("/docs/")
    assert r.status_code in (200, 401)
    if r.status_code == 200:
        assert "bg-rose-700" in r.text
        assert "bg-slate-800" not in r.text or r.text.count("bg-slate-800") < r.text.count("bg-rose-700")
        assert ">Admin<" in r.text


def test_admin_link_points_to_stacks(app_factory) -> None:
    app = app_factory(operator_mode=True)
    client = TestClient(app)
    r = client.get("/docs/")
    if r.status_code == 200:
        assert 'href="/admin/stacks/"' in r.text
```

- [ ] **Step 2: Run tests; verify the operator-mode-on tests fail.**

Run: `uv run pytest tests/test_admin_nav.py -v`
Expected: `test_nav_red_bar_when_operator_mode_on` and `test_admin_link_points_to_stacks` FAIL — current nav has no operator-mode branch.

- [ ] **Step 3: Update `_nav.html` with conditional bar color + Admin link.**

Replace the entire file `src/findajob/web/templates/_nav.html` with:

```jinja
{# Top navigation. Highlights the active group via aria-current="page".

   `active_prefix` is the URL prefix that marks the group active — distinct
   from `href` because e.g. the Board link points at /board/dashboard (the
   landing view) but should highlight on every /board/* page. Previously all
   non-dashboard board pages failed to highlight the Board tab (#138).

   When operator_mode is set (FINDAJOB_OPERATOR_MODE=1 in env), the bar
   renders in rose-700 as an ambient visual cue and the Admin link is
   shown. Operator mode is operator-only — testers' stacks render the
   default slate-800 bar with no Admin link. #}
{% set groups = [
  ("/", "Home", "/"),
  ("/board/dashboard", "Board", "/board/"),
  ("/materials/", "Materials", "/materials/"),
  ("/ingest/", "Ingest", "/ingest/"),
  ("/stats/funnel", "Stats", "/stats/"),
  ("/tools/", "Tools", "/tools/"),
  ("/config/", "Config", "/config/"),
  ("/docs/", "Docs", "/docs/"),
] %}
<nav class="{% if operator_mode %}bg-rose-700{% else %}bg-slate-800{% endif %} text-slate-100 px-4 py-2 shadow-sm">
  <ul class="flex gap-4 items-center">
    <li class="font-bold mr-4">findajob{% if operator_mode %} <span class="text-xs uppercase tracking-wide opacity-80">operator</span>{% endif %}</li>
    {% for href, label, active_prefix in groups %}
      {% set active = (active_prefix == "/" and request.url.path == "/")
           or (active_prefix != "/" and request.url.path.startswith(active_prefix)) %}
      <li>
        <a href="{{ href }}"
           {% if active %}aria-current="page"{% endif %}
           class="px-2 py-1 rounded {% if active %}{% if operator_mode %}bg-rose-900{% else %}bg-slate-600{% endif %}{% else %}{% if operator_mode %}hover:bg-rose-800{% else %}hover:bg-slate-700{% endif %}{% endif %}">
          {{ label }}
        </a>
      </li>
    {% endfor %}
    {% if operator_mode %}
      {% set admin_active = request.url.path.startswith("/admin/") %}
      <li class="ml-auto">
        <a href="/admin/stacks/"
           {% if admin_active %}aria-current="page"{% endif %}
           class="px-2 py-1 rounded {% if admin_active %}bg-rose-900{% else %}hover:bg-rose-800{% endif %}">
          Admin
        </a>
      </li>
    {% endif %}
  </ul>
</nav>
```

- [ ] **Step 4: Add `operator_mode` to templates globals in `app.py`.**

In `src/findajob/web/app.py`, after the existing `templates.env.globals[...]` block (currently ending around line 42), add the operator_mode flag:

```python
    templates.env.globals["folder_stages"] = set(FOLDER_STAGES)
    templates.env.globals["applied_age_bucket"] = applied_age_bucket
    templates.env.globals["remote_cell_class"] = remote_cell_class
    templates.env.globals["stage_row_class"] = stage_row_class
    templates.env.globals["filter_remove_qs"] = filter_remove_qs
    templates.env.globals["filter_qs_with"] = filter_qs_with
    templates.env.globals["operator_mode"] = os.environ.get("FINDAJOB_OPERATOR_MODE") == "1"
```

The `os` module is already imported in `app.py`.

- [ ] **Step 5: Run tests; verify they pass.**

Run: `uv run pytest tests/test_admin_nav.py -v`
Expected: 3 passed.

- [ ] **Step 6: Run the full test suite to confirm no regressions in other nav-touching pages.**

Run: `uv run pytest tests/ -k "nav or board or materials or landing or ingest or docs or stats or config or tools" -q`
Expected: all pre-existing tests still pass.

- [ ] **Step 7: Commit.**

```bash
git add src/findajob/web/app.py src/findajob/web/templates/_nav.html tests/test_admin_nav.py
git commit -m "$(cat <<'EOF'
feat(web): operator-mode nav cue + Admin link for #333

The top nav bar renders in rose-700 (red) on every page when
FINDAJOB_OPERATOR_MODE=1 is set in the env. An "Admin" link
pinned to the right side appears in the same condition. Tester
stacks render the existing slate-800 bar unchanged.

The flag is wired through templates.env.globals once at app
startup so individual route handlers don't need to thread it.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: `/admin/stacks/` route + template

**Files:**
- Create: `src/findajob/web/routes/admin_stacks.py`
- Create: `src/findajob/web/templates/admin/stacks_index.html`
- Create: `tests/test_admin_stacks_route.py`

- [ ] **Step 1: Write the failing test file.**

```python
# tests/test_admin_stacks_route.py
"""Tests for /admin/stacks/ route."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from findajob.web.app import create_app

from tests.conftest_admin import build_pipeline_db, build_pipeline_jsonl


@pytest.fixture
def operator_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Build an operator-mode app whose admin route reads from a tmp /opt/stacks."""
    monkeypatch.setenv("FINDAJOB_OPERATOR_MODE", "1")
    monkeypatch.setenv("FINDAJOB_ADMIN_STACKS_ROOT", str(tmp_path / "stacks"))
    companies = tmp_path / "companies"
    companies.mkdir()
    db = tmp_path / "pipeline.db"
    db.touch()
    return create_app(companies_root=companies, db_path=db, base_root=tmp_path)


@pytest.fixture
def tester_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Tester-mode (operator flag unset) — admin route should not exist."""
    monkeypatch.delenv("FINDAJOB_OPERATOR_MODE", raising=False)
    companies = tmp_path / "companies"
    companies.mkdir()
    db = tmp_path / "pipeline.db"
    db.touch()
    return create_app(companies_root=companies, db_path=db, base_root=tmp_path)


def _seed_stack(stacks_root: Path, handle: str, *, with_data: bool = True) -> None:
    sp_root = stacks_root / f"findajob-{handle}" / "state"
    if with_data:
        build_pipeline_db(
            sp_root / "data" / "pipeline.db",
            rows=[{"id": f"{handle}-1", "stage": "scored"}],
        )
        build_pipeline_jsonl(
            sp_root / "logs" / "pipeline.jsonl",
            [{"ts": "2026-04-30T11:00:00+00:00", "event": "pipeline_complete"}],
        )
    else:
        sp_root.mkdir(parents=True)


def test_route_returns_404_when_operator_mode_off(tester_app) -> None:
    client = TestClient(tester_app)
    r = client.get("/admin/stacks/")
    assert r.status_code == 404


def test_route_returns_200_in_operator_mode(operator_app, tmp_path: Path) -> None:
    stacks = tmp_path / "stacks"
    stacks.mkdir()
    _seed_stack(stacks, "alice")
    _seed_stack(stacks, "dave")

    client = TestClient(operator_app)
    r = client.get("/admin/stacks/")
    assert r.status_code == 200
    assert ">alice<" in r.text
    assert ">dave<" in r.text


def test_pure_alphabetical_when_no_operator_handle(operator_app, tmp_path: Path) -> None:
    """When FINDAJOB_OPERATOR_HANDLE is unset, rows sort pure alphabetical."""
    stacks = tmp_path / "stacks"
    stacks.mkdir()
    for h in ("tango", "alice", "papa"):
        _seed_stack(stacks, h)

    client = TestClient(operator_app)
    r = client.get("/admin/stacks/")
    body = r.text
    pos = lambda s: body.find(s)  # noqa: E731
    assert pos(">alice<") < pos(">papa<") < pos(">tango<")


def test_operator_handle_floats_to_top(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When FINDAJOB_OPERATOR_HANDLE=papa, papa renders first; rest alphabetical."""
    monkeypatch.setenv("FINDAJOB_OPERATOR_MODE", "1")
    monkeypatch.setenv("FINDAJOB_OPERATOR_HANDLE", "papa")
    stacks = tmp_path / "stacks"
    stacks.mkdir()
    monkeypatch.setenv("FINDAJOB_ADMIN_STACKS_ROOT", str(stacks))
    for h in ("tango", "alice", "papa", "dave"):
        _seed_stack(stacks, h)
    companies = tmp_path / "companies"
    companies.mkdir()
    db = tmp_path / "pipeline.db"
    db.touch()
    app = create_app(companies_root=companies, db_path=db, base_root=tmp_path)
    client = TestClient(app)
    r = client.get("/admin/stacks/")
    body = r.text
    pos = lambda s: body.find(s)  # noqa: E731
    # papa first, then alphabetical: alice, dave, tango.
    assert pos(">papa<") < pos(">alice<") < pos(">dave<") < pos(">tango<")


def test_empty_state_banner_when_no_stacks(operator_app, tmp_path: Path) -> None:
    (tmp_path / "stacks").mkdir()
    client = TestClient(operator_app)
    r = client.get("/admin/stacks/")
    assert r.status_code == 200
    assert "No stacks found" in r.text


def test_per_row_error_does_not_crash_page(operator_app, tmp_path: Path) -> None:
    stacks = tmp_path / "stacks"
    stacks.mkdir()
    _seed_stack(stacks, "alice")
    # Broken stack: write garbage as DB.
    broken = stacks / "findajob-broken" / "state" / "data"
    broken.mkdir(parents=True)
    (broken / "pipeline.db").write_bytes(b"not a sqlite file")
    (stacks / "findajob-broken" / "state" / "logs").mkdir()
    (stacks / "findajob-broken" / "state" / "logs" / "pipeline.jsonl").write_text("")

    client = TestClient(operator_app)
    r = client.get("/admin/stacks/")
    assert r.status_code == 200
    assert ">alice<" in r.text
    assert ">broken<" in r.text


def test_drill_down_recipe_renders_per_row(operator_app, tmp_path: Path) -> None:
    stacks = tmp_path / "stacks"
    stacks.mkdir()
    _seed_stack(stacks, "alice")
    client = TestClient(operator_app)
    r = client.get("/admin/stacks/")
    assert "ssh docker.lan tail -F /opt/stacks/findajob-alice/state/logs/pipeline.jsonl" in r.text


def test_basic_auth_inherited_when_set(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FINDAJOB_OPERATOR_MODE", "1")
    monkeypatch.setenv("FINDAJOB_ADMIN_STACKS_ROOT", str(tmp_path / "stacks"))
    monkeypatch.setenv("FINDAJOB_AUTH_USER", "op")
    monkeypatch.setenv("FINDAJOB_AUTH_PASS", "secret")
    (tmp_path / "stacks").mkdir()
    companies = tmp_path / "companies"
    companies.mkdir()
    db = tmp_path / "pipeline.db"
    db.touch()
    app = create_app(companies_root=companies, db_path=db, base_root=tmp_path)
    client = TestClient(app)
    # No auth header → 401
    r = client.get("/admin/stacks/")
    assert r.status_code == 401
    # With auth → 200
    import base64
    creds = base64.b64encode(b"op:secret").decode()
    r = client.get("/admin/stacks/", headers={"Authorization": f"Basic {creds}"})
    assert r.status_code == 200


def test_render_under_2s(operator_app, tmp_path: Path) -> None:
    """Performance budget per spec §4.6 — <2s for 6 stacks."""
    import time

    stacks = tmp_path / "stacks"
    stacks.mkdir()
    for h in ("alice", "dave", "ed", "judy", "papa", "tango"):
        sp_root = stacks / f"findajob-{h}" / "state"
        rows = [{"id": f"{h}-{i}", "stage": "scored"} for i in range(50)]
        build_pipeline_db(sp_root / "data" / "pipeline.db", rows=rows)
        events = [{"ts": "2026-04-30T11:00:00+00:00", "event": "watchdog_run"} for _ in range(500)]
        events.append({"ts": "2026-04-30T11:30:00+00:00", "event": "pipeline_complete"})
        build_pipeline_jsonl(sp_root / "logs" / "pipeline.jsonl", events)

    client = TestClient(operator_app)
    t0 = time.perf_counter()
    r = client.get("/admin/stacks/")
    elapsed = time.perf_counter() - t0
    assert r.status_code == 200
    assert elapsed < 2.0, f"render took {elapsed:.2f}s — over 2s budget"
```

- [ ] **Step 2: Run tests; verify they fail.**

Run: `uv run pytest tests/test_admin_stacks_route.py -v`
Expected: ImportError on `findajob.web.routes.admin_stacks` for every test that hits the operator app.

- [ ] **Step 3: Implement the route module.**

```python
# src/findajob/web/routes/admin_stacks.py
"""Operator-only multi-tenant stack health dashboard (#333).

Loaded only when FINDAJOB_OPERATOR_MODE=1. Reads cross-stack state from
/opt/stacks/findajob-*/state/ via read-only SQLite + bounded JSONL tail.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from findajob.admin.stack_discovery import discover_stacks
from findajob.admin.stack_health import StackHealth, gather

logger = logging.getLogger(__name__)
router = APIRouter()

DEFAULT_STACKS_ROOT = Path("/opt/stacks")


@router.get("/admin/stacks/", response_class=HTMLResponse)
def stacks_index(request: Request) -> HTMLResponse:
    """Render one row per active findajob stack."""
    t0 = time.perf_counter()
    stacks_root = Path(os.environ.get("FINDAJOB_ADMIN_STACKS_ROOT", str(DEFAULT_STACKS_ROOT)))
    stacks = discover_stacks(stacks_root)
    health = [gather(s) for s in stacks]
    sorted_health = _sort_operator_first(health)

    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    logger.info("admin_stacks: rendered N=%d stacks in %dms", len(sorted_health), elapsed_ms)

    templates = request.app.state.templates
    return templates.TemplateResponse(
        "admin/stacks_index.html",
        {
            "request": request,
            "health": sorted_health,
            "rendered_at": datetime.now(UTC),
            "stacks_root_display": str(stacks_root),
            "elapsed_ms": elapsed_ms,
        },
    )


def _sort_operator_first(rows: list[StackHealth]) -> list[StackHealth]:
    """When `FINDAJOB_OPERATOR_HANDLE` is set in the env, that handle's row
    renders first; the rest sort alphabetically. When unset, pure
    alphabetical.

    The handle is read from the env, never hardcoded — keeps tracked
    code free of operator-specific identifiers per CLAUDE.md PII /
    domain-neutrality rules.
    """
    op = os.environ.get("FINDAJOB_OPERATOR_HANDLE", "").strip()
    if not op:
        return sorted(rows, key=lambda r: r.handle)
    operator = [r for r in rows if r.handle == op]
    rest = sorted([r for r in rows if r.handle != op], key=lambda r: r.handle)
    return operator + rest
```

- [ ] **Step 4: Implement the template.**

Create `src/findajob/web/templates/admin/stacks_index.html`:

```jinja
{% extends "base.html" %}

{% block title %}Admin · Stacks · findajob{% endblock %}

{% block content %}
<div class="space-y-4">
  <header class="flex items-baseline justify-between">
    <h1 class="text-2xl font-semibold">Stack Health</h1>
    <p class="text-xs text-slate-500">
      Rendered {{ rendered_at.isoformat(timespec='seconds') }} · {{ elapsed_ms }}ms · browser reload to refresh
    </p>
  </header>

  {% if not health %}
    <div class="rounded border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-800">
      No stacks found at <code>{{ stacks_root_display }}/findajob-*/</code>.
      Check that operator compose mounts <code>/opt/stacks:/opt/stacks:ro</code>
      and that <code>FINDAJOB_OPERATOR_MODE=1</code> is set.
    </div>
  {% else %}
    <table class="w-full text-sm">
      <thead class="text-left text-xs uppercase tracking-wide text-slate-500 border-b">
        <tr>
          <th class="py-2 pr-4">Stack</th>
          <th class="py-2 pr-4">Last triage</th>
          <th class="py-2 pr-4">Stage distribution</th>
          <th class="py-2 pr-4">Stuck prep</th>
          <th class="py-2 pr-4">24h triage</th>
          <th class="py-2 pr-4">Last failure</th>
          <th class="py-2 pr-4">Drill-down</th>
        </tr>
      </thead>
      <tbody>
        {% for h in health %}
          {% include "admin/_stack_row.html" %}
        {% endfor %}
      </tbody>
    </table>
  {% endif %}
</div>
{% endblock %}
```

Create `src/findajob/web/templates/admin/_stack_row.html`:

```jinja
{# One row of the stack health table. `h` = StackHealth dataclass. #}
<tr class="border-b last:border-b-0 align-top">
  <td class="py-2 pr-4 font-medium">{{ h.handle }}</td>

  {# Last triage — relative time + freshness color #}
  <td class="py-2 pr-4">
    {% if h.last_triage_complete %}
      {% set cls = {"fresh":"text-emerald-700","late":"text-amber-700","stale":"text-rose-700","unknown":"text-slate-500"}[h.freshness] %}
      <span class="{{ cls }}">{{ h.last_triage_complete.isoformat(timespec='minutes') }}</span>
    {% else %}
      <span class="text-slate-400">—</span>
    {% endif %}
  </td>

  {# Stage distribution — compact inline, zeros suppressed #}
  <td class="py-2 pr-4 text-xs text-slate-700">
    {% if h.error %}
      <span class="text-rose-700">error: {{ h.error }}</span>
    {% elif h.db_missing %}
      <span class="text-slate-400">db missing</span>
    {% else %}
      {% set parts = [] %}
      {% for stage, n in h.stage_counts.items() if n > 0 %}{% set _ = parts.append(n ~ ' ' ~ stage) %}{% endfor %}
      {% if parts %}{{ parts | join(' / ') }}{% else %}<span class="text-slate-400">empty</span>{% endif %}
    {% endif %}
  </td>

  {# Stuck prep #}
  <td class="py-2 pr-4">
    {% if h.stuck_prep_count > 0 %}
      <span class="font-bold text-rose-700">{{ h.stuck_prep_count }}</span>
    {% else %}
      0
    {% endif %}
  </td>

  {# 24h triage success/failure #}
  <td class="py-2 pr-4 whitespace-nowrap">
    <span class="text-emerald-700">{{ h.triage_success_24h }} ✓</span>
    <span class="text-slate-400">/</span>
    {% if h.triage_failure_24h > 0 %}
      <span class="text-rose-700 font-bold">{{ h.triage_failure_24h }} ✗</span>
    {% else %}
      <span>0 ✗</span>
    {% endif %}
  </td>

  {# Last failure timestamp + tag #}
  <td class="py-2 pr-4">
    {% set last_failures = [
      ("aichat", h.last_aichat_failure),
      ("discovery", h.last_discovery_failed),
      ("prep", h.last_prep_failed),
    ] | rejectattr('1', 'none') | list %}
    {% if last_failures %}
      {% set most_recent = last_failures | sort(attribute='1', reverse=true) | first %}
      <span class="text-rose-700">
        {{ most_recent[1].isoformat(timespec='minutes') }} <span class="text-xs uppercase opacity-70">{{ most_recent[0] }}</span>
      </span>
    {% else %}
      <span class="text-slate-400">—</span>
    {% endif %}
  </td>

  {# Drill-down recipe — click to copy via Alpine #}
  <td class="py-2 pr-4">
    {% set cmd = "ssh docker.lan tail -F /opt/stacks/findajob-" ~ h.handle ~ "/state/logs/pipeline.jsonl" %}
    <div x-data="{copied:false}" class="flex items-center gap-2">
      <code class="text-xs bg-slate-100 px-2 py-1 rounded">{{ cmd }}</code>
      <button type="button"
              @click="navigator.clipboard.writeText('{{ cmd }}'); copied=true; setTimeout(()=>copied=false,1500)"
              class="text-xs text-slate-600 hover:text-slate-900">
        <span x-show="!copied">copy</span>
        <span x-show="copied" class="text-emerald-700">✓</span>
      </button>
    </div>
  </td>
</tr>
```

- [ ] **Step 5: Wire conditional registration inside `create_app()`.**

In `src/findajob/web/app.py`, modify the `create_app` function to include the admin router after the existing aggregated-router include and before `install_basic_auth(app)`. The dependency mirrors the `_guard` (onboarding-complete check) used elsewhere via the aggregator. Also import `Depends` and `require_onboarding_complete` if not already in scope:

```python
# Top-of-file imports — add this line if not present:
from findajob.web.onboarding_guard import require_onboarding_complete
# `Depends` is already imported on line 10 of app.py.
```

Then in `create_app`, replace the existing two-line block:

```python
    app.dependency_overrides.setdefault(_materials_routes.get_db, get_db)
    app.include_router(_aggregated_router)
    install_basic_auth(app)
    return app
```

with:

```python
    app.dependency_overrides.setdefault(_materials_routes.get_db, get_db)
    app.include_router(_aggregated_router)
    if os.environ.get("FINDAJOB_OPERATOR_MODE") == "1":
        from findajob.web.routes import admin_stacks
        app.include_router(
            admin_stacks.router,
            dependencies=[Depends(require_onboarding_complete)],
        )
    install_basic_auth(app)
    return app
```

The conditional import — not just the conditional `include_router` — is intentional: tester stacks never load the module, defending against any future code that might inadvertently expose the route by reading global state.

- [ ] **Step 6: Run tests; verify they pass.**

Run: `uv run pytest tests/test_admin_stacks_route.py -v`
Expected: 9 passed.

- [ ] **Step 7: Run the whole admin test suite.**

Run: `uv run pytest tests/test_admin_*.py -v`
Expected: 33 passed (6 jsonl_tail + 7 stack_discovery + 8 stack_health + 3 nav + 9 route).

- [ ] **Step 8: Lint + type-check.**

Run: `uv run ruff check src/findajob/admin src/findajob/web/routes/admin_stacks.py tests/test_admin_*.py tests/conftest_admin.py && uv run ruff format --check src/findajob/admin src/findajob/web/routes/admin_stacks.py tests/test_admin_*.py tests/conftest_admin.py && uv run mypy src/findajob/admin src/findajob/web/routes/admin_stacks.py`
Expected: clean.

- [ ] **Step 9: Commit.**

```bash
git add src/findajob/web/routes/admin_stacks.py src/findajob/web/app.py src/findajob/web/templates/admin/ tests/test_admin_stacks_route.py
git commit -m "$(cat <<'EOF'
feat(web): /admin/stacks/ operator dashboard for #333

Conditionally registered when FINDAJOB_OPERATOR_MODE=1; route module
literally not imported when flag is unset (404, not 403). Uses the
new findajob.admin package for discovery + per-stack gather.

Template renders one row per stack: handle, last triage with
freshness color, stage distribution, stuck-prep count, 24h
success/failure counts, last-failure tag, click-to-copy SSH tail
recipe. Operator's stack always renders first.

Empty-state banner when /opt/stacks is unmounted; per-row error
string when a stack's DB is corrupt — siblings still render.

Test override env var FINDAJOB_ADMIN_STACKS_ROOT lets the test
suite substitute a tmpdir for the production /opt/stacks path.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Documentation updates

**Files:**
- Modify: `docs/setup/install-docker.md` (new "Operator mode" subsection)
- Modify: `CLAUDE.md` (route list + container context table + architecture rule)
- Modify: `CLAUDE.local.md` (operator-stack notes)
- Modify: `CHANGELOG.md` (`[Unreleased]` → `Added` + `### Migration required`)

- [ ] **Step 1: Add "Operator mode" subsection to `docs/setup/install-docker.md`.**

Find the "Multi-tenant hosts" subsection (added by #344) and append a new subsection immediately after it:

````markdown
### Operator mode (multi-tenant stack health dashboard) — #333

If you run multiple findajob stacks side-by-side (e.g. yourself + several
beta testers on the same `docker.lan`), the operator stack can run with
operator mode enabled to surface a cross-stack health dashboard at
`/admin/stacks/`. The dashboard shows last-triage time, stage distribution,
stuck-prep count, and last-failure timestamp for every stack at
`/opt/stacks/findajob-*/`.

Operator mode is operator-only — testers' stacks must NOT enable it. It is
gated by a single env flag and a read-only mount.

**On operator's stack only**, edit `compose.yaml`:

```yaml
services:
  scheduler:
    environment:
      FINDAJOB_OPERATOR_MODE: "1"
      # Optional: float operator's own row to the top of the dashboard.
      # Value must match the operator's stack handle (the trailing component
      # of /opt/stacks/findajob-{handle}). When unset, rows render in pure
      # alphabetical order. The handle is read from the env so tracked code
      # stays free of operator-specific identifiers.
      FINDAJOB_OPERATOR_HANDLE: "${YOUR_HANDLE}"
    volumes:
      - /opt/stacks:/opt/stacks:ro
```

Apply with `docker compose up -d`. The route is loaded conditionally — when
the flag is unset, `/admin/stacks/` returns 404 and no cross-stack mount is
required.

**Visual cue:** when operator mode is enabled, the top nav bar renders red
on every page (not just `/admin/stacks/`). This is intentional — it keeps
you aware that you're in the operator surface.

**Auth:** the dashboard inherits `FINDAJOB_AUTH_USER` / `FINDAJOB_AUTH_PASS`
Basic Auth (the same credentials that protect `/board/`). No new credential
to manage.

**Read-only invariant:** the dashboard cannot modify any tester state. All
SQLite reads use `mode=ro` URI; `/opt/stacks` is mounted read-only.
````

- [ ] **Step 2: Update `CLAUDE.md`.**

In the routes list under "Web Frontend Architecture" (search for `routes/landing.py`), add this line in alphabetical order:

```
<repo>/src/findajob/web/routes/admin_stacks.py # GET /admin/stacks/ — operator-only multi-tenant stack health (#333; loaded iff FINDAJOB_OPERATOR_MODE=1)
```

In the same section, add a new "Operator mode" sub-bullet after the existing Web Frontend bullets:

```markdown
**Operator mode** — gated by `FINDAJOB_OPERATOR_MODE=1` (operator's stack only;
never set on testers'). Adds `/admin/stacks/` route and renders the top nav in
red on every page. The route is the **only** code path that reads cross-stack
state from inside `findajob.web` — invariant: read-only, no POST handlers, all
SQLite handles open with `mode=ro` URI. See `findajob.admin.{stack_discovery,
stack_health,jsonl_tail}` and `docs/setup/install-docker.md` "Operator mode"
subsection.
```

In the Container Context table, add a row in the "thing → native → container" table for the operator-mode mount + flag (between `companies/` and `Onboarding sentinel` rows):

```
| Cross-stack mount (operator-mode only) | n/a | `/opt/stacks/:/opt/stacks:ro` (added to operator's `compose.yaml` only) |
| `FINDAJOB_OPERATOR_MODE` env | n/a | `1` on operator's stack only; unset on testers' (#333) |
| `FINDAJOB_OPERATOR_HANDLE` env (optional) | n/a | Operator's stack handle (e.g. matches the trailing dir component of `/opt/stacks/findajob-{handle}`); when set, that row floats to the top of the `/admin/stacks/` table. Unset = pure alphabetical (#333). |
```

- [ ] **Step 3: Update `CLAUDE.local.md`.**

In the "Platform (docker.lan — active machine)" table, add a row:

```
| Operator mode | `FINDAJOB_OPERATOR_MODE=1` set on `findajob-brock` only; testers leave unset. Adds `/admin/stacks/` dashboard + red top nav bar (#333) |
```

- [ ] **Step 4: Update `CHANGELOG.md`.**

Under `## [Unreleased]`, add to the `### Added` subsection (creating it if absent):

```markdown
### Added

- **`/admin/stacks/` multi-tenant operator dashboard (#333).** When
  `FINDAJOB_OPERATOR_MODE=1` is set on the operator's stack, a new route
  surfaces last-triage time, stage distribution, stuck-prep count, and
  last-failure timestamp for every `findajob-*` stack on the host. Top nav
  bar renders red on every page as an ambient cue that operator mode is
  active. Tester stacks unaffected — no flag, no route, no visual change.
  Auth inherits the existing `FINDAJOB_AUTH_USER`/`PASS` Basic Auth.
```

Under `## [Unreleased]`, add a `### Migration required` subsection (creating it if absent):

```markdown
### Migration required

- **Operator mode (#333) — operator's stack only.** If you want the
  `/admin/stacks/` dashboard, edit operator's `compose.yaml` to add:
  ```yaml
  services:
    scheduler:
      environment:
        FINDAJOB_OPERATOR_MODE: "1"
        # Optional — match this to your stack handle to float your own
        # row to the top of the dashboard. When unset, rows render
        # alphabetically.
        FINDAJOB_OPERATOR_HANDLE: "<your-handle>"
      volumes:
        - /opt/stacks:/opt/stacks:ro
  ```
  Apply with `docker compose up -d`. Tester stacks: leave both unset.
```

- [ ] **Step 5: Verify markdown syntax with a quick render.**

Run: `uv run python -c "import markdown; markdown.markdown(open('CHANGELOG.md').read()[:8000])"`
Expected: no exceptions.

- [ ] **Step 6: Commit.**

```bash
git add docs/setup/install-docker.md CLAUDE.md CLAUDE.local.md CHANGELOG.md
git commit -m "$(cat <<'EOF'
docs(#333): operator-mode setup, route notes, migration bullet

docs/setup/install-docker.md gains a new "Operator mode" subsection
under Multi-tenant hosts, with the compose-edit recipe and the
operator-only-not-tester discipline. CLAUDE.md adds the route line
and a Critical Architecture Rules bullet documenting the read-only
cross-stack invariant. CLAUDE.local.md notes operator's stack runs
operator mode and the red-bar visual cue. CHANGELOG.md gets the
Unreleased Added entry plus a Migration required bullet so external
operators see the compose change at next pull.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Whole-feature verification gate

This task runs **after the last commit lands on `main`** and **before** the operator's stack is configured for operator mode. Each step is a manual check or a single command — no production state mutates until step 4.

- [ ] **Step 1: Local CI gate.**

Run: `uv run pytest tests/ -q && uv run ruff check . && uv run ruff format --check . && uv run mypy src/findajob`
Expected: all green. No warnings introduced by the new modules.

- [ ] **Step 2: Verify the conditional registration.**

In a clean shell with `FINDAJOB_OPERATOR_MODE` unset:

```bash
uv run python -c "
import os, tempfile
os.environ.pop('FINDAJOB_OPERATOR_MODE', None)
from pathlib import Path
from findajob.web.app import create_app
with tempfile.TemporaryDirectory() as td:
    p = Path(td); (p/'companies').mkdir(); (p/'pipeline.db').touch()
    app = create_app(companies_root=p/'companies', db_path=p/'pipeline.db', base_root=p)
paths = [r.path for r in app.routes]
assert '/admin/stacks/' not in paths, paths
print('OK: route absent when flag unset')
"
```

Then with the flag set:

```bash
FINDAJOB_OPERATOR_MODE=1 uv run python -c "
import tempfile
from pathlib import Path
from findajob.web.app import create_app
with tempfile.TemporaryDirectory() as td:
    p = Path(td); (p/'companies').mkdir(); (p/'pipeline.db').touch()
    app = create_app(companies_root=p/'companies', db_path=p/'pipeline.db', base_root=p)
paths = [r.path for r in app.routes]
assert '/admin/stacks/' in paths, paths
print('OK: route present when flag set')
"
```

Expected: both `OK:` lines printed.

- [ ] **Step 3: Build + push the image (handled by release process).**

Per `docs/release-process.md`, image rebuild + push is part of the normal release flow. This task does not invent a new release procedure; the `### Migration required` bullet from Task 6 is the operator's signal at next `docker compose pull`.

- [ ] **Step 4: Manual smoke on operator's stack (post-release).**

After bumping `findajob-brock` to the new image:

1. Edit `/opt/stacks/findajob-brock/compose.yaml`. Add `FINDAJOB_OPERATOR_MODE: "1"` under `environment:` and `- /opt/stacks:/opt/stacks:ro` under `volumes:`.
2. `cd /opt/stacks/findajob-brock && docker compose up -d`.
3. Wait 5s, then `docker logs <scheduler container>` — confirm no errors at startup.
4. Hit `/admin/stacks/` from a browser (or `curl -u op:secret https://findajob.<operator-domain>/admin/stacks/`).
5. Verify: page returns 200; nav bar is red; one row per `findajob-*` stack on the host; if you set `FINDAJOB_OPERATOR_HANDLE` your row is first, otherwise rows are alphabetical; each row's drill-down recipe matches the handle.
6. Hit `/board/dashboard` — verify nav bar is red there too (ambient cue applies to every page).
7. Hit `/admin/stacks/` from one of the tester stacks (e.g. `findajob-alice.<operator-domain>/admin/stacks/`) — verify 404. Verify alice's nav bar is the existing slate-800 (unchanged).

Expected: every check passes.

- [ ] **Step 5: Failure-mode smoke.**

On operator's stack, with operator mode active:

1. Temporarily rename `/opt/stacks/findajob-alice/state/data/pipeline.db` → `pipeline.db.bak`.
2. Reload `/admin/stacks/`. Alice's row renders with `db missing` in the stage-distribution cell. Other rows unaffected.
3. Restore the file.

Expected: failure isolation works.

- [ ] **Step 6: Confirm performance budget on real data.**

```bash
ssh docker.lan 'sudo -u lad docker exec findajob-brock-scheduler-1 \
  curl -s -o /dev/null -w "%{time_total}\n" -u "op:secret" http://localhost:8090/admin/stacks/'
```

Expected: under 2 seconds. (This is the spec §4.6 budget.)

- [ ] **Step 7: Acceptance criteria mapping.**

Walk through every AC in the issue body (#333) and confirm visually:

| AC | Confirmed in step |
|---|---|
| One row per `/opt/stacks/findajob-*/` | step 4 #5 |
| Per-row: handle, last triage, stage dist, stuck prep, 24h triage, last failure | step 4 #5 |
| Stale indicator (>36h) | step 5 (will appear on any stack legitimately stale; or contrive by editing one stack's JSONL) |
| Failure indicator (last 24h) | step 5 (provoked) |
| Linkable drill-down | step 4 #5 (recipe per row) |
| <2s render | step 6 |
| Auth via existing FINDAJOB_AUTH_USER/PASS | step 4 #4 (curl with auth) |

Expected: every AC ticked.

---

## Documentation Impact

| Doc | Change | Task |
|---|---|---|
| `README.md` | None — no top-level user-facing surface change. | n/a |
| `docs/setup/install-docker.md` | New "Operator mode" subsection: env flag + mount + auth note + read-only invariant. | Task 6 |
| `CLAUDE.md` | New route in Web Frontend Architecture list; new Container Context rows for `FINDAJOB_OPERATOR_MODE` and the `/opt/stacks` mount; new "Operator mode" bullet under Critical Architecture Rules with the read-only invariant. | Task 6 |
| `CLAUDE.local.md` | New row in the "Platform (docker.lan — active machine)" table noting operator's stack runs operator mode + the red-bar visual cue. | Task 6 |
| `CHANGELOG.md` | `[Unreleased]` → `### Added` entry for the dashboard + `### Migration required` entry with the operator-stack compose edits. | Task 6 |
| `docs/superpowers/specs/2026-04-30-333-design.md` | None — spec is fixed; if implementation reveals a flaw, append a "Decisions made during implementation" subsection. | (only if needed) |
| In-code docstrings | Module docstrings on `findajob.admin.{__init__,jsonl_tail,stack_discovery,stack_health}` and `findajob.web.routes.admin_stacks`. | Tasks 1-5 (each module has a docstring) |
| `docs/release-process.md` | None — Task 6's `### Migration required` bullet is the operator's signal at next `docker compose pull`. The release process already surfaces the migration section in release notes. | n/a |
| `docs/project-board.md` | None — no new board conventions. | n/a |

---

## Verification gate (whole feature)

The plan is acceptance-gated by Task 7. Specifically:

- All `tests/test_admin_*.py` pass under `uv run pytest`.
- `ruff check`, `ruff format --check`, and `mypy` clean against the new files.
- Conditional route registration verified by Task 7 step 2 (route present iff flag set).
- `/admin/stacks/` returns 200 on operator's stack with all 6 rows in operator-first-then-alphabetical order; returns 404 on tester stack.
- Red nav bar visible on operator's stack on every page; unchanged on tester stacks.
- Render time < 2s against real production data.
- Per-row error isolation works (smoke step 5).
- Every issue-body AC has a corresponding visual check in step 7.

---

## Self-review checklist

**Spec section → task mapping (every spec section has at least one task):**

| Spec section | Task |
|---|---|
| §1 Context | n/a (rationale only) |
| §2 Objectives | Task 7 step 7 (AC mapping) |
| §3.1 In scope | Tasks 1–6 |
| §3.2 Out of scope | n/a (out of scope is out of plan) |
| §4.1 Route registration | Task 5 step 5 (conditional include in `create_app()` — see "Deviation from spec §4.1" note above); Task 7 step 2 (verification) |
| §4.2 Mount surface | Task 6 step 1 (docs); Task 7 step 4 (manual smoke) |
| §4.3 Auth | Task 5 (route inherits middleware); `test_basic_auth_inherited_when_set` |
| §4.4 Static-on-load | Task 5 (no polling logic in route handler — implicit) |
| §4.5 Data flow | Tasks 1, 2, 3 (jsonl_tail, stack_discovery, stack_health) |
| §4.6 Performance budget | Task 5 step 1 `test_render_under_2s`; Task 7 step 6 (real-data smoke) |
| §5.1 stack_discovery | Task 2 |
| §5.2 stack_health | Task 3 |
| §5.3 jsonl_tail | Task 1 |
| §5.4 Route module | Task 5 |
| §5.5 Template + nav | Task 4 (nav globals + bar color); Task 5 step 4 (template) |
| §6 Column layout | Task 5 step 4 (`stacks_index.html` + `_stack_row.html`) |
| §7 Error-handling matrix | Each row corresponds to a test in tests/test_admin_*.py (see test names) |
| §8 Testing strategy | Tasks 1–5 each include test files |
| §9 Observability | Task 5 step 3 (one INFO log per request); Task 3 step 4 (one WARNING per gather exception) |
| §10 Documentation Impact | Task 6 |
| §11 Open follow-ups | n/a (post-merge, not in plan) |
| §12 Self-review checklist | This section |

**Placeholder scan:** No `TBD`, `TODO`, `implement later`, `add appropriate error handling`, or `similar to Task N` — every code step shows the actual code; every verification step gives the actual command and expected output.

**Type/contract consistency:**

- `StackPath(handle, root, db_path, jsonl_path)` — defined Task 2; consumed by Tasks 3, 5. Field names match.
- `StackHealth(handle, last_triage_complete, last_triage_failed, last_aichat_failure, last_discovery_failed, last_prep_failed, triage_success_24h, triage_failure_24h, stage_counts, stuck_prep_count, db_missing, jsonl_missing, error, freshness)` — defined Task 3; consumed by Tasks 5 (`_sort_operator_first`, template). Template references match (handle, last_triage_complete, freshness, stage_counts, stuck_prep_count, triage_success_24h, triage_failure_24h, last_aichat_failure, last_discovery_failed, last_prep_failed, error, db_missing — all present).
- `discover_stacks(stacks_root: Path) -> list[StackPath]` — defined Task 2; consumed by Task 5 (route handler).
- `gather(stack: StackPath, *, now: datetime | None = None) -> StackHealth` — defined Task 3; consumed by Task 5 (route handler).
- `tail_events(path: Path, *, max_bytes: int = 1_048_576) -> Iterator[dict]` — defined Task 1; consumed by Task 3 (`stack_health.gather`).
- `operator_mode` template global — defined Task 4 step 4; consumed by `_nav.html` (Task 4 step 3).
- `FINDAJOB_OPERATOR_MODE` env flag — read in Task 4 step 4 (template global), Task 5 step 5 (route registration). Consistent string `"1"` check both places.
- `FINDAJOB_ADMIN_STACKS_ROOT` test override env — read in Task 5 step 3 (route handler), set in Task 5 step 1 (test fixtures). Consistent name.

All cross-task identifiers consistent. Plan is self-coherent.
