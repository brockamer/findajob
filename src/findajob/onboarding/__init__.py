"""findajob onboarding pipeline: interview emission parser + config injector."""

from __future__ import annotations

from findajob.onboarding.parser import ALLOWED_FILENAMES, ParsedEmission, parse_emission

__all__ = ["ALLOWED_FILENAMES", "ParsedEmission", "parse_emission"]
