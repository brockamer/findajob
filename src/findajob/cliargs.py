"""Positional arguments for the detached job-prep entry points.

The web app launches ``prep_application.py`` and ``interview_prep.py`` with a
``--`` before the job fields, so a company or title that begins with ``-`` is
read as data and never as an option. The scripts also read those fields by
position when they report a failure, and the ``--`` would shift every index;
this helper returns the fields with the terminator removed.
"""

from __future__ import annotations

import sys


def positional_argv(argv: list[str] | None = None) -> list[str]:
    """Return the fields after the first ``--``, or everything after the
    program name when the caller passed no ``--`` (older launchers)."""
    args = list(sys.argv if argv is None else argv)[1:]
    if "--" in args:
        return args[args.index("--") + 1 :]
    return args
