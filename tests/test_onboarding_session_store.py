"""Unit tests for findajob.onboarding.session_store (#336 Task 2, #339 Task 1)."""

from __future__ import annotations

import dataclasses
import json
import os
import sqlite3
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from findajob.onboarding.session_store import (
    Credentials,
    Session,
    add_turn_cost,
    append_turn,
    create_session,
    find_credentials_only,
    get_credentials,
    get_session,
    mark_complete,
    migrate_schema,
    set_credentials,
    set_error,
    update_captured_blocks,
)


@pytest.fixture
def db(tmp_path):
    """Initialize a fresh pipeline.db via init_db.py and yield a connection."""
    base = tmp_path / "repo"
    (base / "data").mkdir(parents=True)
    (base / "src" / "findajob").mkdir(parents=True)
    (base / "src" / "findajob" / "__init__.py").write_text("")
    (base / "src" / "findajob" / "paths.py").write_text(f'BASE = r"{base}"\n')

    env = os.environ.copy()
    env["PYTHONPATH"] = str(base / "src")
    repo_root = Path(__file__).resolve().parents[1]
    init_db = repo_root / "scripts" / "init_db.py"
    result = subprocess.run([sys.executable, str(init_db)], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr

    conn = sqlite3.connect(str(base / "data" / "pipeline.db"))
    yield conn
    conn.close()


def test_create_session_returns_uuid_and_persists_row(db):
    sid = create_session(db)
    # Validates UUID4 format.
    parsed = uuid.UUID(sid)
    assert parsed.version == 4

    row = db.execute(
        "SELECT id, history_json, captured_blocks_json, started_at, last_turn_at, "
        "completed_at, error_state FROM onboarding_sessions WHERE id = ?",
        (sid,),
    ).fetchone()
    assert row is not None
    assert row[0] == sid
    assert row[1] == "[]"
    assert row[2] == "{}"
    assert row[3] is not None and row[3].endswith("Z")
    assert row[4] == row[3]
    assert row[5] is None
    assert row[6] is None


def test_create_session_writes_commit_visible_to_fresh_connection(db, tmp_path):
    """A second connection to the same DB sees the row immediately."""
    sid = create_session(db)
    db_path = tmp_path / "repo" / "data" / "pipeline.db"
    other = sqlite3.connect(str(db_path))
    try:
        row = other.execute("SELECT id FROM onboarding_sessions WHERE id = ?", (sid,)).fetchone()
        assert row is not None
    finally:
        other.close()


def test_get_session_roundtrip_returns_frozen_session(db):
    sid = create_session(db)
    sess = get_session(db, sid)
    assert isinstance(sess, Session)
    assert sess.id == sid
    assert sess.history == []
    assert sess.captured_blocks == {}
    assert sess.completed_at is None
    assert sess.error_state is None
    # Frozen dataclass — direct attribute mutation must fail.
    with pytest.raises(dataclasses.FrozenInstanceError):
        sess.id = "mutated"  # type: ignore[misc]


def test_get_session_returns_none_for_unknown_id(db):
    assert get_session(db, "nonexistent-uuid") is None


def test_append_turn_extends_history_in_order(db):
    sid = create_session(db)
    append_turn(db, sid, "assistant", "Welcome — what role are you targeting?")
    append_turn(db, sid, "user", "Data center operations.")
    append_turn(db, sid, "assistant", "Got it. Tell me about your last team.")
    sess = get_session(db, sid)
    assert sess is not None
    assert sess.history == [
        {"role": "assistant", "content": "Welcome — what role are you targeting?"},
        {"role": "user", "content": "Data center operations."},
        {"role": "assistant", "content": "Got it. Tell me about your last team."},
    ]


def test_append_turn_updates_last_turn_at(db):
    sid = create_session(db)
    sess0 = get_session(db, sid)
    assert sess0 is not None
    initial_last = sess0.last_turn_at
    # Force a clock tick by patching the helper. Otherwise sub-second precision
    # collapses both timestamps to the same second-resolution string.
    import findajob.onboarding.session_store as store

    real_utcnow = store._utcnow_iso
    store._utcnow_iso = lambda: "2099-01-01T00:00:00Z"
    try:
        append_turn(db, sid, "assistant", "next turn")
    finally:
        store._utcnow_iso = real_utcnow
    sess1 = get_session(db, sid)
    assert sess1 is not None
    assert sess1.last_turn_at == "2099-01-01T00:00:00Z"
    assert sess1.last_turn_at != initial_last


def test_append_turn_rejects_bad_role(db):
    sid = create_session(db)
    with pytest.raises(ValueError, match="role must be"):
        append_turn(db, sid, "system", "not allowed")


def test_append_turn_raises_keyerror_for_unknown_session(db):
    with pytest.raises(KeyError):
        append_turn(db, "nonexistent", "user", "hi")


def test_update_captured_blocks_replaces_map(db):
    sid = create_session(db)
    update_captured_blocks(db, sid, {"profile.md": "# profile\n"})
    sess1 = get_session(db, sid)
    assert sess1 is not None
    assert sess1.captured_blocks == {"profile.md": "# profile\n"}
    # Replacement, not merge.
    update_captured_blocks(db, sid, {"master_resume.md": "# resume\n"})
    sess2 = get_session(db, sid)
    assert sess2 is not None
    assert sess2.captured_blocks == {"master_resume.md": "# resume\n"}


def test_update_captured_blocks_serializes_unicode(db):
    sid = create_session(db)
    update_captured_blocks(db, sid, {"profile.md": "# Bröck — naïve résumé"})
    sess = get_session(db, sid)
    assert sess is not None
    assert sess.captured_blocks["profile.md"] == "# Bröck — naïve résumé"


def test_mark_complete_sets_completed_at(db):
    sid = create_session(db)
    mark_complete(db, sid)
    sess = get_session(db, sid)
    assert sess is not None
    assert sess.completed_at is not None
    assert sess.completed_at.endswith("Z")


def test_set_error_persists_message(db):
    sid = create_session(db)
    set_error(db, sid, "OpenRouter 429 — rate limit; retry in 30s")
    sess = get_session(db, sid)
    assert sess is not None
    assert sess.error_state == "OpenRouter 429 — rate limit; retry in 30s"
    # Empty string clears.
    set_error(db, sid, "")
    sess2 = get_session(db, sid)
    assert sess2 is not None
    assert sess2.error_state == ""


def test_history_json_round_trips_complex_content(db):
    """Multi-line content with quotes + escapes survives JSON round-trip."""
    sid = create_session(db)
    tricky = 'She said "I\'m here" and the LLM\nsaid:\n```python\nprint("x")\n```'
    append_turn(db, sid, "user", tricky)
    sess = get_session(db, sid)
    assert sess is not None
    assert sess.history[0]["content"] == tricky


def test_session_dataclass_is_frozen():
    """Session must be frozen so callers can't accidentally mutate persisted state."""
    s = Session(
        id="x",
        history=[],
        captured_blocks={},
        started_at="2026-05-01T00:00:00Z",
        last_turn_at="2026-05-01T00:00:00Z",
        completed_at=None,
        error_state=None,
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        s.id = "mutated"  # type: ignore[misc]


def test_get_session_history_is_a_list_not_string(db):
    """Defends against accidentally returning the raw json string."""
    sid = create_session(db)
    append_turn(db, sid, "assistant", "hello")
    sess = get_session(db, sid)
    assert sess is not None
    assert isinstance(sess.history, list)
    assert isinstance(sess.captured_blocks, dict)


def test_create_session_ids_are_unique(db):
    """Two consecutive create_session calls produce distinct ids."""
    sid1 = create_session(db)
    sid2 = create_session(db)
    assert sid1 != sid2


def test_history_json_is_canonical_after_create(db):
    """create_session must write '[]' / '{}' literals so the dataclass parses cleanly."""
    sid = create_session(db)
    raw = db.execute(
        "SELECT history_json, captured_blocks_json FROM onboarding_sessions WHERE id = ?",
        (sid,),
    ).fetchone()
    assert raw[0] == "[]"
    assert raw[1] == "{}"
    # And the parsed values are the empty containers.
    assert json.loads(raw[0]) == []
    assert json.loads(raw[1]) == {}


# ── find_active (#336 Task 8) ─────────────────────────────────────────────


def _set_last_turn_at(db, session_id: str, sql_expr: str) -> None:
    """Override last_turn_at via a raw SQL expression so tests can age sessions."""
    db.execute(f"UPDATE onboarding_sessions SET last_turn_at = {sql_expr} WHERE id = ?", (session_id,))
    db.commit()


def test_find_active_returns_none_when_no_sessions(db):
    from findajob.onboarding.session_store import find_active

    assert find_active(db) is None


def test_find_active_returns_recent_uncompleted_session(db):
    from findajob.onboarding.session_store import find_active

    sid = create_session(db)
    sess = find_active(db)
    assert sess is not None
    assert sess.id == sid


def test_find_active_excludes_completed_sessions(db):
    from findajob.onboarding.session_store import find_active

    sid = create_session(db)
    mark_complete(db, sid)
    assert find_active(db) is None


def test_find_active_excludes_stale_sessions_older_than_24h(db):
    """Sessions whose last_turn_at is > 24h ago should be excluded."""
    from findajob.onboarding.session_store import find_active

    sid = create_session(db)
    _set_last_turn_at(db, sid, "datetime('now', '-25 hours')")
    assert find_active(db) is None


def test_find_active_returns_most_recently_active_when_multiple(db):
    """When multiple un-completed sessions exist, the most recent wins."""
    from findajob.onboarding.session_store import find_active

    older = create_session(db)
    newer = create_session(db)
    _set_last_turn_at(db, older, "datetime('now', '-2 hours')")
    _set_last_turn_at(db, newer, "datetime('now', '-30 minutes')")

    sess = find_active(db)
    assert sess is not None
    assert sess.id == newer


def test_find_active_skips_completed_in_favor_of_uncompleted(db):
    """A completed session should not block an older but un-completed one."""
    from findajob.onboarding.session_store import find_active

    finished = create_session(db)
    pending = create_session(db)
    mark_complete(db, finished)
    _set_last_turn_at(db, pending, "datetime('now', '-3 hours')")

    sess = find_active(db)
    assert sess is not None
    assert sess.id == pending


def test_find_active_includes_errored_sessions(db):
    """An error'd but un-completed session is still resumable — the user
    can retry from the chat page."""
    from findajob.onboarding.session_store import find_active, set_error

    sid = create_session(db)
    set_error(db, sid, "boom")

    sess = find_active(db)
    assert sess is not None
    assert sess.id == sid
    assert sess.error_state == "boom"


def test_find_active_max_age_hours_parameter_works(db):
    """max_age_hours=1 excludes 2h-old sessions."""
    from findajob.onboarding.session_store import find_active

    sid = create_session(db)
    _set_last_turn_at(db, sid, "datetime('now', '-2 hours')")

    assert find_active(db, max_age_hours=1) is None
    assert find_active(db, max_age_hours=24) is not None
    assert find_active(db, max_age_hours=24).id == sid


# ── migrate_schema + credentials (#339 Task 1) ────────────────────────────────


def test_migrate_schema_is_idempotent(db):
    """Calling migrate_schema twice raises no error and all columns exist."""
    migrate_schema(db)
    migrate_schema(db)
    cols = {row[1] for row in db.execute("PRAGMA table_info(onboarding_sessions)").fetchall()}
    assert "tester_openrouter_key" in cols
    assert "tester_rapidapi_key" in cols
    assert "tester_google_key" in cols


def test_migrate_schema_on_table_without_credential_columns(tmp_path):
    """ALTER TABLE adds credential columns to a pre-existing table that lacks them."""
    # Build a minimal DB with the original onboarding_sessions schema (no cred cols).
    db_path = tmp_path / "pipeline.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """CREATE TABLE onboarding_sessions (
               id TEXT PRIMARY KEY,
               history_json TEXT NOT NULL,
               captured_blocks_json TEXT NOT NULL DEFAULT '{}',
               started_at TEXT NOT NULL,
               last_turn_at TEXT NOT NULL,
               completed_at TEXT,
               error_state TEXT
           )"""
    )
    conn.commit()

    # Confirm columns are absent before migration.
    cols_before = {row[1] for row in conn.execute("PRAGMA table_info(onboarding_sessions)").fetchall()}
    assert "tester_openrouter_key" not in cols_before

    migrate_schema(conn)

    cols_after = {row[1] for row in conn.execute("PRAGMA table_info(onboarding_sessions)").fetchall()}
    assert "tester_openrouter_key" in cols_after
    assert "tester_rapidapi_key" in cols_after
    assert "tester_google_key" in cols_after
    conn.close()


def test_set_and_get_credentials_round_trip(db):
    """set_credentials stores values; get_credentials returns them intact."""
    migrate_schema(db)
    sid = create_session(db)
    set_credentials(
        db,
        sid,
        openrouter_api_key="sk-or-test-abc123",
        rapidapi_key="rapi-test-xyz",
        google_api_key="AIza-test-google",
    )
    creds = get_credentials(db, sid)
    assert creds is not None
    assert creds.openrouter_api_key == "sk-or-test-abc123"
    assert creds.rapidapi_key == "rapi-test-xyz"
    assert creds.google_api_key == "AIza-test-google"


def test_set_credentials_blank_strings_stored_as_null(db):
    """Blank strings must be coerced to NULL, not persisted as empty strings."""
    migrate_schema(db)
    sid = create_session(db)
    set_credentials(
        db,
        sid,
        openrouter_api_key="  ",  # whitespace only → NULL
        rapidapi_key="rapi-test",
        google_api_key="",  # empty → NULL
    )
    creds = get_credentials(db, sid)
    assert creds is not None
    assert creds.openrouter_api_key is None
    assert creds.rapidapi_key == "rapi-test"
    assert creds.google_api_key is None

    # Verify at the raw SQL level too.
    row = db.execute(
        "SELECT tester_openrouter_key, tester_google_key FROM onboarding_sessions WHERE id = ?",
        (sid,),
    ).fetchone()
    assert row[0] is None
    assert row[1] is None


def test_set_credentials_all_blank_get_returns_none(db):
    """When all three are blank, get_credentials must return None (not collected)."""
    migrate_schema(db)
    sid = create_session(db)
    set_credentials(db, sid, openrouter_api_key="", rapidapi_key="", google_api_key="")
    assert get_credentials(db, sid) is None


def test_set_credentials_raises_for_unknown_session(db):
    """set_credentials must raise KeyError when session_id doesn't exist."""
    migrate_schema(db)
    with pytest.raises(KeyError):
        set_credentials(
            db,
            "nonexistent-id",
            openrouter_api_key="key",
            rapidapi_key="key",
            google_api_key="key",
        )


def test_find_credentials_only_returns_credentialed_no_history_session(db):
    """find_credentials_only should return the session with creds and no chat history."""
    migrate_schema(db)
    sid = create_session(db)
    set_credentials(db, sid, openrouter_api_key="sk-or-x", rapidapi_key="rapi-x", google_api_key="g-x")
    result = find_credentials_only(db)
    assert result is not None
    assert result.id == sid


def test_find_credentials_only_excludes_session_with_chat_history(db):
    """A session that already has chat turns must NOT be returned."""
    migrate_schema(db)
    sid = create_session(db)
    set_credentials(db, sid, openrouter_api_key="sk-or-x", rapidapi_key="", google_api_key="")
    append_turn(db, sid, "assistant", "Welcome!")
    assert find_credentials_only(db) is None


def test_find_credentials_only_returns_most_recent_of_multiple(db):
    """When multiple credentialed no-history sessions exist, the most recent wins."""
    migrate_schema(db)
    sid_older = create_session(db)
    sid_newer = create_session(db)
    set_credentials(db, sid_older, openrouter_api_key="sk-or-old", rapidapi_key="", google_api_key="")
    set_credentials(db, sid_newer, openrouter_api_key="sk-or-new", rapidapi_key="", google_api_key="")
    # Age the older session.
    db.execute(
        "UPDATE onboarding_sessions SET last_turn_at = datetime('now', '-2 hours') WHERE id = ?",
        (sid_older,),
    )
    db.commit()

    result = find_credentials_only(db)
    assert result is not None
    assert result.id == sid_newer


def test_find_credentials_only_returns_none_when_no_credentialed_sessions(db):
    """Returns None when no session has any credential set."""
    migrate_schema(db)
    _sid = create_session(db)  # no credentials set
    assert find_credentials_only(db) is None


def test_credentials_dataclass_is_frozen():
    """Credentials must be frozen so callers can't accidentally mutate them."""
    creds = Credentials(openrouter_api_key="x", rapidapi_key=None, google_api_key=None)
    with pytest.raises(dataclasses.FrozenInstanceError):
        creds.openrouter_api_key = "mutated"  # type: ignore[misc]


def test_add_turn_cost_accumulates_across_calls(db):
    """Two turns of cost data sum into cumulative_cost_usd."""
    migrate_schema(db)
    sid = create_session(db)
    add_turn_cost(db, sid, {"cost": 0.0125, "prompt_tokens": 100})
    add_turn_cost(db, sid, {"cost": 0.0050})
    sess = get_session(db, sid)
    assert sess is not None
    assert sess.cumulative_cost_usd == pytest.approx(0.0175)


def test_add_turn_cost_falls_back_to_upstream_inference_cost(db):
    """BYOK responses sometimes report cost under cost_details.upstream_inference_cost."""
    migrate_schema(db)
    sid = create_session(db)
    add_turn_cost(db, sid, {"cost_details": {"upstream_inference_cost": 0.42}})
    sess = get_session(db, sid)
    assert sess is not None
    assert sess.cumulative_cost_usd == pytest.approx(0.42)


def test_add_turn_cost_no_op_on_empty_or_missing_cost(db):
    """Empty / missing / non-numeric cost must not corrupt the running total."""
    migrate_schema(db)
    sid = create_session(db)
    add_turn_cost(db, sid, {})
    add_turn_cost(db, sid, {"cost": None})
    add_turn_cost(db, sid, {"cost": "not-a-number"})
    add_turn_cost(db, sid, {"cost": 0})
    add_turn_cost(db, sid, "garbage")  # type: ignore[arg-type]
    sess = get_session(db, sid)
    assert sess is not None
    assert sess.cumulative_cost_usd == 0.0


def test_lifetime_cost_usd_sums_across_sessions(db):
    """lifetime_cost_usd sums cumulative_cost_usd across every session row."""
    from findajob.onboarding.session_store import lifetime_cost_usd

    migrate_schema(db)
    sid_a = create_session(db)
    sid_b = create_session(db)
    add_turn_cost(db, sid_a, {"cost": 0.10})
    add_turn_cost(db, sid_a, {"cost": 0.05})
    add_turn_cost(db, sid_b, {"cost": 0.30})
    assert lifetime_cost_usd(db) == pytest.approx(0.45)


def test_lifetime_cost_usd_zero_on_empty_db(db):
    from findajob.onboarding.session_store import lifetime_cost_usd

    assert lifetime_cost_usd(db) == 0.0


def test_lifetime_cost_usd_handles_missing_column_gracefully(tmp_path):
    """Older stacks before 2026-05-02 lack the column. Aggregator returns 0,
    not a crash, so the nav badge degrades to "$0.00" instead of 500ing."""
    from findajob.onboarding.session_store import lifetime_cost_usd

    db_path = tmp_path / "old.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE onboarding_sessions (
            id TEXT PRIMARY KEY,
            history_json TEXT NOT NULL DEFAULT '[]',
            captured_blocks_json TEXT NOT NULL DEFAULT '{}',
            started_at TEXT NOT NULL DEFAULT '',
            last_turn_at TEXT NOT NULL DEFAULT '',
            completed_at TEXT,
            error_state TEXT
        );
    """)
    try:
        assert lifetime_cost_usd(conn) == 0.0
    finally:
        conn.close()
