"""findajob onboarding pipeline: interview emission parser + config injector.

Public surface:

- :func:`parse_emission` — parse an interview emission into files to inject.
- :func:`inject` — write parsed files atomically; return the backup dir.
- :func:`is_complete` — True iff the sentinel file exists under ``base_root``.
- :func:`mark_complete` — write the sentinel file with the current UTC timestamp.
"""

from __future__ import annotations

from findajob.onboarding.injector import inject, is_complete, mark_complete
from findajob.onboarding.parser import ALLOWED_FILENAMES, ParsedEmission, parse_emission

__all__ = [
    "ALLOWED_FILENAMES",
    "ParsedEmission",
    "inject",
    "is_complete",
    "mark_complete",
    "parse_emission",
]
