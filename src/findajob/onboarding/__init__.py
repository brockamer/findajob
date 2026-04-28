"""findajob onboarding pipeline: interview emission parser + config injector.

Public surface:

- :func:`parse_emission` — parse an interview emission into files to inject.
- :func:`inject` — write parsed files atomically + run discovery; return :class:`InjectResult`.
- :class:`InjectResult` — backup_dir + DiscoveryStatus from a successful inject.
- :class:`DiscoveryStatus` — success/count/error from the post-commit discovery hook.
- :func:`is_complete` — True iff the sentinel file exists under ``base_root``.
- :func:`mark_complete` — write the sentinel file with the current UTC timestamp.
"""

from __future__ import annotations

from findajob.onboarding.injector import (
    DiscoveryStatus,
    InjectResult,
    inject,
    is_complete,
    mark_complete,
)
from findajob.onboarding.openrouter_smoke import OnboardingSmokeCheckFailed
from findajob.onboarding.parser import ALLOWED_FILENAMES, ParsedEmission, parse_emission

__all__ = [
    "ALLOWED_FILENAMES",
    "DiscoveryStatus",
    "InjectResult",
    "OnboardingSmokeCheckFailed",
    "ParsedEmission",
    "inject",
    "is_complete",
    "mark_complete",
    "parse_emission",
]
