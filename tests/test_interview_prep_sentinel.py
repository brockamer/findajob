"""Stale-sentinel handling for scripts/interview_prep.py (#312)."""

import os
import sys
import time
from pathlib import Path
from unittest.mock import patch

# scripts/ isn't on sys.path by default; tests need interview_prep importable.
SCRIPTS = Path(__file__).parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import interview_prep  # noqa: E402


def test_sentinel_missing_does_not_block(tmp_path: Path) -> None:
    sentinel = tmp_path / interview_prep.SENTINEL_NAME
    assert interview_prep._sentinel_blocks_run(str(sentinel), log_kwargs={"job_id": "j1"}) is False


def test_fresh_sentinel_blocks(tmp_path: Path) -> None:
    sentinel = tmp_path / interview_prep.SENTINEL_NAME
    sentinel.write_text("just started")
    # mtime is `now` from the write — well under STALE_AFTER_SECONDS.
    assert interview_prep._sentinel_blocks_run(str(sentinel), log_kwargs={"job_id": "j1"}) is True
    assert sentinel.exists()  # fresh sentinel must NOT be removed


def test_stale_sentinel_removed_and_does_not_block(tmp_path: Path) -> None:
    sentinel = tmp_path / interview_prep.SENTINEL_NAME
    sentinel.write_text("orphaned by kill -9 long ago")
    # Backdate mtime to STALE_AFTER_SECONDS + 1 minute ago.
    stale_mtime = time.time() - (interview_prep.SENTINEL_STALE_AFTER_SECONDS + 60)
    os.utime(sentinel, (stale_mtime, stale_mtime))

    with patch.object(interview_prep, "log_event") as mock_log:
        result = interview_prep._sentinel_blocks_run(
            str(sentinel),
            log_kwargs={"job_id": "j1", "company": "Anthropic", "title": "Director"},
        )

    assert result is False
    assert not sentinel.exists()  # stale sentinel must be removed in place
    # Audit event must fire so pipeline.jsonl shows the recovery.
    assert mock_log.called
    event_name = mock_log.call_args[0][0]
    assert event_name == "interview_prep_sentinel_stale_removed"
    kwargs = mock_log.call_args[1]
    assert kwargs.get("job_id") == "j1"
    assert kwargs.get("company") == "Anthropic"
    assert isinstance(kwargs.get("age_seconds"), int)
    assert kwargs["age_seconds"] >= interview_prep.SENTINEL_STALE_AFTER_SECONDS
