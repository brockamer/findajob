"""Tier-1 stats strip — the recall-audit + drift-alert crons are disabled in the
declarative schedule (stats-platform observability de-scoped in favour of the
filter-proposals loop). Re-enablable via FINDAJOB_<JOB>_ENABLED env override.

(The top-nav Stats-link removal is covered by
tests/test_web_stats_tabs.py::test_top_nav_omits_stats_link.)
"""

from pathlib import Path

import yaml

_SCHEDULE = Path(__file__).resolve().parent.parent / "ops" / "scheduled-jobs.yaml"


def test_stats_platform_crons_disabled() -> None:
    jobs = yaml.safe_load(_SCHEDULE.read_text())["jobs"]
    assert jobs["recall-audit"]["enabled"] is False
    assert jobs["drift-alert"]["enabled"] is False
