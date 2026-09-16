"""Unit tests for findajob.web.verify_auth (#487).

Each failure mode of the post-deploy auth gate verifier must produce a
distinct non-zero exit code. The hard rule downstream of this module
(`docker compose down` on any non-zero exit) means the rule is only as
useful as the codes are reliable.
"""

from __future__ import annotations

import urllib.request
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


# --- probe URL / FINDAJOB_INTERNAL_PORT (#1062) -----------------------------


class _FakeResponse:
    """Minimal stand-in for what urlopen returns — status + headers only."""

    def __init__(self, status: int = 200, headers: dict[str, str] | None = None) -> None:
        self.status = status
        self.headers = headers or {}


def _urls_probed(monkeypatch: pytest.MonkeyPatch, responses: list[_FakeResponse]) -> list[str]:
    """Stub the network and record every URL the probe actually requested."""
    seen: list[str] = []
    queue = list(responses)

    def fake_urlopen(req: urllib.request.Request, timeout: float | None = None) -> _FakeResponse:
        seen.append(req.full_url)
        return queue.pop(0)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return seen


def test_probe_url_defaults_to_8090_when_env_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FINDAJOB_INTERNAL_PORT", raising=False)
    assert verify_auth._probe_url() == "http://127.0.0.1:8090/board/dashboard"


def test_probe_url_honors_findajob_internal_port(monkeypatch: pytest.MonkeyPatch) -> None:
    """Web-launched Fly apps serve 8080 (#1010/#1011) — probing 8090 is a false exit 5."""
    monkeypatch.setenv("FINDAJOB_INTERNAL_PORT", "8080")
    assert verify_auth._probe_url() == "http://127.0.0.1:8080/board/dashboard"


def test_probe_url_falls_back_when_env_is_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """Parity with ops/entrypoint.sh's ${FINDAJOB_INTERNAL_PORT:-8090}.

    Shell ``:-`` falls back on unset *or* empty; if this resolver treated a
    set-but-empty value as a port, it would probe a URL uvicorn never bound.
    """
    monkeypatch.setenv("FINDAJOB_INTERNAL_PORT", "   ")
    assert verify_auth._probe_url() == "http://127.0.0.1:8090/board/dashboard"


def test_probe_requests_the_env_configured_port(monkeypatch: pytest.MonkeyPatch) -> None:
    """The end-to-end form: the request that leaves _probe carries the right port."""
    monkeypatch.setenv("FINDAJOB_INTERNAL_PORT", "8080")
    seen = _urls_probed(monkeypatch, [_FakeResponse()])
    verify_auth._probe({})
    assert seen == ["http://127.0.0.1:8080/board/dashboard"]


def test_probe_requests_default_port_when_env_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FINDAJOB_INTERNAL_PORT", raising=False)
    seen = _urls_probed(monkeypatch, [_FakeResponse()])
    verify_auth._probe({})
    assert seen == ["http://127.0.0.1:8090/board/dashboard"]


def test_probe_url_is_resolved_after_load_env(creds_set: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """data/.env can carry the port, so the URL must be built after load_env().

    A module-level constant evaluated at import time would miss it. load_env is
    stubbed here so a real data/.env on the box can't make this flaky.
    """
    monkeypatch.delenv("FINDAJOB_INTERNAL_PORT", raising=False)

    def fake_load_env(path: str | None = None) -> dict[str, str]:
        monkeypatch.setenv("FINDAJOB_INTERNAL_PORT", "8081")
        return {}

    monkeypatch.setattr("findajob.paths.load_env", fake_load_env)
    seen = _urls_probed(
        monkeypatch,
        [
            _FakeResponse(401, {"WWW-Authenticate": 'Basic realm="findajob"'}),
            _FakeResponse(200, {}),
        ],
    )
    assert verify_auth.main() == 0
    assert seen == ["http://127.0.0.1:8081/board/dashboard"] * 2
