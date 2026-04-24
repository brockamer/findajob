#!/usr/bin/env python3
"""Derive ``config/companies_of_interest.txt`` from ``config/target_companies.md``.

Idempotent: runs on every container start (from ``ops/entrypoint.sh``) and
is a no-op when ``companies_of_interest.txt`` already exists or when
``target_companies.md`` is missing.

Backfills pre-#148 stacks whose ``target_companies.md`` was written before
the onboarding paste-back injector learned to derive the companies list.
See issue #222.
"""

from pathlib import Path

from findajob.onboarding.injector import derive_companies_of_interest
from findajob.paths import BASE
from findajob.utils import log_event

SRC = Path(BASE) / "config" / "target_companies.md"
DST = Path(BASE) / "config" / "companies_of_interest.txt"


def main() -> int:
    if not SRC.is_file():
        return 0
    if DST.exists():
        return 0

    body = derive_companies_of_interest(SRC.read_text(encoding="utf-8"))
    if not body:
        log_event(
            "companies_of_interest_derive_skip",
            reason="no_tier1_section",
            src=str(SRC),
        )
        return 0

    DST.parent.mkdir(parents=True, exist_ok=True)
    DST.write_text(body, encoding="utf-8")
    count = sum(1 for line in body.splitlines() if line.strip())
    log_event("companies_of_interest_derived", dst=str(DST), count=count)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
