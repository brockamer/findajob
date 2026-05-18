"""Tests for the briefing-first gate UI on /materials/{fp}/ (#691).

When ``jobs.stage='briefing_ready'`` the folder view renders an
operator-decision panel above the materials: a Continue-prep button
(POSTs to /board/jobs/{fp}/continue-prep) and a Reject affordance with
a reject_reasons dropdown (POSTs to the existing /board/jobs/{fp}/reject).
Other stages are unchanged.

Coverage:

1. Briefing-ready stage renders the gate panel + the right POST targets.
2. Non-briefing stages don't render the panel.
3. The stage badge picks up the new ``briefing_ready`` color class.
4. When reject_reasons.yaml is present, dropdown surfaces its options.
5. When reject_reasons.yaml is missing, the form falls back to free-text input.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from findajob import audit
from findajob.onboarding import mark_complete
from findajob.web.app import create_app
from tests.conftest import ensure_view_prefs_table


def _build_pipeline_db(db_path: Path) -> None:
    from findajob.db.migrate import apply_pending

    conn = sqlite3.connect(db_path)
    try:
        apply_pending(conn)
        ensure_view_prefs_table(conn)
    finally:
        conn.close()


@pytest.fixture()
def folder_client(tmp_path: Path, monkeypatch):
    """Build a TestClient with one job at a chosen stage and a briefing folder."""
    monkeypatch.setattr(audit, "LOG_PATH", str(tmp_path / "events.jsonl"))

    def _make(*, stage: str, reject_reasons_yaml: str | None = None) -> TestClient:
        companies = tmp_path / "companies"
        companies.mkdir(exist_ok=True)
        folder = companies / "Acme_Eng_2026-05-13_120000"
        folder.mkdir(exist_ok=True)
        # Phase A produced a briefing — file presence matches what
        # `_run_prep_phase_a` writes.
        (folder / "Tester Briefing - Acme - Sr Ops - 20260513-120000.md").write_text("# Briefing\n\nBody.")
        (folder / "JD - Acme - Sr Ops.txt").write_text("JD body.")

        db_path = tmp_path / "pipeline.db"
        if not db_path.exists():
            _build_pipeline_db(db_path)
        conn = sqlite3.connect(db_path)
        conn.execute(
            "INSERT INTO jobs (id, fingerprint, url, title, company, source, stage, prep_folder_path) "
            "VALUES ('jid', 'fp', 'https://x.test', 'Sr Ops', 'Acme', 'test', ?, ?)",
            (stage, str(folder)),
        )
        conn.commit()
        conn.close()

        if reject_reasons_yaml is not None:
            config_dir = tmp_path / "config"
            config_dir.mkdir(exist_ok=True)
            (config_dir / "reject_reasons.yaml").write_text(reject_reasons_yaml)
            # config_loader resolves the path off BASE at import time —
            # point it at tmp_path for this client.
            monkeypatch.setattr("findajob.config_loader._REJECT_REASONS_PATH", config_dir / "reject_reasons.yaml")

        mark_complete(tmp_path)
        app = create_app(companies_root=companies, db_path=db_path, base_root=tmp_path)
        return TestClient(app)

    return _make


# ── gate panel presence / absence ─────────────────────────────────────────


def test_gate_panel_renders_at_briefing_ready(folder_client):
    """At stage='briefing_ready', the folder view shows the decision panel."""
    client = folder_client(stage="briefing_ready")
    resp = client.get("/materials/fp")
    assert resp.status_code == 200
    assert "Briefing ready" in resp.text
    assert "decide before continuing prep" in resp.text


def test_gate_panel_absent_for_materials_drafted(folder_client):
    """Other stages don't get the gate panel — only briefing_ready triggers it."""
    client = folder_client(stage="materials_drafted")
    resp = client.get("/materials/fp")
    assert resp.status_code == 200
    assert "Briefing ready" not in resp.text
    assert "decide before continuing prep" not in resp.text


def test_gate_panel_absent_for_scored(folder_client):
    client = folder_client(stage="scored")
    resp = client.get("/materials/fp")
    assert resp.status_code == 200
    assert "decide before continuing prep" not in resp.text


