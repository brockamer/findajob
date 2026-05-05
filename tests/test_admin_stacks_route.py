# tests/test_admin_stacks_route.py
"""Tests for /admin/stacks/ route."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from findajob.web.app import create_app
from tests.conftest_admin import build_pipeline_db, build_pipeline_jsonl


@pytest.fixture
def operator_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Build an operator-mode app whose admin route reads from a tmp /opt/stacks."""
    monkeypatch.setenv("FINDAJOB_OPERATOR_MODE", "1")
    monkeypatch.setenv("FINDAJOB_ADMIN_STACKS_ROOT", str(tmp_path / "stacks"))
    companies = tmp_path / "companies"
    companies.mkdir()
    db = tmp_path / "pipeline.db"
    db.touch()
    # Onboarding guard — synthesize the sentinel so /admin/stacks/ isn't gated.
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / ".onboarding-complete").write_text("ok")
    return create_app(companies_root=companies, db_path=db, base_root=tmp_path)


@pytest.fixture
def tester_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Tester-mode (operator flag unset) — admin route should not exist."""
    monkeypatch.delenv("FINDAJOB_OPERATOR_MODE", raising=False)
    companies = tmp_path / "companies"
    companies.mkdir()
    db = tmp_path / "pipeline.db"
    db.touch()
    return create_app(companies_root=companies, db_path=db, base_root=tmp_path)


def _seed_stack(stacks_root: Path, handle: str, *, with_data: bool = True) -> None:
    sp_root = stacks_root / f"findajob-{handle}" / "state"
    if with_data:
        build_pipeline_db(
            sp_root / "data" / "pipeline.db",
            rows=[{"id": f"{handle}-1", "stage": "scored"}],
        )
        build_pipeline_jsonl(
            sp_root / "logs" / "pipeline.jsonl",
            [{"ts": "2026-04-30T11:00:00+00:00", "event": "pipeline_complete"}],
        )
    else:
        sp_root.mkdir(parents=True)


def test_route_returns_404_when_operator_mode_off(tester_app) -> None:
    client = TestClient(tester_app)
    r = client.get("/admin/stacks/")
    assert r.status_code == 404


def test_route_returns_200_in_operator_mode(operator_app, tmp_path: Path) -> None:
    stacks = tmp_path / "stacks"
    stacks.mkdir()
    _seed_stack(stacks, "alice")
    _seed_stack(stacks, "dave")

    client = TestClient(operator_app)
    r = client.get("/admin/stacks/")
    assert r.status_code == 200
    assert ">alice<" in r.text
    assert ">dave<" in r.text


def test_pure_alphabetical_when_no_operator_handle(
    operator_app, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When FINDAJOB_OPERATOR_HANDLE is unset, rows sort pure alphabetical."""
    monkeypatch.delenv("FINDAJOB_OPERATOR_HANDLE", raising=False)
    stacks = tmp_path / "stacks"
    stacks.mkdir()
    for h in ("tango", "alice", "papa"):
        _seed_stack(stacks, h)

    client = TestClient(operator_app)
    r = client.get("/admin/stacks/")
    body = r.text
    pos = lambda s: body.find(s)  # noqa: E731
    assert pos(">alice<") < pos(">papa<") < pos(">tango<")


