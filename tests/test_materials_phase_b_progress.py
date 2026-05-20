"""#738 — Materials page surfaces Phase B progress.

When the orchestrator runs Phase B (resume → cover → critique → outreach
after a Continue-prep dispatch), it writes a ``.phase_b_step`` sidecar
into the prep folder before each step. The materials page reads it and
renders "Phase B in progress — step N of 5: <label>" so the operator
sees a distinct progress signal instead of the generic "⟳ Regenerating…"
label (which is also what Phase A regen shows — undistinguishable was
the original bug).

Coverage:

1. With sidecar present and stage=prep_in_progress, the page renders the
   Phase B label including step number and human-readable action.
2. Without sidecar (Phase A regen path), the page falls back to the
   generic "⟳ Regenerating…" label.
3. Outside prep_in_progress, the sidecar is never read (defensive against
   stale sidecars from a prior Phase B that didn't clean up).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from findajob import audit
from findajob.onboarding import mark_complete
from findajob.web.app import create_app
from tests.conftest import init_test_db


@pytest.fixture()
def folder_client(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(audit, "LOG_PATH", str(tmp_path / "events.jsonl"))

    def _make(*, stage: str, sidecar_content: str | None = None) -> TestClient:
        companies = tmp_path / "companies"
        companies.mkdir(exist_ok=True)
        folder = companies / "Acme_Eng_2026-05-20_120000"
        folder.mkdir(exist_ok=True)
        (folder / "Tester Briefing - Acme - Sr Ops - 20260520-120000.md").write_text("# Briefing\n\nBody.")
        (folder / "JD - Acme - Sr Ops.txt").write_text("JD body.")
        if sidecar_content is not None:
            (folder / ".phase_b_step").write_text(sidecar_content)

        db_path = tmp_path / "pipeline.db"
        if not db_path.exists():
            init_test_db(db_path)
        conn = sqlite3.connect(db_path)
        conn.execute(
            "INSERT INTO jobs (id, fingerprint, url, title, company, source, stage, prep_folder_path) "
            "VALUES ('jid', 'fp', 'https://x.test', 'Sr Ops', 'Acme', 'test', ?, ?)",
            (stage, str(folder)),
        )
        conn.commit()
        conn.close()

        mark_complete(tmp_path)
        app = create_app(companies_root=companies, db_path=db_path, base_root=tmp_path)
        return TestClient(app)

    return _make


def test_phase_b_label_renders_when_sidecar_present(folder_client):
    """prep_in_progress + sidecar → 'Phase B in progress — step N of 5: <label>'."""
    client = folder_client(stage="prep_in_progress", sidecar_content="3/5 cover\n")
    resp = client.get("/materials/fp")
    assert resp.status_code == 200
    assert "Phase B in progress" in resp.text
    assert "step 3/5" in resp.text
    assert "drafting cover letter" in resp.text
    # And NOT the generic regenerate label — the whole point of #738.
    assert "⟳ Regenerating…" not in resp.text


def test_phase_b_label_maps_each_step_key(folder_client, tmp_path):
    """Each step key (resume/changes/cover/critique/outreach) maps to its
    human-readable label. Regression cover for the step→label map in the
    template that operators rely on to know which step is running."""
    # Build the client once (DB + folder setup are one-shot); then rewrite
    # the sidecar between requests since that's the only state #738 cares
    # about for the label-mapping contract.
    client = folder_client(stage="prep_in_progress", sidecar_content="1/5 resume\n")
    sidecar = tmp_path / "companies" / "Acme_Eng_2026-05-20_120000" / ".phase_b_step"

    expected = {
        "1/5 resume": "tailoring resume",
        "2/5 changes": "reviewing changes",
        "3/5 cover": "drafting cover letter",
        "4/5 critique": "recruiter critique",
        "5/5 outreach": "drafting outreach",
    }
    for content, label in expected.items():
        sidecar.write_text(f"{content}\n")
        resp = client.get("/materials/fp")
        assert label in resp.text, f"sidecar={content!r} should render label {label!r}; not found in response"


def test_generic_regenerating_label_when_no_sidecar(folder_client):
    """prep_in_progress without sidecar → fall back to generic label.

    This is the Phase A regen path (legacy ``--phase=all`` or the
    Regenerate button on /materials/{fp}/), which doesn't write the
    Phase B sidecar. The page must still render coherently."""
    client = folder_client(stage="prep_in_progress")  # no sidecar
    resp = client.get("/materials/fp")
    assert resp.status_code == 200
    assert "⟳ Regenerating…" in resp.text
    assert "Phase B in progress" not in resp.text


def test_sidecar_ignored_outside_prep_in_progress(folder_client):
    """A stale sidecar from a prior Phase B run must not bleed Phase B
    labeling into other stages — only ``prep_in_progress`` reads it."""
    client = folder_client(stage="materials_drafted", sidecar_content="5/5 outreach\n")
    resp = client.get("/materials/fp")
    assert resp.status_code == 200
    assert "Phase B in progress" not in resp.text