# ── POST targets ──────────────────────────────────────────────────────────


def test_continue_prep_button_points_at_materials_flow_endpoint(folder_client):
    """The Continue-prep form MUST post to the materials-flow wrapper
    (/materials/{fp}/continue-prep), NOT the dashboard-flow endpoint
    (/board/jobs/{fp}/continue-prep). The dashboard route returns an
    HTMX-shaped <tr>; a plain form POST would navigate the operator to
    a page whose body is a bare <tr>. Regression cover for the bug the
    advisor caught at the Session 2 pre-PR checkpoint."""
    client = folder_client(stage="briefing_ready")
    resp = client.get("/materials/fp")
    assert 'action="/materials/fp/continue-prep"' in resp.text
    # And NOT the dashboard-flow endpoint (the bug-prone version):
    assert 'action="/board/jobs/fp/continue-prep"' not in resp.text


def test_reject_form_posts_to_materials_flow_endpoint(folder_client):
    """Same reasoning as continue-prep: the dashboard-flow /reject returns
    HTMLResponse(""), which renders as a blank page on plain-form POST.
    The materials-flow wrapper calls the same handle_rejection helper
    (writes feedback_log, moves folder), then 303-redirects to /materials/."""
    client = folder_client(stage="briefing_ready")
    resp = client.get("/materials/fp")
    assert 'action="/materials/fp/reject"' in resp.text
    assert 'action="/board/jobs/fp/reject"' not in resp.text


# ── reject_reasons surface ────────────────────────────────────────────────


def test_reject_form_shows_dropdown_when_reasons_configured(folder_client):
    """When reject_reasons.yaml is present, the form surfaces the configured
    options + an "Other" escape hatch."""
    yaml = """\
reasons:
  - Wrong title fit
  - Out of geo
  - Comp too low
title_signal_reasons: []
"""
    client = folder_client(stage="briefing_ready", reject_reasons_yaml=yaml)
    resp = client.get("/materials/fp")
    assert "Wrong title fit" in resp.text
    assert "Out of geo" in resp.text
    assert "Comp too low" in resp.text
    # And the "Other" escape-hatch option must be present so the dropdown
    # isn't a hard gate on the configured list.
    assert ">Other<" in resp.text


def test_reject_form_uses_default_reasons_when_yaml_missing(folder_client):
    """No reject_reasons.yaml → load_reject_reasons returns the field-agnostic
    defaults so the dropdown still renders with sensible options. The empty
    fallback (free-text input) only triggers on a malformed yaml — see
    test_reject_form_falls_back_to_text_input_on_load_failure."""
    client = folder_client(stage="briefing_ready")  # no reject_reasons_yaml kw
    resp = client.get("/materials/fp")
    # Dropdown rendered with the default field-agnostic reasons.
    assert "<select" in resp.text
    assert 'name="reason"' in resp.text


def test_reject_form_falls_back_to_text_input_on_load_failure(folder_client, monkeypatch):
    """Defensive path: if load_reject_reasons raises (e.g. malformed yaml on
    a freshly-edited /settings/reject-reasons/ save), the gate still
    renders — with a free-text input so the operator can still reject."""
    from findajob.web.routes import materials as materials_module

    def _boom():
        raise materials_module.__dict__.get("ConfigError", RuntimeError)("simulated malformed yaml")

    # The route imports load_reject_reasons lazily; patch at module level.
    from findajob import config_loader as _cl

    monkeypatch.setattr(_cl, "load_reject_reasons", _boom)

    client = folder_client(stage="briefing_ready")
    resp = client.get("/materials/fp")
    # Free-text input variant — no <select> element.
    assert "<select" not in resp.text.split("decide before continuing prep")[-1].split("</aside>")[0]
    assert 'placeholder="e.g.' in resp.text


# ── stage badge ───────────────────────────────────────────────────────────


def test_stage_badge_uses_briefing_ready_class(folder_client):
    """The stage badge at the top of the page must pick up the new
    briefing_ready color class so the operator can distinguish it from
    prep_in_progress at a glance."""
    client = folder_client(stage="briefing_ready")
    resp = client.get("/materials/fp")
    # Teal palette per the template's stage_class map.
    assert "bg-teal-100" in resp.text
    assert "text-teal-800" in resp.text


