"""Unit tests for findajob.web.verify_auth (#487).

Each failure mode of the post-deploy auth gate verifier must produce a
distinct non-zero exit code. The hard rule downstream of this module
(`docker compose down` on any non-zero exit) means the rule is only as
useful as the codes are reliable.
"""

from __future__ import annotations

import email.message
import urllib.error
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


# --- probe port resolution (#1062) ----------------------------------------
#
# These tests patch at the `urllib` layer rather than `verify_auth._probe`,
# because the port lives in the URL that `_probe` builds — patching `_probe`
# itself (as every test above does) would step over the code under test.


def _headers(**kv: str) -> email.message.Message:
    msg = email.message.Message()
    for k, v in kv.items():
        msg[k.replace("_", "-")] = v
    return msg


class _FakeOK:
    """Minimal stand-in for urllib's response object."""

    status = 200
    headers = _headers()


@pytest.fixture
def no_env_file(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep `main()`'s `load_env()` from importing the operator's data/.env.

    Otherwise a machine with FINDAJOB_INTERNAL_PORT in data/.env would
    override monkeypatch's environment and make these assertions flap.
    """
    monkeypatch.setattr("findajob.paths.load_env", lambda *a, **kw: {})


@pytest.fixture
def probed_urls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record every URL urlopen is handed; 401-then-200 for a healthy gate."""
    urls: list[str] = []
    responses: list[object] = [
        urllib.error.HTTPError(
            "unused", 401, "Unauthorized", _headers(WWW_Authenticate='Basic realm="findajob"'), None
        ),
        _FakeOK(),
    ]

    def fake_urlopen(req: urllib.request.Request, timeout: float | None = None) -> object:
        urls.append(req.full_url)
        result = responses.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return urls


def test_probe_url_uses_internal_port_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fly's web-launch path serves 8080 (see fly.toml [env])."""
    monkeypatch.setenv("FINDAJOB_INTERNAL_PORT", "8080")
    assert verify_auth._probe_url() == "http://127.0.0.1:8080/board/dashboard"


def test_probe_url_falls_back_to_8090_when_env_var_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FINDAJOB_INTERNAL_PORT", raising=False)
    assert verify_auth._probe_url() == "http://127.0.0.1:8090/board/dashboard"


def test_probe_url_falls_back_to_8090_when_env_var_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mirrors `${FINDAJOB_INTERNAL_PORT:-8090}` — empty is unset, not port ''."""
    monkeypatch.setenv("FINDAJOB_INTERNAL_PORT", "  ")
    assert verify_auth._probe_url() == "http://127.0.0.1:8090/board/dashboard"


def test_main_probes_env_port_on_web_launched_instance(
    creds_set: None,
    no_env_file: None,
    probed_urls: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A healthy 8080 instance must exit 0, not 5 on a refused 8090 (#1062)."""
    monkeypatch.setenv("FINDAJOB_INTERNAL_PORT", "8080")
    assert verify_auth.main() == 0
    assert probed_urls == ["http://127.0.0.1:8080/board/dashboard"] * 2


def test_main_probes_8090_when_env_port_unset(
    creds_set: None,
    no_env_file: None,
    probed_urls: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The compose / CLI-deploy default is unchanged."""
    monkeypatch.delenv("FINDAJOB_INTERNAL_PORT", raising=False)
    assert verify_auth.main() == 0
    assert probed_urls == ["http://127.0.0.1:8090/board/dashboard"] * 2
