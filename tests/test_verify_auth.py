"""Unit tests for findajob.web.verify_auth (#487).

Each failure mode of the post-deploy auth gate verifier must produce a
distinct non-zero exit code. The hard rule downstream of this module
(`docker compose down` on any non-zero exit) means the rule is only as
useful as the codes are reliable.
"""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import patch

import pytest

from findajob.web import verify_auth


@pytest.fixture
def creds_set(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("FINDAJOB_AUTH_USER", "tester")
    monkeypatch.setenv("FINDAJOB_AUTH_PASS", "s3cret")
    yield


def test_returns_2_when_both_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FINDAJOB_AUTH_USER", raising=False)
    monkeypatch.delenv("FINDAJOB_AUTH_PASS", raising=False)
    assert verify_auth.main() == 2


def test_returns_2_when_user_set_but_pass_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FINDAJOB_AUTH_USER", "tester")
    monkeypatch.setenv("FINDAJOB_AUTH_PASS", "")
    assert verify_auth.main() == 2


def test_returns_2_when_pass_set_but_user_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FINDAJOB_AUTH_USER", "")
    monkeypatch.setenv("FINDAJOB_AUTH_PASS", "s3cret")
    assert verify_auth.main() == 2


def test_returns_3_when_anonymous_returns_200(creds_set: None) -> None:
    """Auth gate not enforcing — anonymous request reached the app body."""
    with patch.object(verify_auth, "_probe", return_value=(200, {})):
        assert verify_auth.main() == 3


def test_returns_3_when_401_lacks_www_authenticate_header(creds_set: None) -> None:
    with patch.object(verify_auth, "_probe", return_value=(401, {})):
        assert verify_auth.main() == 3


def test_returns_3_when_www_authenticate_uses_wrong_scheme(creds_set: None) -> None:
    with patch.object(verify_auth, "_probe", return_value=(401, {"WWW-Authenticate": "Bearer xyz"})):
        assert verify_auth.main() == 3


def test_accepts_lowercase_www_authenticate_header(creds_set: None) -> None:
    """urllib often lowercases header keys; verifier must handle both casings."""
    sequence = [
        (401, {"www-authenticate": 'Basic realm="findajob"'}),
        (200, {}),
    ]
    with patch.object(verify_auth, "_probe", side_effect=sequence):
        assert verify_auth.main() == 0


def test_returns_4_when_authenticated_request_returns_500(creds_set: None) -> None:
    """Anon probe correct, but creds don't authorize through to a 200."""
    sequence = [
        (401, {"WWW-Authenticate": 'Basic realm="findajob"'}),
        (500, {}),
    ]
    with patch.object(verify_auth, "_probe", side_effect=sequence):
        assert verify_auth.main() == 4


def test_returns_4_when_authenticated_request_returns_401(creds_set: None) -> None:
    """Configured creds don't actually unlock — middleware reads different env."""
    sequence = [
        (401, {"WWW-Authenticate": 'Basic realm="findajob"'}),
        (401, {"WWW-Authenticate": 'Basic realm="findajob"'}),
    ]
    with patch.object(verify_auth, "_probe", side_effect=sequence):
        assert verify_auth.main() == 4


def test_returns_5_when_anonymous_probe_raises(creds_set: None) -> None:
    """Network blip or app not yet booted — distinct from gate-misconfigured."""
    with patch.object(verify_auth, "_probe", side_effect=ConnectionRefusedError("boom")):
        assert verify_auth.main() == 5


def test_returns_5_when_authenticated_probe_raises(creds_set: None) -> None:
    sequence = [
        (401, {"WWW-Authenticate": 'Basic realm="findajob"'}),
    ]

    def probe_side_effect(_headers: dict[str, str]) -> tuple[int, dict[str, str]]:
        if sequence:
            return sequence.pop(0)
        raise TimeoutError("authed probe stalled")

    with patch.object(verify_auth, "_probe", side_effect=probe_side_effect):
        assert verify_auth.main() == 5


def test_returns_0_on_healthy_gate(creds_set: None) -> None:
    sequence = [
        (401, {"WWW-Authenticate": 'Basic realm="findajob"'}),
        (200, {}),
    ]
    with patch.object(verify_auth, "_probe", side_effect=sequence):
        assert verify_auth.main() == 0


def test_probe_url_defaults_to_8090_when_port_env_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """When FINDAJOB_INTERNAL_PORT is unset, the probe falls back to 8090.

    Matches the compose / docker / CLI-deployed-Fly default; a healthy 8090
    stack must still be probed on 8090. Without this test, a future
    refactor that drops the fallback (or moves the default port) silently
    regresses every docker stack.
    """
    monkeypatch.delenv("FINDAJOB_INTERNAL_PORT", raising=False)
    assert verify_auth._probe_url() == "http://127.0.0.1:8090/board/dashboard"


def test_probe_url_uses_findajob_internal_port_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """When FINDAJOB_INTERNAL_PORT is set, the probe uses it verbatim.

    Web-launched Fly apps set FINDAJOB_INTERNAL_PORT=8080 (#1010/#1011) —
    a hardcoded 8090 probe would hit a closed port and falsely report exit 5
    on a healthy gate, triggering the documented tear-down contract.
    """
    monkeypatch.setenv("FINDAJOB_INTERNAL_PORT", "8080")
    assert verify_auth._probe_url() == "http://127.0.0.1:8080/board/dashboard"


def test_probe_requests_the_env_driven_url(creds_set: None) -> None:
    """Wiring guard: `_probe` requests the URL `_probe_url()` builds.

    Asserts the probe and the URL builder share the same source of truth —
    catches a regression where one is updated and the other isn't (e.g. a
    stale `_PROBE_URL` constant resurfacing). We capture via
    `urllib.request.urlopen` rather than patching `_probe`, because patching
    `_probe` with `side_effect` replaces it with a Mock that never invokes
    `_probe_url()` at all.
    """
    captured: list[str] = []

    class _FakeResponse:
        def __init__(self, status: int, headers: dict[str, str]) -> None:
            self.status = status
            self.headers = headers

        def __enter__(self) -> _FakeResponse:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    responses = iter(
        [
            _FakeResponse(401, {"WWW-Authenticate": 'Basic realm="findajob"'}),
            _FakeResponse(200, {}),
        ]
    )

    def fake_urlopen(req: object, timeout: float = 0) -> _FakeResponse:  # noqa: ARG001
        captured.append(req.full_url)  # type: ignore[attr-defined]
        return next(responses)

    with patch("urllib.request.urlopen", fake_urlopen):
        assert verify_auth.main() == 0

    expected = verify_auth._probe_url()
    assert captured == [expected, expected]  # anon + authed probes both hit it