# ── materials-flow route wrappers ─────────────────────────────────────────
#
# /materials/{fp}/continue-prep and /materials/{fp}/reject exist as 303-
# redirecting wrappers around their dashboard-flow counterparts so the
# server-rendered materials page can post plain forms without the operator
# landing on a bare <tr> or blank body. Cover behaviour + redirect targets.


@pytest.fixture()
def popen_calls(monkeypatch):
    """Capture subprocess.Popen invocations from the materials-flow route
    so /materials/{fp}/continue-prep doesn't actually fork prep_application.py."""
    calls: list[list[str]] = []

    class _FakePopen:
        pid = 99999

        def __init__(self, args, **_kw):
            calls.append(args)

    from findajob.web.routes import board_actions

    monkeypatch.setattr(board_actions.subprocess, "Popen", _FakePopen)
    return calls


def _fetch_stage(client: TestClient, fingerprint: str) -> str | None:
    conn = sqlite3.connect(client._db_path) if hasattr(client, "_db_path") else None
    if conn is None:
        # The folder_client fixture doesn't expose _db_path — recreate path.
        import tempfile  # noqa: F401 — unused; kept to mirror other test helpers

        return None
    row = conn.execute("SELECT stage FROM jobs WHERE fingerprint=?", (fingerprint,)).fetchone()
    conn.close()
    return row[0] if row else None


def _stage_from_tmp(tmp_path, fingerprint: str = "fp") -> str | None:
    """Read jobs.stage directly from the test DB; the folder_client fixture
    doesn't expose _db_path so we open the well-known tmp path ourselves."""
    db_path = tmp_path / "pipeline.db"
    if not db_path.exists():
        return None
    conn = sqlite3.connect(str(db_path))
    row = conn.execute("SELECT stage FROM jobs WHERE fingerprint=?", (fingerprint,)).fetchone()
    conn.close()
    return row[0] if row else None


class TestContinuePrepFromMaterials:
    def test_happy_path_advances_briefing_ready_to_prep_in_progress(self, folder_client, tmp_path, popen_calls):
        client = folder_client(stage="briefing_ready")
        resp = client.post("/materials/fp/continue-prep", follow_redirects=False)
        # 303 redirect — plain form submission lands the operator back on the materials page.
        assert resp.status_code == 303
        assert resp.headers["location"] == "/materials/fp"
        assert _stage_from_tmp(tmp_path) == "prep_in_progress"
        # Subprocess dispatched with --phase=b (not --phase=all → would double-charge).
        prep_calls = [c for c in popen_calls if "prep_application.py" in c[1]]
        assert len(prep_calls) == 1
        assert "--phase=b" in prep_calls[0]

    def test_404_on_unknown_fingerprint(self, folder_client, popen_calls):
        client = folder_client(stage="briefing_ready")
        resp = client.post("/materials/fp_nonexistent/continue-prep", follow_redirects=False)
        assert resp.status_code == 404
        assert popen_calls == []

    def test_409_on_scored_stage(self, folder_client, tmp_path, popen_calls):
        client = folder_client(stage="scored")
        resp = client.post("/materials/fp/continue-prep", follow_redirects=False)
        assert resp.status_code == 409
        assert _stage_from_tmp(tmp_path) == "scored"
        assert popen_calls == []

    def test_idempotent_on_prep_in_progress(self, folder_client, tmp_path, popen_calls):
        """Double-submit: second POST finds stage already advanced and redirects
        without dispatching another subprocess."""
        client = folder_client(stage="prep_in_progress")
        resp = client.post("/materials/fp/continue-prep", follow_redirects=False)
        assert resp.status_code == 303
        assert resp.headers["location"] == "/materials/fp"
        assert _stage_from_tmp(tmp_path) == "prep_in_progress"
        assert popen_calls == []

    def test_queue_full_redirects_with_error_param(self, folder_client, tmp_path, popen_calls):
        """When the 3-job cap is reached, redirect to /materials/ with an
        error param so the index page can surface a banner — mirrors the
        ?regen_error=queue_full convention from regenerate_from_materials."""
        client = folder_client(stage="briefing_ready")
        # Seed three in-flight prep rows to trip the cap.
        db_path = tmp_path / "pipeline.db"
        conn = sqlite3.connect(str(db_path))
        for n in range(3):
            conn.execute(
                "INSERT INTO jobs (id, fingerprint, url, title, company, source, stage) "
                "VALUES (?, ?, 'u', 'T', 'C', 'test', 'prep_in_progress')",
                (f"inflight-{n}", f"fp_inflight_{n}"),
            )
        conn.commit()
        conn.close()

        resp = client.post("/materials/fp/continue-prep", follow_redirects=False)
        assert resp.status_code == 303
        assert "continue_prep_error=queue_full" in resp.headers["location"]
        # Stage unchanged — gate refusals must not advance the row.
        assert _stage_from_tmp(tmp_path) == "briefing_ready"
        prep_calls = [c for c in popen_calls if "prep_application.py" in c[1]]
        assert prep_calls == []

    def test_spend_ceiling_redirects_with_error_param(self, folder_client, tmp_path, popen_calls, monkeypatch):
        """Patch findajob.spend_ceiling.check_launch_gate (the materials-flow
        route imports it directly from that module, unlike board_actions
        which imports + binds at module load)."""
        from findajob import spend_ceiling

        monkeypatch.setattr(
            spend_ceiling,
            "check_launch_gate",
            lambda _db: spend_ceiling.LaunchGateRefusal(ceiling_usd=50.0, current_sum_usd=51.23),
        )

        client = folder_client(stage="briefing_ready")
        resp = client.post("/materials/fp/continue-prep", follow_redirects=False)
        assert resp.status_code == 303
        assert "continue_prep_error=spend_ceiling" in resp.headers["location"]
        assert _stage_from_tmp(tmp_path) == "briefing_ready"
        prep_calls = [c for c in popen_calls if "prep_application.py" in c[1]]
        assert prep_calls == []


