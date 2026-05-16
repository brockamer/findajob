"""Tests for src/findajob/onboarding/key_validation.py.

Coverage goals:
- Each validator: happy path, empty/blank input, format failures.
- RapidAPI edge cases: leading/trailing whitespace stripped → bare key passes;
  embedded whitespace → fails; pasted curl header style → fails.
- OpenRouter edge cases: missing prefix → fails with clear message; correct prefix → pass.
"""

from findajob.onboarding.key_validation import (
    validate_openrouter_format,
    validate_rapidapi_format,
)

# ---------------------------------------------------------------------------
# validate_openrouter_format
# ---------------------------------------------------------------------------


class TestValidateOpenrouterFormat:
    def test_pass_valid_key(self) -> None:
        ok, msg = validate_openrouter_format("sk-or-v1-fake-test-key-abc123")
        assert ok is True
        assert msg == ""

    def test_fail_empty_string(self) -> None:
        ok, msg = validate_openrouter_format("")
        assert ok is False
        assert msg  # non-empty error message

    def test_fail_whitespace_only(self) -> None:
        ok, msg = validate_openrouter_format("   ")
        assert ok is False
        assert msg

    def test_fail_missing_prefix(self) -> None:
        ok, msg = validate_openrouter_format("sk-abcdef1234567890")
        assert ok is False
        assert "sk-or-v1-" in msg

    def test_fail_wrong_prefix(self) -> None:
        ok, msg = validate_openrouter_format("sk-ant-v1-abc123")
        assert ok is False
        assert "sk-or-v1-" in msg

    def test_pass_leading_trailing_whitespace_stripped(self) -> None:
        # Strip is applied before prefix check
        ok, msg = validate_openrouter_format("  sk-or-v1-fake-test-key-abc123  ")
        assert ok is True
        assert msg == ""

    def test_fail_openrouter_key_is_required(self) -> None:
        # Blank is required-field failure; message should mention "required" or similar
        ok, msg = validate_openrouter_format("")
        assert ok is False
        assert msg  # any non-empty user-actionable message


# ---------------------------------------------------------------------------
# validate_rapidapi_format
# ---------------------------------------------------------------------------


class TestValidateRapidapiFormat:
    def test_pass_blank_input(self) -> None:
        # Optional field — blank is fine
        ok, msg = validate_rapidapi_format("")
        assert ok is True
        assert msg == ""

    def test_pass_whitespace_only_treated_as_blank(self) -> None:
        ok, msg = validate_rapidapi_format("   \t\n")
        assert ok is True
        assert msg == ""

    def test_pass_valid_key(self) -> None:
        ok, msg = validate_rapidapi_format("abc123XYZfakeRapidAPIkey9876")
        assert ok is True
        assert msg == ""

    def test_pass_key_with_surrounding_whitespace_stripped(self) -> None:
        # Leading/trailing whitespace stripped → bare key should pass
        ok, msg = validate_rapidapi_format("  abc123XYZfakeRapidAPIkey9876  ")
        assert ok is True
        assert msg == ""

    def test_fail_embedded_space(self) -> None:
        # Pasted as "X-RapidAPI-Key: abc123" style — space in the value
        ok, msg = validate_rapidapi_format("abc 123")
        assert ok is False
        assert msg

    def test_fail_curl_header_style(self) -> None:
        # Accidentally pasted the whole header line
        ok, msg = validate_rapidapi_format("X-RapidAPI-Key: abc123fakekey")
        assert ok is False
        assert msg

    def test_fail_embedded_newline(self) -> None:
        # Copy-paste artifact: key with embedded newline
        ok, msg = validate_rapidapi_format("abc123\nfakekey")
        assert ok is False
        assert msg

    def test_fail_embedded_tab(self) -> None:
        ok, msg = validate_rapidapi_format("abc123\tfakekey")
        assert ok is False
        assert msg

    def test_fail_bearer_prefix_with_space(self) -> None:
        # "Bearer abc123" — space between token type and value
        ok, msg = validate_rapidapi_format("Bearer abc123fake")
        assert ok is False
        assert msg

    def test_pass_alphanumeric_with_hyphens(self) -> None:
        # Dashes and underscores are printable ASCII with no whitespace — valid
        ok, msg = validate_rapidapi_format("abc-123_XYZ-fake-key")
        assert ok is True
        assert msg == ""

    def test_fail_openrouter_key_pasted_in_rapidapi_field(self) -> None:
        # Dominant cross-paste shape — OpenRouter key in the RapidAPI field (#689).
        ok, msg = validate_rapidapi_format("sk-or-v1-fake-tester-key-abc123")
        assert ok is False
        assert "OpenRouter" in msg

    def test_fail_openrouter_key_pasted_with_surrounding_whitespace(self) -> None:
        # Strip-then-check semantic should still catch the cross-paste.
        ok, msg = validate_rapidapi_format("  sk-or-v1-fake-tester-key-abc123  ")
        assert ok is False
        assert "OpenRouter" in msg
