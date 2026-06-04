import json
import urllib.error
import urllib.request
from datetime import UTC

import pytest

from findajob.web import update_check


@pytest.fixture(autouse=True)
def _reset_cache():
    update_check._cache["checked_at"] = None
    update_check._cache["latest"] = None
    yield


class _FakeResp:
    def __init__(self, body: bytes):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_fetch_latest_release_strips_leading_v(monkeypatch):
    envelope = json.dumps({"tag_name": "v0.33.0", "name": "v0.33.0"}).encode()
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _FakeResp(envelope))
    assert update_check.fetch_latest_release() == "0.33.0"


def test_fetch_latest_release_failopen_on_network_error(monkeypatch):
    def boom(*a, **k):
        raise urllib.error.URLError("no network")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    assert update_check.fetch_latest_release() is None


def test_refresh_cache_populates_latest(monkeypatch):
    monkeypatch.setattr(update_check, "fetch_latest_release", lambda: "0.34.0")
    update_check.refresh_cache()
    assert update_check.get_cached_latest() == "0.34.0"
    assert update_check._cache["checked_at"] is not None


def test_refresh_cache_failopen_keeps_prior_latest(monkeypatch):
    update_check._cache["latest"] = "0.33.0"
    monkeypatch.setattr(update_check, "fetch_latest_release", lambda: None)
    update_check.refresh_cache()
    assert update_check.get_cached_latest() == "0.33.0"  # unchanged
    assert update_check._cache["checked_at"] is not None  # but stamped


def test_maybe_schedule_refresh_only_when_stale(monkeypatch):
    class _BG:
        def __init__(self):
            self.tasks = []

        def add_task(self, fn, *a, **k):
            self.tasks.append(fn)

    # Never fetched → stale → schedules.
    bg = _BG()
    update_check.maybe_schedule_refresh(bg)
    assert len(bg.tasks) == 1

    # Fresh stamp → not stale → no schedule.
    from datetime import datetime

    update_check._cache["checked_at"] = datetime.now(UTC)
    bg2 = _BG()
    update_check.maybe_schedule_refresh(bg2)
    assert len(bg2.tasks) == 0


def test_detect_substrate(monkeypatch):
    monkeypatch.setenv("FLY_APP_NAME", "findajob-prod")
    assert update_check.detect_substrate() == "fly"
    monkeypatch.delenv("FLY_APP_NAME", raising=False)
    assert update_check.detect_substrate() == "docker"


class _Req:
    def __init__(self, cookies=None):
        self.cookies = cookies or {}


class _BGNoop:
    def add_task(self, fn, *a, **k):
        pass


def _fresh_stamp():
    from datetime import datetime

    update_check._cache["checked_at"] = datetime.now(UTC)


def test_banner_state_shows_when_newer(monkeypatch):
    monkeypatch.setattr(update_check, "findajob_version", lambda: "0.33.0")
    update_check._cache["latest"] = "0.34.0"
    _fresh_stamp()
    monkeypatch.delenv("FLY_APP_NAME", raising=False)
    banner = update_check.update_banner_state(_Req(), _BGNoop())
    assert banner is not None
    assert banner.current == "0.33.0"
    assert banner.latest == "0.34.0"
    assert banner.substrate == "docker"


def test_banner_state_none_when_uptodate(monkeypatch):
    monkeypatch.setattr(update_check, "findajob_version", lambda: "0.34.0")
    update_check._cache["latest"] = "0.34.0"
    _fresh_stamp()
    assert update_check.update_banner_state(_Req(), _BGNoop()) is None


def test_banner_state_none_when_no_cached_latest(monkeypatch):
    monkeypatch.setattr(update_check, "findajob_version", lambda: "0.33.0")
    _fresh_stamp()  # latest stays None
    assert update_check.update_banner_state(_Req(), _BGNoop()) is None


def test_banner_state_dismissed_for_this_exact_version(monkeypatch):
    monkeypatch.setattr(update_check, "findajob_version", lambda: "0.33.0")
    update_check._cache["latest"] = "0.34.0"
    _fresh_stamp()
    req = _Req(cookies={"update_banner_dismissed": "0.34.0"})
    assert update_check.update_banner_state(req, _BGNoop()) is None


def test_banner_state_resurfaces_after_newer_than_dismissed(monkeypatch):
    monkeypatch.setattr(update_check, "findajob_version", lambda: "0.33.0")
    update_check._cache["latest"] = "0.35.0"
    _fresh_stamp()
    # Dismissed 0.34.0 earlier; 0.35.0 is newer → show again.
    req = _Req(cookies={"update_banner_dismissed": "0.34.0"})
    assert update_check.update_banner_state(req, _BGNoop()) is not None


def test_banner_state_reports_watchtower_enabled(monkeypatch):
    monkeypatch.setattr(update_check, "findajob_version", lambda: "0.33.0")
    update_check._cache["latest"] = "0.34.0"
    _fresh_stamp()
    monkeypatch.setenv("FINDAJOB_WATCHTOWER_HTTP_URL", "http://watchtower:8080")
    monkeypatch.setenv("FINDAJOB_WATCHTOWER_HTTP_TOKEN", "tok")
    banner = update_check.update_banner_state(_Req(), _BGNoop())
    assert banner is not None and banner.watchtower_enabled is True


def test_dashboard_renders_update_banner(tmp_path, monkeypatch):
    """Integration: banner text appears on the dashboard when an update is available."""

    from fastapi.testclient import TestClient

    from findajob.onboarding import mark_complete
    from findajob.web import update_check as uc
    from findajob.web.app import create_app
    from tests.conftest import init_test_db

    db = tmp_path / "pipeline.db"
    init_test_db(db)
    companies = tmp_path / "companies"
    companies.mkdir()
    mark_complete(tmp_path)

    from datetime import datetime

    uc._cache["latest"] = "0.34.0"
    uc._cache["checked_at"] = datetime.now(UTC)
    monkeypatch.setattr(uc, "findajob_version", lambda: "0.33.0")
    monkeypatch.delenv("FLY_APP_NAME", raising=False)

    client = TestClient(create_app(companies_root=companies, db_path=db, base_root=tmp_path))
    resp = client.get("/board/dashboard")
    assert resp.status_code == 200
    assert "Update available" in resp.text
    assert "0.34.0" in resp.text