class TestRejectFromMaterials:
    def test_happy_path_flips_to_rejected_and_redirects(self, folder_client, tmp_path):
        client = folder_client(stage="briefing_ready")
        resp = client.post(
            "/materials/fp/reject",
            data={"reason": "Wrong title fit"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert resp.headers["location"] == "/materials/"
        assert _stage_from_tmp(tmp_path) == "rejected"

    def test_blank_reason_defaults_to_other(self, folder_client, tmp_path):
        client = folder_client(stage="briefing_ready")
        resp = client.post("/materials/fp/reject", data={"reason": ""}, follow_redirects=False)
        assert resp.status_code == 303
        assert _stage_from_tmp(tmp_path) == "rejected"
        # Confirm the helper applied the "Other" default.
        db_path = tmp_path / "pipeline.db"
        conn = sqlite3.connect(str(db_path))
        row = conn.execute("SELECT reject_reason FROM jobs WHERE fingerprint=?", ("fp",)).fetchone()
        conn.close()
        assert row[0] == "Other"

    def test_404_on_unknown_fingerprint(self, folder_client):
        client = folder_client(stage="briefing_ready")
        resp = client.post("/materials/fp_nonexistent/reject", data={"reason": "x"}, follow_redirects=False)
        assert resp.status_code == 404

    def test_idempotent_on_already_rejected(self, folder_client, tmp_path):
        """Operator re-submit after the row has already moved: redirect, no
        second handle_rejection call (would duplicate feedback_log)."""
        client = folder_client(stage="rejected")
        # Capture the feedback_log row count before — the idempotency assertion
        # is that this number doesn't change after the second POST.
        db_path = tmp_path / "pipeline.db"
        conn = sqlite3.connect(str(db_path))
        before = conn.execute("SELECT COUNT(*) FROM feedback_log").fetchone()[0]
        conn.close()

        resp = client.post("/materials/fp/reject", data={"reason": "Wrong fit"}, follow_redirects=False)
        assert resp.status_code == 303
        assert resp.headers["location"] == "/materials/"

        conn = sqlite3.connect(str(db_path))
        after = conn.execute("SELECT COUNT(*) FROM feedback_log").fetchone()[0]
        conn.close()
        assert before == after, "second submit must not duplicate feedback_log"
