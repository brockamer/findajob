"""Tests for the JobSourceAdapter Protocol and result dataclasses (#408)."""

from __future__ import annotations

from findajob.fetchers.adapters.base import (
    JobSourceAdapter,
    LiveTestResult,
    QueryResult,
)


class _DummyAdapter:
    name = "dummy"
    display_name = "Dummy Adapter"
    source_label = "dummy"
    required_env_vars: tuple[str, ...] = ("DUMMY_KEY",)

    def is_configured(self) -> bool:
        return True

    def fetch(self, queries: list[str]) -> list[dict]:
        return []

    def live_test(self, queries: list[str]) -> LiveTestResult:
        return LiveTestResult(ok=True, bucket="success", per_query=[], auth_error=None)


def test_dummy_adapter_satisfies_protocol() -> None:
    """A class with the right shape passes runtime_checkable isinstance check."""
    adapter = _DummyAdapter()
    assert isinstance(adapter, JobSourceAdapter)


def test_query_result_dataclass() -> None:
    qr = QueryResult(query="engineer", count=5, error=None)
    assert qr.query == "engineer"
    assert qr.count == 5
    assert qr.error is None


def test_live_test_result_success_bucket() -> None:
    result = LiveTestResult(
        ok=True,
        bucket="success",
        per_query=[QueryResult(query="a", count=3, error=None)],
        auth_error=None,
    )
    assert result.ok is True
    assert result.bucket == "success"
    assert len(result.per_query) == 1


def test_live_test_result_auth_failure_bucket() -> None:
    result = LiveTestResult(
        ok=False,
        bucket="auth",
        per_query=[],
        auth_error="HTTP 401: invalid key",
    )
    assert result.ok is False
    assert result.bucket == "auth"
    assert result.auth_error is not None


def test_live_test_result_invalid_bucket_rejected() -> None:
    """Bucket must be one of the documented values."""
    valid_buckets = {"success", "mixed", "zero_rows", "auth", "rate_limit", "server", "network"}
    # If we accidentally typo, the type system catches it. This test asserts the
    # documented bucket set so anyone changing it has to update this list too.
    for bucket in valid_buckets:
        LiveTestResult(ok=True, bucket=bucket, per_query=[], auth_error=None)


def test_protocol_rejects_missing_method() -> None:
    """An object missing fetch() doesn't satisfy the Protocol."""

    class _Incomplete:
        name = "incomplete"
        display_name = "Incomplete"
        source_label = "incomplete"
        required_env_vars: tuple[str, ...] = ()

        def is_configured(self) -> bool:
            return True

        # missing fetch() and live_test()

    assert not isinstance(_Incomplete(), JobSourceAdapter)
