"""Concurrency sentinel for in-flight interview-prep generation.

Re-clicking "Interviewing" on the dashboard regenerates the artifact;
this sentinel prevents two concurrent runs against the same prep folder
from racing on writes. A sentinel older than `SENTINEL_STALE_AFTER_SECONDS`
is treated as orphaned from a killed run and removed in place.

Extracted from `scripts/interview_prep.py` in M3 (#537). Behavior preserved.
"""

import os
import time

from findajob.utils import log_event

SENTINEL_NAME = ".interview_prep_in_progress"

# Treat any sentinel older than this as orphaned from a killed run.
# Well above typical Opus 4.7 generation time (~2 min observed) but well below
# the operator-noticing threshold for a stuck Interviewing button.
SENTINEL_STALE_AFTER_SECONDS = 600


def _sentinel_blocks_run(sentinel_path: str, *, log_kwargs: dict[str, object]) -> bool:
    """Return True iff a fresh in-flight sentinel exists at ``sentinel_path``.

    A sentinel older than ``SENTINEL_STALE_AFTER_SECONDS`` is treated as
    orphaned from a killed run: removed in place and a
    ``interview_prep_sentinel_stale_removed`` event logged so the recovery
    is auditable in pipeline.jsonl. Returns False after removal so the
    caller proceeds.
    """
    if not os.path.exists(sentinel_path):
        return False
    try:
        age = time.time() - os.path.getmtime(sentinel_path)
    except OSError:
        return False
    if age < SENTINEL_STALE_AFTER_SECONDS:
        return True
    log_event(
        "interview_prep_sentinel_stale_removed",
        age_seconds=int(age),
        **log_kwargs,
    )
    try:
        os.remove(sentinel_path)
    except OSError:
        pass
    return False
