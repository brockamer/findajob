# /config/ Editor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship `/config/` — a web page that lists the pipeline's editable configuration files by category and lets the operator edit each one in an in-browser `<textarea>` and save back to disk. Closes GitHub issue #149 and unblocks the tuning section of #11 (user-facing docs).

**Architecture:** Plain FastAPI + Jinja templates, consistent with the existing `/materials/` and `/ingest/` routes. One router module (`src/findajob/web/routes/config.py`) exposes three endpoints (`GET /config/`, `GET /config/files/{path:path}`, `POST /config/files/{path:path}`). A pure-Python allowlist module (`src/findajob/web/config_files.py`) decides which relative paths are editable and resolves them to absolute paths under `base_root`. Writes replace file contents atomically (`tmpfile + os.replace`) so a half-written file never appears on disk. HTMX partial returns a save-result banner without a full page reload.

**Tech Stack:** FastAPI (existing), Jinja2 (existing), HTMX (existing, CDN via `base.html`), plain Python stdlib for path handling. No new dependencies.

**Execution environment:** Feature branch `feat/149-config-editor` off `origin/main` (not a worktree — this session is inline since #149 had no brainstorm step). PR flow per `CLAUDE.md` "Commit Flow" (pipeline code → PR).

---

## File Structure

**New files:**

| File | Responsibility |
|---|---|
| `src/findajob/web/config_files.py` | Allowlist definition + `is_editable(relpath) -> bool` + `resolve_editable(relpath, base_root) -> Path \| None` (returns absolute path after allowlist + traversal guards, else None). Pure Python, no FastAPI import. |
| `src/findajob/web/routes/config.py` | Three endpoints: `GET /config/` (index), `GET /config/files/{path:path}` (editor view), `POST /config/files/{path:path}` (save handler). Uses `config_files` module; no DB access. |
| `src/findajob/web/templates/config/index.html` | Lists editable files grouped by category ("Candidate context", "Search config", "Role prompts"). Each link goes to the editor page. Extends `base.html`. |
| `src/findajob/web/templates/config/editor.html` | Editor view: shows file relative path + `<textarea>` pre-filled with current content + save button. HTMX-submits to `POST /config/files/{path}` and swaps `#save-result` with the returned partial. |
| `src/findajob/web/templates/config/_save_result.html` | HTMX partial: green "Saved." or red "Error: …" banner with `data-outcome="success"` / `data-outcome="error"` attrs for tests. |
| `src/findajob/web/templates/tools/index.html` | Minimal `/tools/` stub with one link ("Edit config files → /config/") so the #149 AC "editor is linked from /tools/" is satisfied. Scope-limited — other `/tools/` features are follow-up work. |
| `tests/test_web_config_files_allowlist.py` | Unit tests for `config_files` module (pure-Python, no TestClient). |
| `tests/test_web_config_editor.py` | Integration tests for the three endpoints using FastAPI's TestClient. |

**Modified files:**

| File | Change |
|---|---|
| `src/findajob/web/app.py` | Accept optional `base_root: Path` kwarg (default derived from `findajob.paths.BASE`); store on `app.state.base_root`. Update `default_app()` to pass `Path(os.environ.get("JSP_BASE", "/app"))`. |
| `src/findajob/web/routes/__init__.py` | Add `from findajob.web.routes import config` and `router.include_router(config.router)`. Also `router.include_router(tools.router)` for the /tools/ stub. |
| `src/findajob/web/routes/landing.py` | Remove `("/config/", …)` and `("/tools/", …)` entries from `_PLACEHOLDERS` (both are now real routes). |
| `src/findajob/web/routes/tools.py` | New file: one-handler stub rendering `tools/index.html`. |
| `CLAUDE.md` | Add `src/findajob/web/routes/config.py` and `src/findajob/web/config_files.py` to "Key File Locations"; note `/config/` in "Web Frontend Architecture" as the config editor. |
| `CHANGELOG.md` | Add Unreleased/Added bullet for `/config/`. |

**Allowlist (frozen by this plan):**

```
candidate_context/profile.md
candidate_context/master_resume.md
config/prefilter_rules.yaml
config/in_domain_patterns.yaml
config/jsearch_queries.txt
config/feed_urls.txt
config/roles/*.md    # glob — resolves to 10 role files as of 2026-04-23
```

**Design decisions already settled (from issue body):**

- Plain `<textarea>`, not Monaco/CodeMirror.
- No auth (Wireguard-perimeter security model; consistent with rest of UI).
- Explicit allowlist, not open-ended editing.
- POST writes back via the same URL (`/config/files/{path}` not `/config/save/{path}`).

**Design decisions added by this plan:**

- **Missing files behave as empty.** A `GET /config/files/candidate_context/profile.md` on a stack where `profile.md` doesn't yet exist returns the editor with an empty textarea, not a 404. This keeps the editor usable before #148's NUX has run. POST creates the file if absent (parent dir is guaranteed to exist since `config/` and `candidate_context/` are tracked).
- **Atomic writes.** Write to `path + ".tmp"` then `os.replace()`. Protects against half-written files if uvicorn crashes mid-save.
- **UTF-8 only, no newline translation.** Read/write with `encoding="utf-8"`, `newline=""` on open-for-write so Windows clients don't inject `\r\n`.
- **Relative-path URL.** `GET /config/files/candidate_context/profile.md` — FastAPI's `{path:path}` converter accepts slashes. The allowlist validates the full relative string; no component-by-component recombination.
- **No versioning, no diffing.** Out of scope — the intent is a simple editor, not a CMS. File history lives in git if the operator chooses to commit `state/config/` (they may not).

---

## Task 1 — Allowlist module (pure-Python unit)

**Files:**
- Create: `src/findajob/web/config_files.py`
- Test: `tests/test_web_config_files_allowlist.py`

- [ ] **Step 1: Write the failing unit tests**

Create `tests/test_web_config_files_allowlist.py`:

```python
"""Unit tests for the /config/ editor allowlist module."""

from __future__ import annotations

from pathlib import Path

import pytest

from findajob.web.config_files import (
    EDITABLE_CATEGORIES,
    is_editable,
    list_editable,
    resolve_editable,
)


# ---- is_editable ----------------------------------------------------------

@pytest.mark.parametrize(
    "relpath",
    [
        "candidate_context/profile.md",
        "candidate_context/master_resume.md",
        "config/prefilter_rules.yaml",
        "config/in_domain_patterns.yaml",
        "config/jsearch_queries.txt",
        "config/feed_urls.txt",
        "config/roles/job_scorer.md",
        "config/roles/cover_letter_writer.md",
        "config/roles/onboarding_interviewer.md",
    ],
)
def test_is_editable_allows_whitelisted(relpath: str) -> None:
    assert is_editable(relpath) is True


@pytest.mark.parametrize(
    "relpath",
    [
        "",
        "/",
        "config",
        "config/",
        "config/roles",
        "config/roles/",
        "config/roles/anything.txt",           # wrong extension under roles/
        "config/roles/nested/file.md",         # no subdir recursion
        "config/other.yaml",                   # not in flat allowlist
        "config/roles.md",                     # not under roles/
        "candidate_context/voice_samples/a.md",  # voice_samples not editable
        "data/pipeline.db",
        "secrets.env",
    ],
)
def test_is_editable_rejects_unlisted(relpath: str) -> None:
    assert is_editable(relpath) is False


@pytest.mark.parametrize(
    "relpath",
    [
        "../etc/passwd",
        "config/../secrets.env",
        "config/roles/../../etc/passwd",
        "config/roles/./job_scorer.md",        # dot components rejected
        "/etc/passwd",                          # absolute path rejected
        "config/roles/job_scorer.md/..",       # trailing traversal
    ],
)
def test_is_editable_rejects_traversal(relpath: str) -> None:
    assert is_editable(relpath) is False


# ---- resolve_editable -----------------------------------------------------

def test_resolve_editable_returns_absolute_path(tmp_path: Path) -> None:
    target = tmp_path / "config" / "roles" / "job_scorer.md"
    target.parent.mkdir(parents=True)
    target.write_text("original content")

    resolved = resolve_editable("config/roles/job_scorer.md", tmp_path)

    assert resolved == target.resolve()


def test_resolve_editable_returns_none_for_unlisted(tmp_path: Path) -> None:
    assert resolve_editable("config/random.txt", tmp_path) is None


def test_resolve_editable_returns_none_for_traversal(tmp_path: Path) -> None:
    assert resolve_editable("../etc/passwd", tmp_path) is None


def test_resolve_editable_returns_path_even_if_file_missing(tmp_path: Path) -> None:
    # Allowlisted but not yet created on disk — still resolves, caller handles
    # the missing-file case (GET renders empty, POST creates).
    resolved = resolve_editable("candidate_context/profile.md", tmp_path)

    assert resolved == (tmp_path / "candidate_context" / "profile.md").resolve()


def test_resolve_editable_blocks_symlink_escape(tmp_path: Path) -> None:
    # An allowlisted path that, on disk, symlinks out of base_root must be rejected.
    outside = tmp_path.parent / "outside.md"
    outside.write_text("leaked")
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "roles").mkdir()
    (tmp_path / "config" / "roles" / "job_scorer.md").symlink_to(outside)

    assert resolve_editable("config/roles/job_scorer.md", tmp_path) is None


# ---- list_editable --------------------------------------------------------

def test_list_editable_groups_by_category(tmp_path: Path) -> None:
    (tmp_path / "candidate_context").mkdir()
    (tmp_path / "candidate_context" / "profile.md").write_text("x")
    (tmp_path / "candidate_context" / "master_resume.md").write_text("x")
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "prefilter_rules.yaml").write_text("x")
    (tmp_path / "config" / "roles").mkdir()
    (tmp_path / "config" / "roles" / "job_scorer.md").write_text("x")
    (tmp_path / "config" / "roles" / "cover_letter_writer.md").write_text("x")

    groups = list_editable(tmp_path)

    names = [g["name"] for g in groups]
    assert names == ["Candidate context", "Search config", "Role prompts"]

    candidate = next(g for g in groups if g["name"] == "Candidate context")
    candidate_paths = [f["relpath"] for f in candidate["files"]]
    assert "candidate_context/profile.md" in candidate_paths
    assert "candidate_context/master_resume.md" in candidate_paths

    roles = next(g for g in groups if g["name"] == "Role prompts")
    role_paths = sorted(f["relpath"] for f in roles["files"])
    assert role_paths == [
        "config/roles/cover_letter_writer.md",
        "config/roles/job_scorer.md",
    ]


def test_list_editable_flags_missing_files(tmp_path: Path) -> None:
    # An allowlisted file that doesn't exist on disk shows up with exists=False.
    groups = list_editable(tmp_path)

    candidate = next(g for g in groups if g["name"] == "Candidate context")
    profile = next(f for f in candidate["files"] if f["relpath"] == "candidate_context/profile.md")
    assert profile["exists"] is False


def test_editable_categories_constant_shape() -> None:
    assert set(EDITABLE_CATEGORIES.keys()) == {"Candidate context", "Search config", "Role prompts"}
    assert "candidate_context/profile.md" in EDITABLE_CATEGORIES["Candidate context"]
    assert "config/jsearch_queries.txt" in EDITABLE_CATEGORIES["Search config"]
    assert EDITABLE_CATEGORIES["Role prompts"] == "config/roles/*.md"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_web_config_files_allowlist.py -v`
Expected: all tests FAIL with `ModuleNotFoundError: No module named 'findajob.web.config_files'`.

- [ ] **Step 3: Implement the allowlist module**

Create `src/findajob/web/config_files.py`:

```python
"""Allowlist for the /config/ editor.

The editor may read and write only the files named in :data:`EDITABLE_CATEGORIES`
plus any ``config/roles/*.md`` file. Path-traversal guards reject any relpath
with a dot component, a leading slash, or a resolved absolute path outside
``base_root``.

The module has no FastAPI import — it is a pure function-level API so the
allowlist can be unit-tested and reused from other surfaces (CLI tools,
future materials editor).
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath

# Fixed allowlist: exact relative POSIX paths the editor may touch.
# Anything under ``config/roles/`` matching ``*.md`` is additionally allowed
# via :func:`_is_role_file`.
EDITABLE_CATEGORIES: dict[str, list[str] | str] = {
    "Candidate context": [
        "candidate_context/profile.md",
        "candidate_context/master_resume.md",
    ],
    "Search config": [
        "config/prefilter_rules.yaml",
        "config/in_domain_patterns.yaml",
        "config/jsearch_queries.txt",
        "config/feed_urls.txt",
    ],
    "Role prompts": "config/roles/*.md",
}

_FLAT_ALLOWLIST: frozenset[str] = frozenset(
    p
    for value in EDITABLE_CATEGORIES.values()
    if isinstance(value, list)
    for p in value
)

_ROLES_DIR = "config/roles"


def _is_role_file(relpath: str) -> bool:
    """True iff ``relpath`` is a direct ``.md`` child of ``config/roles/``.

    Direct child only — subdirectories are not allowed ("config/roles/a/b.md"
    returns False).
    """
    p = PurePosixPath(relpath)
    if p.suffix != ".md":
        return False
    if str(p.parent) != _ROLES_DIR:
        return False
    # No dot components, no empty name.
    parts = p.parts
    if any(part in ("", ".", "..") for part in parts):
        return False
    return True


def is_editable(relpath: str) -> bool:
    """True iff ``relpath`` is on the editable allowlist.

    Rejects: absolute paths, empty string, paths with dot or parent-ref
    components, paths not in the flat allowlist and not a role file.
    """
    if not relpath or relpath.startswith("/"):
        return False
    # PurePosixPath normalizes ``a/./b`` to ``a/b`` and ``a/b/..`` to ``a`` —
    # we want to reject, not normalize. Check parts first.
    parts = relpath.split("/")
    if any(part in ("", ".", "..") for part in parts):
        return False
    return relpath in _FLAT_ALLOWLIST or _is_role_file(relpath)


def resolve_editable(relpath: str, base_root: Path) -> Path | None:
    """Return the absolute :class:`Path` for ``relpath`` or ``None`` if rejected.

    Runs :func:`is_editable` first, then resolves symlinks and verifies the
    final absolute path is still under ``base_root``. Returns the path even
    if the file does not yet exist — callers handle the missing case.
    """
    if not is_editable(relpath):
        return None

    base_resolved = base_root.resolve()
    candidate = (base_root / relpath).resolve()

    try:
        candidate.relative_to(base_resolved)
    except ValueError:
        return None

    return candidate


def list_editable(base_root: Path) -> list[dict]:
    """Enumerate the allowlist for the index page.

    Returns a list of category dicts, each ``{"name": str, "files": [...]}``.
    Each file dict is ``{"relpath": str, "exists": bool}``. Role-prompt files
    are discovered from the filesystem (glob) so new role files appear
    automatically; the other two categories use the fixed allowlist.
    """
    categories: list[dict] = []

    for name, value in EDITABLE_CATEGORIES.items():
        if isinstance(value, list):
            files = [
                {"relpath": p, "exists": (base_root / p).is_file()}
                for p in sorted(value)
            ]
        else:
            # Role prompts — glob the directory.
            roles_dir = base_root / _ROLES_DIR
            role_files: list[dict] = []
            if roles_dir.is_dir():
                for child in sorted(roles_dir.iterdir()):
                    if child.is_file() and child.suffix == ".md":
                        role_files.append(
                            {
                                "relpath": f"{_ROLES_DIR}/{child.name}",
                                "exists": True,
                            }
                        )
            files = role_files
        categories.append({"name": name, "files": files})

    return categories
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_web_config_files_allowlist.py -v`
Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/findajob/web/config_files.py tests/test_web_config_files_allowlist.py
git commit -m "feat(web/config): allowlist module for /config/ editor (#149)"
```

---

## Task 2 — `GET /config/` index page

**Files:**
- Create: `src/findajob/web/routes/config.py`
- Create: `src/findajob/web/templates/config/index.html`
- Modify: `src/findajob/web/app.py` (add `base_root` kwarg + state)
- Modify: `src/findajob/web/routes/__init__.py` (register router)
- Test: `tests/test_web_config_editor.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_web_config_editor.py`:

```python
"""Integration tests for the /config/ editor web routes (#149)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from findajob.web.app import create_app

_MINIMAL_SCHEMA = """
CREATE TABLE jobs (
    id TEXT PRIMARY KEY,
    fingerprint TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL,
    company TEXT NOT NULL,
    stage TEXT DEFAULT 'discovered',
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL,
    field_changed TEXT NOT NULL,
    old_value TEXT,
    new_value TEXT,
    changed_at TEXT DEFAULT (datetime('now'))
);
"""


@pytest.fixture()
def base_root(tmp_path: Path) -> Path:
    """Populate a realistic subset of the allowlist on disk."""
    (tmp_path / "candidate_context").mkdir()
    (tmp_path / "candidate_context" / "profile.md").write_text("# Profile\nHello.\n")
    # master_resume.md intentionally omitted — tests the missing-file case.

    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "jsearch_queries.txt").write_text("site reliability engineer\n")
    (tmp_path / "config" / "feed_urls.txt").write_text("acme\nexample-corp\n")

    (tmp_path / "config" / "roles").mkdir()
    (tmp_path / "config" / "roles" / "job_scorer.md").write_text("# Scorer role\n")
    (tmp_path / "config" / "roles" / "cover_letter_writer.md").write_text("# CL role\n")

    return tmp_path


@pytest.fixture()
def client(base_root: Path, tmp_path: Path) -> TestClient:
    db_path = tmp_path / "pipeline.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(_MINIMAL_SCHEMA)
    conn.close()

    companies = tmp_path / "companies"
    companies.mkdir()

    app = create_app(
        companies_root=companies,
        db_path=db_path,
        base_root=base_root,
    )
    return TestClient(app)


def test_index_lists_files_by_category(client: TestClient) -> None:
    resp = client.get("/config/")
    assert resp.status_code == 200
    html = resp.text
    # Category headings present
    assert "Candidate context" in html
    assert "Search config" in html
    assert "Role prompts" in html
    # Files listed
    assert "candidate_context/profile.md" in html
    assert "candidate_context/master_resume.md" in html   # missing but still listed
    assert "config/jsearch_queries.txt" in html
    assert "config/roles/job_scorer.md" in html
    assert "config/roles/cover_letter_writer.md" in html
    # Editor links go to /config/files/…
    assert 'href="/config/files/candidate_context/profile.md"' in html
    # Missing file has a visible indicator
    assert "missing" in html.lower() or "not yet" in html.lower()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_web_config_editor.py::test_index_lists_files_by_category -v`
Expected: FAIL — `create_app` TypeError (no `base_root` kwarg) or 404 on `/config/`.

- [ ] **Step 3: Wire `base_root` through the app factory**

Modify `src/findajob/web/app.py`:

- In the `create_app` signature, add `base_root: Path | None = None` after `db_path`.
- After existing state assignments, add `app.state.base_root = base_root if base_root is not None else Path(os.environ.get("JSP_BASE", "/app"))`.
- In `default_app()`, pass `base_root=Path(os.environ.get("JSP_BASE", "/app"))`.

Example diff for `create_app`:

```python
def create_app(
    *,
    companies_root: Path,
    db_path: Path,
    base_root: Path | None = None,
) -> FastAPI:
    app = FastAPI(title="findajob web", docs_url=None, redoc_url=None)
    ...
    app.state.companies_root = companies_root
    app.state.db_path = db_path
    app.state.base_root = base_root if base_root is not None else Path(
        os.environ.get("JSP_BASE", "/app")
    )
    app.state.templates = templates
    ...
```

- [ ] **Step 4: Create the config router with the index endpoint**

Create `src/findajob/web/routes/config.py`:

```python
"""In-browser editor for pipeline config files (#149).

Three endpoints:

* ``GET /config/`` — index page, groups editable files by category.
* ``GET /config/files/{path:path}`` — editor view with current content in a textarea.
* ``POST /config/files/{path:path}`` — save handler, returns an HTMX result partial.

The allowlist lives in :mod:`findajob.web.config_files`.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from findajob.web.config_files import list_editable

router = APIRouter()


@router.get("/config/", response_class=HTMLResponse)
def config_index(request: Request) -> HTMLResponse:
    base_root: Path = request.app.state.base_root
    categories = list_editable(base_root)
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="config/index.html",
        context={"categories": categories},
    )
```

- [ ] **Step 5: Create the index template**

Create `src/findajob/web/templates/config/index.html`:

```html
{% extends "base.html" %}

{% block title %}Config — findajob{% endblock %}

{% block content %}
<div class="max-w-4xl mx-auto p-6">
  <h1 class="text-2xl font-semibold mb-2">Config editor</h1>
  <p class="text-sm text-gray-600 mb-6">
    Edit the files that shape scoring, resume tailoring, and search — all without SSH.
    Saved files are written straight to disk on the pipeline host.
  </p>

  {% for cat in categories %}
    <section class="mb-6">
      <h2 class="text-lg font-medium mb-2">{{ cat.name }}</h2>
      <ul class="divide-y border rounded">
        {% for file in cat.files %}
          <li class="flex items-center justify-between px-4 py-2">
            <a
              href="/config/files/{{ file.relpath }}"
              class="text-blue-600 hover:underline font-mono text-sm"
            >{{ file.relpath }}</a>
            {% if not file.exists %}
              <span class="text-xs text-gray-500 italic">missing — will be created on save</span>
            {% endif %}
          </li>
        {% else %}
          <li class="px-4 py-2 text-sm text-gray-500 italic">No files in this category yet.</li>
        {% endfor %}
      </ul>
    </section>
  {% endfor %}
</div>
{% endblock %}
```

- [ ] **Step 6: Register the router**

Modify `src/findajob/web/routes/__init__.py`:

```python
"""Aggregates all sub-module routers into a single `router` the app includes."""

from fastapi import APIRouter

from findajob.web.routes import (
    board,
    board_actions,
    config,
    healthz,
    ingest,
    landing,
    materials,
    stats,
)

router = APIRouter()
router.include_router(materials.router)
router.include_router(healthz.router)
router.include_router(landing.router)
router.include_router(board.router)
router.include_router(board_actions.router)
router.include_router(ingest.router)
router.include_router(stats.router)
router.include_router(config.router)
```

- [ ] **Step 7: Remove the `/config/` placeholder**

Modify `src/findajob/web/routes/landing.py`. In `_PLACEHOLDERS` (currently lines 45–50), delete the `/config/` tuple so it is no longer registered as a "coming soon" page. Result:

```python
_PLACEHOLDERS = [
    # /ingest/ promoted to a real route in src/findajob/web/routes/ingest.py (#62).
    # /config/ promoted to a real route in src/findajob/web/routes/config.py (#149).
    ("/tools/", "Tools", "Doctor, stats, scoreboard.", ""),
    ("/docs/", "Docs", "User-facing documentation.", ""),
]
```

- [ ] **Step 8: Run the test to verify it passes**

Run: `uv run pytest tests/test_web_config_editor.py::test_index_lists_files_by_category -v`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add src/findajob/web/app.py src/findajob/web/routes/config.py src/findajob/web/routes/__init__.py src/findajob/web/routes/landing.py src/findajob/web/templates/config/index.html tests/test_web_config_editor.py
git commit -m "feat(web/config): GET /config/ lists editable files by category (#149)"
```

---

## Task 3 — `GET /config/files/{path}` editor view

**Files:**
- Modify: `src/findajob/web/routes/config.py`
- Create: `src/findajob/web/templates/config/editor.html`
- Test: `tests/test_web_config_editor.py` (append)

- [ ] **Step 1: Append the failing tests**

Append to `tests/test_web_config_editor.py`:

```python
def test_editor_shows_existing_content(client: TestClient) -> None:
    resp = client.get("/config/files/candidate_context/profile.md")
    assert resp.status_code == 200
    html = resp.text
    assert "<textarea" in html
    assert "# Profile\nHello." in html
    # Save form posts back to same path
    assert 'hx-post="/config/files/candidate_context/profile.md"' in html
    # Breadcrumb / header shows the relpath
    assert "candidate_context/profile.md" in html


def test_editor_shows_empty_textarea_for_missing_file(client: TestClient) -> None:
    resp = client.get("/config/files/candidate_context/master_resume.md")
    assert resp.status_code == 200
    html = resp.text
    assert "<textarea" in html
    # Missing-file banner so the user understands they're creating it
    assert "does not exist" in html.lower() or "will be created" in html.lower()


def test_editor_rejects_unlisted_file(client: TestClient) -> None:
    resp = client.get("/config/files/data/pipeline.db")
    assert resp.status_code == 403


def test_editor_rejects_path_traversal(client: TestClient) -> None:
    # Note: TestClient urlencodes the request path, so ``..`` survives round-trip.
    resp = client.get("/config/files/config/../../etc/passwd")
    assert resp.status_code in (403, 404)


def test_editor_rejects_absolute_path_segment(client: TestClient) -> None:
    # FastAPI's `{path:path}` strips a leading slash off the arg, so the
    # effective relpath is "etc/passwd" — still not in allowlist → 403.
    resp = client.get("/config/files//etc/passwd")
    assert resp.status_code == 403
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_web_config_editor.py -v -k editor`
Expected: four of the five FAIL (404 on the editor route); `test_editor_rejects_unlisted_file` also fails — endpoint doesn't exist.

- [ ] **Step 3: Add the editor handler**

Append to `src/findajob/web/routes/config.py`:

```python
from fastapi import HTTPException

from findajob.web.config_files import resolve_editable


@router.get("/config/files/{relpath:path}", response_class=HTMLResponse)
def config_edit_form(relpath: str, request: Request) -> HTMLResponse:
    base_root: Path = request.app.state.base_root
    resolved = resolve_editable(relpath, base_root)
    if resolved is None:
        raise HTTPException(status_code=403, detail="file is not editable")

    content = ""
    exists = resolved.is_file()
    if exists:
        content = resolved.read_text(encoding="utf-8", errors="replace")

    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="config/editor.html",
        context={"relpath": relpath, "content": content, "exists": exists},
    )
```

- [ ] **Step 4: Create the editor template**

Create `src/findajob/web/templates/config/editor.html`:

```html
{% extends "base.html" %}

{% block title %}{{ relpath }} — findajob config{% endblock %}

{% block content %}
<div class="max-w-4xl mx-auto p-6">
  <p class="text-sm mb-2">
    <a href="/config/" class="text-blue-600 hover:underline">← all config files</a>
  </p>
  <h1 class="text-xl font-semibold font-mono mb-1">{{ relpath }}</h1>

  {% if not exists %}
    <p class="text-sm text-amber-700 bg-amber-50 border border-amber-200 rounded px-3 py-2 mb-4">
      File does not exist yet — it will be created on save.
    </p>
  {% endif %}

  <form
    hx-post="/config/files/{{ relpath }}"
    hx-target="#save-result"
    hx-swap="innerHTML"
  >
    <textarea
      name="content"
      class="w-full h-[32rem] border rounded p-3 font-mono text-sm"
      spellcheck="false"
    >{{ content }}</textarea>
    <div class="mt-3 flex items-center gap-3">
      <button
        type="submit"
        class="px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700"
      >Save</button>
      <span id="save-result" class="text-sm" aria-live="polite"></span>
    </div>
  </form>
</div>
{% endblock %}
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_web_config_editor.py -v -k editor`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add src/findajob/web/routes/config.py src/findajob/web/templates/config/editor.html tests/test_web_config_editor.py
git commit -m "feat(web/config): GET /config/files/{path} editor view (#149)"
```

---

## Task 4 — `POST /config/files/{path}` save handler

**Files:**
- Modify: `src/findajob/web/routes/config.py`
- Create: `src/findajob/web/templates/config/_save_result.html`
- Test: `tests/test_web_config_editor.py` (append)

- [ ] **Step 1: Append the failing tests**

Append to `tests/test_web_config_editor.py`:

```python
def test_save_writes_content_to_disk(client: TestClient, base_root: Path) -> None:
    new_content = "# Profile\nUpdated from the editor.\n"
    resp = client.post(
        "/config/files/candidate_context/profile.md",
        data={"content": new_content},
    )
    assert resp.status_code == 200
    assert 'data-outcome="success"' in resp.text
    on_disk = (base_root / "candidate_context" / "profile.md").read_text(encoding="utf-8")
    assert on_disk == new_content


def test_save_creates_missing_file(client: TestClient, base_root: Path) -> None:
    target = base_root / "candidate_context" / "master_resume.md"
    assert not target.exists()
    resp = client.post(
        "/config/files/candidate_context/master_resume.md",
        data={"content": "# Master resume\n"},
    )
    assert resp.status_code == 200
    assert 'data-outcome="success"' in resp.text
    assert target.read_text(encoding="utf-8") == "# Master resume\n"


def test_save_preserves_utf8_and_newlines(client: TestClient, base_root: Path) -> None:
    content = "Line 1\nLine 2\n— em-dash — α β γ\n"
    resp = client.post(
        "/config/files/config/jsearch_queries.txt",
        data={"content": content},
    )
    assert resp.status_code == 200
    assert 'data-outcome="success"' in resp.text
    on_disk = (base_root / "config" / "jsearch_queries.txt").read_bytes()
    assert on_disk.decode("utf-8") == content
    # No CRLF translation
    assert b"\r\n" not in on_disk


def test_save_rejects_unlisted_file(client: TestClient) -> None:
    resp = client.post(
        "/config/files/data/pipeline.db",
        data={"content": "anything"},
    )
    assert resp.status_code == 403


def test_save_rejects_traversal(client: TestClient) -> None:
    resp = client.post(
        "/config/files/config/../../etc/passwd",
        data={"content": "oops"},
    )
    assert resp.status_code in (403, 404)


def test_save_result_partial_has_expected_attrs(client: TestClient) -> None:
    resp = client.post(
        "/config/files/candidate_context/profile.md",
        data={"content": "# Profile\n"},
    )
    assert resp.status_code == 200
    assert 'data-outcome="success"' in resp.text
    assert "Saved" in resp.text
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_web_config_editor.py -v -k save`
Expected: all FAIL — POST route returns 405 Method Not Allowed.

- [ ] **Step 3: Add the save handler**

Append to `src/findajob/web/routes/config.py`:

```python
import os
import tempfile

from fastapi import Form


@router.post("/config/files/{relpath:path}", response_class=HTMLResponse)
def config_save(
    relpath: str,
    request: Request,
    content: str = Form(...),
) -> HTMLResponse:
    base_root: Path = request.app.state.base_root
    resolved = resolve_editable(relpath, base_root)
    if resolved is None:
        raise HTTPException(status_code=403, detail="file is not editable")

    # Atomic write: tmpfile in the same directory, then rename.
    resolved.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=resolved.name + ".",
        suffix=".tmp",
        dir=str(resolved.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(content)
        os.replace(tmp_name, resolved)
    except Exception:
        # Clean up the temp file if replace failed.
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
        raise

    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="config/_save_result.html",
        context={"outcome": "success", "message": f"Saved {relpath}."},
    )
```

- [ ] **Step 4: Create the save-result partial**

Create `src/findajob/web/templates/config/_save_result.html`:

```html
<span
  data-outcome="{{ outcome }}"
  class="text-sm {% if outcome == 'success' %}text-green-700{% else %}text-red-700{% endif %}"
>
  {% if outcome == 'success' %}✓ {% else %}✗ {% endif %}{{ message }}
</span>
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_web_config_editor.py -v -k save`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add src/findajob/web/routes/config.py src/findajob/web/templates/config/_save_result.html tests/test_web_config_editor.py
git commit -m "feat(web/config): POST /config/files/{path} saves via atomic write (#149)"
```

---

## Task 5 — `/tools/` stub with link to `/config/`

**Files:**
- Create: `src/findajob/web/routes/tools.py`
- Create: `src/findajob/web/templates/tools/index.html`
- Modify: `src/findajob/web/routes/__init__.py` (register tools router)
- Modify: `src/findajob/web/routes/landing.py` (remove /tools/ placeholder)
- Test: `tests/test_web_config_editor.py` (append)

- [ ] **Step 1: Append the failing test**

Append to `tests/test_web_config_editor.py`:

```python
def test_tools_page_links_to_config(client: TestClient) -> None:
    resp = client.get("/tools/")
    assert resp.status_code == 200
    html = resp.text
    assert 'href="/config/"' in html
    assert "Edit config files" in html or "Config editor" in html
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_web_config_editor.py::test_tools_page_links_to_config -v`
Expected: FAIL — `/tools/` is still the placeholder and does not link to `/config/`.

- [ ] **Step 3: Create the tools router stub**

Create `src/findajob/web/routes/tools.py`:

```python
"""Placeholder ``/tools/`` landing page.

Bumped from a "coming soon" placeholder to a real route so #149's AC
"editor is linked from /tools/ as the 'edit config files' action" can be
satisfied. Future tools (doctor, scoreboard, etc.) extend this template.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

router = APIRouter()


@router.get("/tools/", response_class=HTMLResponse)
def tools_index(request: Request) -> HTMLResponse:
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="tools/index.html",
        context={},
    )
```

- [ ] **Step 4: Create the tools template**

Create `src/findajob/web/templates/tools/index.html`:

```html
{% extends "base.html" %}

{% block title %}Tools — findajob{% endblock %}

{% block content %}
<div class="max-w-4xl mx-auto p-6">
  <h1 class="text-2xl font-semibold mb-4">Tools</h1>
  <ul class="divide-y border rounded">
    <li class="px-4 py-3">
      <a href="/config/" class="text-blue-600 hover:underline font-medium">Edit config files</a>
      <p class="text-sm text-gray-600">Profile, master resume, search config, role prompts.</p>
    </li>
  </ul>
  <p class="text-xs text-gray-500 mt-4">
    More tools (doctor, scoreboard, feedback inspector) land here as they ship.
  </p>
</div>
{% endblock %}
```

- [ ] **Step 5: Register the tools router**

Modify `src/findajob/web/routes/__init__.py` — add `tools` to the import and `router.include_router(tools.router)`:

```python
from findajob.web.routes import (
    board,
    board_actions,
    config,
    healthz,
    ingest,
    landing,
    materials,
    stats,
    tools,
)

router = APIRouter()
router.include_router(materials.router)
router.include_router(healthz.router)
router.include_router(landing.router)
router.include_router(board.router)
router.include_router(board_actions.router)
router.include_router(ingest.router)
router.include_router(stats.router)
router.include_router(config.router)
router.include_router(tools.router)
```

- [ ] **Step 6: Remove the `/tools/` placeholder**

Modify `src/findajob/web/routes/landing.py`. In `_PLACEHOLDERS`, delete the `/tools/` tuple so it matches:

```python
_PLACEHOLDERS = [
    # /ingest/ promoted to a real route in src/findajob/web/routes/ingest.py (#62).
    # /config/ promoted to a real route in src/findajob/web/routes/config.py (#149).
    # /tools/ promoted to a stub in src/findajob/web/routes/tools.py (#149).
    ("/docs/", "Docs", "User-facing documentation.", ""),
]
```

- [ ] **Step 7: Run the test to verify it passes**

Run: `uv run pytest tests/test_web_config_editor.py::test_tools_page_links_to_config -v`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add src/findajob/web/routes/tools.py src/findajob/web/templates/tools/index.html src/findajob/web/routes/__init__.py src/findajob/web/routes/landing.py tests/test_web_config_editor.py
git commit -m "feat(web/tools): stub /tools/ page linking to /config/ (#149)"
```

---

## Task 6 — CLAUDE.md + CHANGELOG.md

**Files:**
- Modify: `CLAUDE.md`
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Update CLAUDE.md "Key File Locations"**

In `CLAUDE.md`, find the "Web Frontend Architecture" subsection inside "Key File Locations" (currently lists `web/app.py`, `web/routes/ingest.py`, `web/routes.py`, `web/folder_resolver.py`, `web/templates/`). Add:

```
<repo>/src/findajob/web/routes/config.py      # GET /config/, GET /config/files/{path}, POST /config/files/{path} — in-browser editor (#149)
<repo>/src/findajob/web/config_files.py       # allowlist + resolve_editable() for /config/ editor (#149)
<repo>/src/findajob/web/routes/tools.py       # GET /tools/ — stub linking to /config/ (#149)
```

- [ ] **Step 2: Update CLAUDE.md "Web Frontend Architecture" prose**

Find the subsection that lists the top-nav surfaces: `/`, `/board/`, `/materials/`, `/ingest/`, `/stats/`, `/tools/`, `/config/`, `/docs/`. Leave the list unchanged (already correct). Add a one-line note under it:

```
/config/ is the in-browser editor for the pipeline's editable config files (profile,
master resume, prefilter rules, search queries, feed URLs, role prompts) with an
explicit allowlist; no auth, consistent with the Wireguard perimeter model. See
`findajob.web.config_files` for the allowlist definition.
```

- [ ] **Step 3: Update CHANGELOG.md**

In the `## [Unreleased]` / `### Added` block in `CHANGELOG.md`, insert this bullet as the first item (most recent first, consistent with existing order):

