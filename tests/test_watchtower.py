import urllib.error
import urllib.request

from findajob.web import watchtower


def test_disabled_without_config(monkeypatch):
    monkeypatch.delenv("FINDAJOB_WATCHTOWER_HTTP_URL", raising=False)
    monkeypatch.delenv("FINDAJOB_WATCHTOWER_HTTP_TOKEN", raising=False)
    assert watchtower.watchtower_button_enabled() is False
    assert watchtower.trigger_watchtower_update() is False


def test_enabled_with_both_env(monkeypatch):
    monkeypatch.setenv("FINDAJOB_WATCHTOWER_HTTP_URL", "http://watchtower:8080")
    monkeypatch.setenv("FINDAJOB_WATCHTOWER_HTTP_TOKEN", "tok")
    assert watchtower.watchtower_button_enabled() is True


def test_disabled_with_only_one_env_var(monkeypatch):
    """Both vars are required — either one alone keeps the button off and the
    trigger a no-op."""
    monkeypatch.setenv("FINDAJOB_WATCHTOWER_HTTP_URL", "http://watchtower:8080")
    monkeypatch.delenv("FINDAJOB_WATCHTOWER_HTTP_TOKEN", raising=False)
    assert watchtower.watchtower_button_enabled() is False
    assert watchtower.trigger_watchtower_update() is False

    monkeypatch.delenv("FINDAJOB_WATCHTOWER_HTTP_URL", raising=False)
    monkeypatch.setenv("FINDAJOB_WATCHTOWER_HTTP_TOKEN", "tok")
    assert watchtower.watchtower_button_enabled() is False
    assert watchtower.trigger_watchtower_update() is False


class _FakeResp:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_trigger_posts_scoped_to_image(monkeypatch):
    monkeypatch.setenv("FINDAJOB_WATCHTOWER_HTTP_URL", "http://watchtower:8080")
    monkeypatch.setenv("FINDAJOB_WATCHTOWER_HTTP_TOKEN", "tok")
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["method"] = req.get_method()
        captured["auth"] = req.get_header("Authorization")
        return _FakeResp()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    assert watchtower.trigger_watchtower_update() is True
    assert captured["method"] == "POST"
    assert "image=ghcr.io/brockamer/findajob" in captured["url"]
    assert captured["auth"] == "Bearer tok"
    # The token must never ride in the URL (never-leak-secrets discipline).
    assert "tok" not in captured["url"]


def test_trigger_failopen_on_error(monkeypatch):
    monkeypatch.setenv("FINDAJOB_WATCHTOWER_HTTP_URL", "http://watchtower:8080")
    monkeypatch.setenv("FINDAJOB_WATCHTOWER_HTTP_TOKEN", "tok")

    def boom(*a, **k):
        raise urllib.error.URLError("refused")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    assert watchtower.trigger_watchtower_update() is False
