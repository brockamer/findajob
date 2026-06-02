#!/usr/bin/env python3
"""Print the operator's picked IANA timezone for the container entrypoint to
``export TZ``. Reads ``<BASE>/data/timezone`` (written by onboarding) via
``findajob.timeutil.read_timezone_file``. Exit 0 + zone on stdout when valid;
exit 1 (silent) when there is no valid pick, so the entrypoint keeps the
deploy-config default. See docs/superpowers/specs/2026-06-02-981-*.md (#981)."""

import sys

from findajob.paths import BASE
from findajob.timeutil import read_timezone_file


def main() -> int:
    zone = read_timezone_file(BASE)
    if zone is None:
        return 1
    print(zone)
    return 0


if __name__ == "__main__":
    sys.exit(main())
