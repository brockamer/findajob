"""Stale-sentinel handling for `findajob.interview.sentinel` (#312).

Post-#537: sentinel logic lives in `findajob.interview.sentinel`; the
`sys.path.insert` workaround for loading from `scripts/` is no longer needed.
"""

import os
import time
from pathlib import Path
from unittest.mock import patch

from findajob.interview import sentinel


def test_sentinel_missing_does_not_block(tmp_path: Path) -> None:
    sentinel_path = tmp_path / sentinel.SENTINEL_NAME
    assert sentinel._sentinel_blocks_run(str(sentinel_path), log_kwargs={"job_id": "j1"}) is False


def test_fresh_sentinel_blocks(tmp_path: Path) -> None:
    sentinel_path = tmp_path / sentinel.SENTINEL_NAME
    sentinel_path.write_text("just started")
    # mtime is `now` from the write — well under STALE_AFTER_SECONDS.
    assert sentinel._sentinel_blocks_run(str(sentinel_path), log_kwargs={"job_id": "j1"}) is True
    assert sentinel_path.exists()  # fresh sentinel must NOT be removed


def test_stale_sentinel_removed_and_does_not_block(tmp_path: Path) -> None:
    sentinel_path = tmp_path / sentinel.SENTINEL_NAME
    sentinel_path.write_text("orphaned by kill -9 long ago")
    # Backdate mtime to STALE_AFTER_SECONDS + 1 minute ago.
    stale_mtime = time.time() - (sentinel.SENTINEL_STALE_AFTER_SECONDS + 60)
    os.utime(sentinel_path, (stale_mtime, stale_mtime))

    with patch.object(sentinel, "log_event") as mock_log:
        result = sentinel._sentinel_blocks_run(
            str(sentinel_path),
            log_kwargs={"job_id": "j1", "company": "Anthropic", "title": "Director"},
        )

    assert result is False
    assert not sentinel_path.exists()  # stale sentinel must be removed in place
    # Audit event must fire so pipeline.jsonl shows the recovery.
    assert mock_log.called
    event_name = mock_log.call_args[0][0]
    assert event_name == "interview_prep_sentinel_stale_removed"
    kwargs = mock_log.call_args[1]
    assert kwargs.get("job_id") == "j1"
    assert kwargs.get("company") == "Anthropic"
    assert isinstance(kwargs.get("age_seconds"), int)
    assert kwargs["age_seconds"] >= sentinel.SENTINEL_STALE_AFTER_SECONDS
