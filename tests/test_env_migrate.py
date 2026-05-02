"""Tests for migrate_rapidapi_key_env (#408)."""

from __future__ import annotations

from pathlib import Path

from findajob.onboarding.env_migrate import migrate_rapidapi_key_env


def test_no_op_when_jobs_api14_key_already_present(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("JOBS_API14_KEY=new-value\nOTHER=x\n")
    migrate_rapidapi_key_env(env)
    assert env.read_text() == "JOBS_API14_KEY=new-value\nOTHER=x\n"


def test_no_op_when_neither_key_set(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("OTHER=x\n")
    migrate_rapidapi_key_env(env)
    assert env.read_text() == "OTHER=x\n"


def test_renames_rapidapi_key_to_jobs_api14_key(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("RAPIDAPI_KEY=secret-value\nOTHER=x\n")
    migrate_rapidapi_key_env(env)
    out = env.read_text()
    assert "RAPIDAPI_KEY=" not in out
    assert "JOBS_API14_KEY=secret-value" in out
    assert "OTHER=x" in out


def test_idempotent(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("RAPIDAPI_KEY=secret-value\n")
    migrate_rapidapi_key_env(env)
    after_first = env.read_text()
    migrate_rapidapi_key_env(env)
    after_second = env.read_text()
    assert after_first == after_second


def test_both_present_keeps_jobs_api14_value_drops_old(tmp_path: Path) -> None:
    """If both are set, the new var wins; the old var is removed."""
    env = tmp_path / ".env"
    env.write_text("RAPIDAPI_KEY=old\nJOBS_API14_KEY=new\n")
    migrate_rapidapi_key_env(env)
    out = env.read_text()
    assert "RAPIDAPI_KEY=" not in out
    assert "JOBS_API14_KEY=new" in out


def test_missing_file_no_op(tmp_path: Path) -> None:
    """Migration does not create the .env file if it doesn't exist."""
    env = tmp_path / "missing.env"
    migrate_rapidapi_key_env(env)
    assert not env.exists()


def test_preserves_quotes_and_comments(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    original = '# Comment\nRAPIDAPI_KEY="quoted-value"\n# Another\nOTHER=x\n'
    env.write_text(original)
    migrate_rapidapi_key_env(env)
    out = env.read_text()
    assert "# Comment" in out
    assert "# Another" in out
    assert 'JOBS_API14_KEY="quoted-value"' in out
    assert "RAPIDAPI_KEY=" not in out