def test_operator_handle_floats_to_top(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When FINDAJOB_OPERATOR_HANDLE=papa, papa renders first; rest alphabetical."""
    monkeypatch.setenv("FINDAJOB_OPERATOR_MODE", "1")
    monkeypatch.setenv("FINDAJOB_OPERATOR_HANDLE", "papa")
    stacks = tmp_path / "stacks"
    stacks.mkdir()
    monkeypatch.setenv("FINDAJOB_ADMIN_STACKS_ROOT", str(stacks))
    for h in ("tango", "alice", "papa", "dave"):
        _seed_stack(stacks, h)
    companies = tmp_path / "companies"
    companies.mkdir()
    db = tmp_path / "pipeline.db"
    db.touch()
    # Onboarding guard — synthesize the sentinel so /admin/stacks/ isn't gated.
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / ".onboarding-complete").write_text("ok")
    app = create_app(companies_root=companies, db_path=db, base_root=tmp_path)
    client = TestClient(app)
    r = client.get("/admin/stacks/")
    body = r.text
    pos = lambda s: body.find(s)  # noqa: E731
    # papa first, then alphabetical: alice, dave, tango.
    assert pos(">papa<") < pos(">alice<") < pos(">dave<") < pos(">tango<")


def test_empty_state_banner_when_no_stacks(operator_app, tmp_path: Path) -> None:
    (tmp_path / "stacks").mkdir()
    client = TestClient(operator_app)
    r = client.get("/admin/stacks/")
    assert r.status_code == 200
    assert "No stacks found" in r.text


def test_per_row_error_does_not_crash_page(operator_app, tmp_path: Path) -> None:
    stacks = tmp_path / "stacks"
    stacks.mkdir()
    _seed_stack(stacks, "alice")
    # Broken stack: write garbage as DB.
    broken = stacks / "findajob-broken" / "state" / "data"
    broken.mkdir(parents=True)
    (broken / "pipeline.db").write_bytes(b"not a sqlite file")
    (stacks / "findajob-broken" / "state" / "logs").mkdir()
    (stacks / "findajob-broken" / "state" / "logs" / "pipeline.jsonl").write_text("")

    client = TestClient(operator_app)
    r = client.get("/admin/stacks/")
    assert r.status_code == 200
    assert ">alice<" in r.text
    assert ">broken<" in r.text


def test_drill_down_recipe_renders_per_row(operator_app, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FINDAJOB_DEPLOYMENT_HOST", "test-host")
    stacks = tmp_path / "stacks"
    stacks.mkdir()
    _seed_stack(stacks, "alice")
    client = TestClient(operator_app)
    r = client.get("/admin/stacks/")
    assert "ssh test-host tail -F /opt/stacks/findajob-alice/state/logs/pipeline.jsonl" in r.text


def test_basic_auth_inherited_when_set(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FINDAJOB_OPERATOR_MODE", "1")
    monkeypatch.setenv("FINDAJOB_ADMIN_STACKS_ROOT", str(tmp_path / "stacks"))
    monkeypatch.setenv("FINDAJOB_AUTH_USER", "op")
    monkeypatch.setenv("FINDAJOB_AUTH_PASS", "secret")
    (tmp_path / "stacks").mkdir()
    companies = tmp_path / "companies"
    companies.mkdir()
    db = tmp_path / "pipeline.db"
    db.touch()
    # Onboarding guard — synthesize the sentinel so /admin/stacks/ isn't gated.
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / ".onboarding-complete").write_text("ok")
    app = create_app(companies_root=companies, db_path=db, base_root=tmp_path)
    client = TestClient(app)
    # No auth header → 401
    r = client.get("/admin/stacks/")
    assert r.status_code == 401
    # With auth → 200
    import base64

    creds = base64.b64encode(b"op:secret").decode()
    r = client.get("/admin/stacks/", headers={"Authorization": f"Basic {creds}"})
    assert r.status_code == 200


def test_render_under_2s(operator_app, tmp_path: Path) -> None:
    """Performance budget per spec §4.6 — <2s for 6 stacks even with multi-MB
    pipeline.jsonl files. The bounded-tail design (tail_events max_bytes=1MB)
    is what makes this hold; seeding ~6MB JSONL per stack proves it instead
    of running the test against trivial fixtures (the previous 500-event ~30KB
    files exercised the happy path without proving the bound matters).

    Operator's stack pipeline.jsonl was ~10MB on 2026-04-30; long-running
    tester stacks will land in the same range.
    """
    import json
    import time

    stacks = tmp_path / "stacks"
    stacks.mkdir()
    # ~61 bytes/line × 90_000 ≈ 5.5 MB per stack. String-multiply instead of
    # json.dumps-per-event to keep setup cost out of the perf measurement.
    filler_line = json.dumps({"ts": "2026-04-30T11:00:00+00:00", "event": "watchdog_run"}) + "\n"
    filler_block = filler_line * 90_000
    completion_line = json.dumps({"ts": "2026-04-30T11:30:00+00:00", "event": "pipeline_complete"}) + "\n"

    for h in ("alice", "dave", "ed", "judy", "papa", "tango"):
        sp_root = stacks / f"findajob-{h}" / "state"
        rows = [{"id": f"{h}-{i}", "stage": "scored"} for i in range(50)]
        build_pipeline_db(sp_root / "data" / "pipeline.db", rows=rows)
        jsonl_path = sp_root / "logs" / "pipeline.jsonl"
        jsonl_path.parent.mkdir(parents=True, exist_ok=True)
        # Completion event LAST so newest-first tail starts there and freshness
        # is computed without reading the whole file.
        jsonl_path.write_text(filler_block + completion_line)
        # Sanity: prove fixture actually produced multi-MB files.
        assert jsonl_path.stat().st_size > 5_000_000, f"fixture for {h} only {jsonl_path.stat().st_size} bytes"

    client = TestClient(operator_app)
    t0 = time.perf_counter()
    r = client.get("/admin/stacks/")
    elapsed = time.perf_counter() - t0
    assert r.status_code == 200
    assert elapsed < 2.0, f"render took {elapsed:.2f}s — over 2s budget"
