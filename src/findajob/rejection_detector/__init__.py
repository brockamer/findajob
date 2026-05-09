"""Pure-data rejection detection package."""

from findajob.rejection_detector.classifier import RejectionSuggestion, classify_email
from findajob.rejection_detector.matcher import MatchResult, match_job

__all__ = ["RejectionSuggestion", "classify_email", "MatchResult", "match_job"]