```
- **`/config/` in-browser editor — edit pipeline config files without SSH.** New top-nav page
  `/config/` lists the editable config files by category (candidate context, search config, role
  prompts) and opens each in a plain `<textarea>` with a save button. An allowlist module
  (`src/findajob/web/config_files.py`) enumerates the editable paths (`candidate_context/profile.md`,
  `candidate_context/master_resume.md`, `config/prefilter_rules.yaml`, `config/in_domain_patterns.yaml`,
  `config/jsearch_queries.txt`, `config/feed_urls.txt`, `config/roles/*.md`) — every other path
  returns 403. Writes are atomic (tmpfile + `os.replace`). Missing files render as an empty
  textarea and are created on save, so the editor works on a fresh stack before the onboarding
  flow (#148) has run. `/tools/` bumped from placeholder to a real page linking to the editor.
  Closes #149; unblocks the tuning section of #11 (user-facing docs).
```

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md CHANGELOG.md
git commit -m "docs: /config/ editor + /tools/ stub in CLAUDE.md and CHANGELOG (#149)"
```

---

## Whole-feature verification gate

Before opening the PR, run this full gate. Do not skip any step; do not declare completion without matching the expected output.

- [ ] **Gate 1: Full test suite**

Run: `uv run pytest -q`
Expected: all tests pass (existing 430-ish tests + ~20 new tests from this plan). Zero failures, zero errors.

- [ ] **Gate 2: Lint + type checks**

Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy src`
Expected: all three succeed with no errors.

Per memory "Ruff format --check alongside ruff check": CI runs both. Running only `ruff check` locally will miss the format diff and fail CI with a one-line error.

- [ ] **Gate 3: Manual UI verification (Playwright)**

Start the dev server against a tmp scratch dir so local files aren't clobbered:

```bash
export JSP_BASE=$(mktemp -d)
mkdir -p "$JSP_BASE/candidate_context" "$JSP_BASE/config/roles"
echo "# Profile" > "$JSP_BASE/candidate_context/profile.md"
cp config/roles/job_scorer.md "$JSP_BASE/config/roles/"
uv run uvicorn findajob.web.app:default_app --factory --port 8090 &
UVICORN_PID=$!
```

Then drive Playwright through the flow (`plugin_playwright_playwright__browser_*` tools):

1. `browser_navigate` → `http://localhost:8090/config/`
2. `browser_snapshot` — verify all three category headings appear, `profile.md` has no "missing" banner, `master_resume.md` shows "missing — will be created on save".
3. `browser_click` on `candidate_context/profile.md` link.
4. `browser_snapshot` — verify the textarea contains `# Profile` and the save button is present.
5. `browser_type` — append `\nAdded via editor\n` to the textarea.
6. `browser_click` on the Save button.
7. `browser_snapshot` — verify the save-result span reads "✓ Saved candidate_context/profile.md." and has `data-outcome="success"`.
8. Shell check: `cat "$JSP_BASE/candidate_context/profile.md"` — expect the appended content.
9. `browser_navigate` → `/tools/` — verify the "Edit config files" link is present and points to `/config/`.
10. `browser_navigate` → `/config/files/data/pipeline.db` — verify 403 response in `browser_snapshot` (FastAPI error page).

Cleanup:

```bash
kill $UVICORN_PID
rm -rf "$JSP_BASE"
```

- [ ] **Gate 4: Feature smoke on docker.lan stacks (optional, ops-side)**

Skippable for this PR — the editor targets `$JSP_BASE/candidate_context/` and `$JSP_BASE/config/` which on docker.lan map to the bind mounts `./state/candidate_context/` and `./state/config/`. Once the image is pulled on `docker.lan`, confirm a single round-trip edit on the operator's own stack before closing #149. Do not skip before merging; do skip before requesting review.

---

## Documentation Impact

This plan touches the following documentation surfaces. Each must be updated in the same PR (per memory "Documentation Sync Rule"); none may be deferred to follow-up.

| Surface | Change | Task |
|---|---|---|
| `CLAUDE.md` — "Key File Locations" → "Web Frontend Architecture" block | Add `routes/config.py`, `config_files.py`, `routes/tools.py` entries and a one-line description of `/config/` | Task 6 |
| `CHANGELOG.md` — `[Unreleased] / Added` | Add bullet describing the `/config/` editor, allowlist, and `/tools/` stub | Task 6 |
| `docs/superpowers/plans/2026-04-23-config-editor.md` (this file) | Initial creation | this plan's write step |
| `docs/project-board.md` | No change — board mechanics unchanged | — |
| `docs/deployment-model.md` | No change — no new bind mounts; editor writes to existing `state/config/` and `state/candidate_context/` paths | — |
| `docs/release-process.md` | No change — no schema/config/crontab migration | — |
| `README.md` | No change required — README does not enumerate web routes today. If a future README overhaul adds a "Web UI surfaces" table, `/config/` lands there then. | — |
| `docs/tuning.md` | Does not exist yet; #11 (user-facing docs) will create it and reference `/config/` then. No work in this PR. | — |
| Docstrings | Module docstrings on `config_files.py` + `routes/config.py` + `routes/tools.py` explain purpose; no inline comments unless the WHY is non-obvious (per CLAUDE.md comment rule). | Tasks 1, 2, 5 |

---

## Self-Review Checklist

Every acceptance criterion from issue #149 maps to at least one task here. Before opening the PR, confirm:

- [ ] **AC 1 — `/config/` page lists editable files by category.**
  → Task 2 creates `templates/config/index.html` with three categories; `tests/test_web_config_editor.py::test_index_lists_files_by_category` verifies.
- [ ] **AC 2 — Clicking a file opens the editor view with current content in a `<textarea>`.**
  → Task 3 adds `GET /config/files/{path:path}` + `editor.html`; `test_editor_shows_existing_content` verifies.
- [ ] **AC 3 — Saving writes content back to disk and shows a success/error indicator.**
  → Task 4 adds `POST /config/files/{path:path}` + `_save_result.html`; `test_save_writes_content_to_disk` + `test_save_result_partial_has_expected_attrs` verify.
- [ ] **AC 4 — Path traversal is blocked: requests for files outside the allowlist return 403.**
  → Task 1's allowlist unit tests + Task 3/4's HTTP tests cover both the pure-function and endpoint levels. The symlink-escape case is in the unit suite.
- [ ] **AC 5 — Role files (`config/roles/*.md`) are listed individually and editable.**
  → Task 1's `list_editable` globs the directory; `test_list_editable_groups_by_category` verifies. Task 2's index template renders each separately.
- [ ] **AC 6 — The editor is linked from `/tools/` as the "edit config files" action.**
  → Task 5's `/tools/` stub + `test_tools_page_links_to_config`.
- [ ] **AC 7 — `docs/tuning.md` can reference the config editor as the primary edit surface (unblocks #11).**
  → Satisfied implicitly: once this PR ships, #11 can describe `/config/` accurately. No doc work in this PR (tuning.md doesn't exist yet).

**Spec-coverage gaps:** none.

**Placeholder scan:** none — every step contains the actual code, path, or command the engineer needs.

**Type consistency:** verified. `base_root: Path` is consistent across `app.py`, `config_files.py`, and `routes/config.py`. `relpath: str` is consistent. `EDITABLE_CATEGORIES` is typed `dict[str, list[str] | str]` and consumed consistently in `list_editable`.

---

## Execution handoff

Plan complete. Two execution options:

1. **Subagent-Driven (recommended)** — dispatch a fresh subagent per task, review between tasks.
2. **Inline Execution** — execute tasks in this session using `superpowers:executing-plans`.

Which approach?
