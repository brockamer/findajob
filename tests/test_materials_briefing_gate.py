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


def _build_pipeline_db(db_path: Path) -> None:
    from findajob.db.migrate import apply_pending

    conn = sqlite3.connect(db_path)
    try:
        apply_pending(conn)
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


def test_continue_prep_button_points_at_correct_endpoint(folder_client):
    """The Continue-prep affordance must POST to /board/jobs/{fp}/continue-prep —
    NOT the materials regenerate route."""
    client = folder_client(stage="briefing_ready")
    resp = client.get("/materials/fp")
    assert 'action="/board/jobs/fp/continue-prep"' in resp.text
    assert 'method="POST"' in resp.text


def test_reject_form_posts_to_board_reject_route(folder_client):
    """Reject from the briefing surface uses the same /reject endpoint as the
    board tabs — handle_rejection writes feedback_log so the scorer still
    learns from this decision."""
    client = folder_client(stage="briefing_ready")
    resp = client.get("/materials/fp")
    assert 'action="/board/jobs/fp/reject"' in resp.text


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
