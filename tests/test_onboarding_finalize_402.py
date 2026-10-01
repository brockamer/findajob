"""Regression tests for #631 — graceful 402 PaymentRequired in /onboarding/.../finalize.

When OpenRouter returns 402 during finalize:
- The route must surface HTTP 402 (not generic 400), with the explicit
  "Add credits at openrouter.ai" message.
- No config files may survive on disk (transactional emit: all-or-none).
- The session must remain finalizable: a second click after credit
  top-up writes the files cleanly and marks the session complete.

Adjacent bug #624 reproducer: initial finalize attempts logged 400 and
left blank config files, which then broke /onboarding/feed-config.

The voice-samples redact LLM call (the other path inside inject() that
can hit OpenRouter) is intentionally NOT covered by a separate 402 test
here. `findajob.onboarding.voice_processor.redact_voice_samples` catches
OpenRouterError, logs it, and returns the structurally-cleaned body with
``success=False`` (see voice_processor.py:118-125). The user-supplied
key is the same one the smoke check is about to verify, so a 402 during
voice-samples manifests at the smoke check that fires immediately after,
on the path these tests already cover.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from findajob.web.app import create_app

_USER_KEY = "sk-or-v1-user-test"

# The exact message verify_openrouter_key emits on a 402 — used to
# trigger the route's status-code-402 branch via monkeypatch on the
# injector's `verify_openrouter_key` import.
_PAYMENT_REQUIRED_MSG = (
    "OpenRouter rejected the request (402 Payment Required). Add prepaid "
    "credit to your OpenRouter account at https://openrouter.ai/credits and "
    "re-paste."
)


def _build_emission_blob() -> str:
    from findajob.onboarding.parser import ALLOWED_FILENAMES

    parts = []
    for name in ALLOWED_FILENAMES:
        parts.append(f"<<<FILE: {name}>>>\nbody for {name}\n<<<END FILE: {name}>>>")
    return "\n\n".join(parts)


@pytest.fixture
def base_root(tmp_path: Path) -> Path:
    (tmp_path / "data").mkdir()
    (tmp_path / "companies").mkdir()
    (tmp_path / "candidate_context").mkdir()
    (tmp_path / "config" / "roles").mkdir(parents=True)
    repo_role = Path(__file__).parent.parent / "config" / "roles" / "onboarding_interviewer.md"
    shutil.copy(repo_role, tmp_path / "config" / "roles" / "onboarding_interviewer.md")

    db_path = tmp_path / "data" / "pipeline.db"
    conn = sqlite3.connect(db_path)
    try:
        from findajob.db.migrate import apply_pending

        apply_pending(conn)
    finally:
        conn.close()
    return tmp_path


@pytest.fixture
def client(base_root: Path) -> TestClient:
    app = create_app(
        companies_root=base_root / "companies",
        db_path=base_root / "data" / "pipeline.db",
        base_root=base_root,
    )
    return TestClient(app, follow_redirects=False)


def _prepare_finalize_ready_session(base_root: Path) -> str:
    """Create a session with the instance key set + all blocks captured, ready for /finalize."""
    from findajob.onboarding.parser import parse_emission
    from findajob.onboarding.session_store import (
        create_session,
        update_captured_blocks,
    )

    os.environ["OPENROUTER_API_KEY"] = _USER_KEY
    conn = sqlite3.connect(base_root / "data" / "pipeline.db")
    try:
        sid = create_session(conn)
        captured = parse_emission(_build_emission_blob()).found
        update_captured_blocks(conn, sid, captured)
    finally:
        conn.close()
    return sid


def _read_session_completed_at(base_root: Path, session_id: str) -> str | None:
    conn = sqlite3.connect(base_root / "data" / "pipeline.db")
    try:
        row = conn.execute(
            "SELECT completed_at FROM onboarding_sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    return row[0]


# ── Unit: OnboardingSmokeCheckFailed carries the 402 status code ────────


def test_smoke_check_failed_carries_status_code() -> None:
    """The exception must carry a status_code so the route can propagate
    402 (vs the default 400 for other auth/throttle failures)."""
    from findajob.onboarding.openrouter_smoke import OnboardingSmokeCheckFailed

    exc = OnboardingSmokeCheckFailed("simulated 402", status_code=402)
    assert exc.status_code == 402

    default = OnboardingSmokeCheckFailed("any other failure")
    assert default.status_code == 400


# ── Unit: inject() raises 402-flavored exception on payment-required ─────


def test_inject_raises_402_status_on_payment_required(base_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When verify_openrouter_key reports the 402 message, the raised
    OnboardingSmokeCheckFailed must carry status_code=402."""
    import findajob.onboarding.injector as inj_mod
    from findajob.onboarding import OnboardingSmokeCheckFailed

    monkeypatch.setattr(inj_mod, "verify_openrouter_key", lambda _k: (False, _PAYMENT_REQUIRED_MSG))

    found = {
        "profile.md": "# Profile\n",
        "master_resume.md": "# Resume\n",
        "target_companies.md": "## Tier 1\n- Acme\n",
        "business_sector_employers_reference.md": "## Cats\n",
        "prefilter_rules.yaml": "hard_rejects: {}\n",
        "in_domain_patterns.yaml": "positive: []\n",
        "reject_reasons.yaml": "reasons:\n  - 'Other'\ntitle_signal_reasons: []\n",
        "display_name.txt": "Test",
        "timezone.txt": "America/Los_Angeles",
        "ntfy_topic.txt": "test-topic",
    }
    with pytest.raises(OnboardingSmokeCheckFailed) as excinfo:
        inj_mod.inject(base_root, found, openrouter_api_key="bad-key")
    assert excinfo.value.status_code == 402


# ── Unit: transactional emit — no config files when smoke fails ──────────


def test_inject_smoke_check_failure_leaves_no_committed_files(base_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Smoke check must run BEFORE the os.replace commit loop. If smoke
    fails (any reason — 401, 402, network), no config file lands on disk.

    This is the heart of #631 AC#2 (transactional emit): the previous
    design committed first and verified after, leaving blank/orphaned
    files when the user encountered a 402 during finalize."""
    import findajob.onboarding.injector as inj_mod
    from findajob.onboarding import OnboardingSmokeCheckFailed

    monkeypatch.setattr(inj_mod, "verify_openrouter_key", lambda _k: (False, _PAYMENT_REQUIRED_MSG))

    found = {
        "profile.md": "# Profile body\n",
        "master_resume.md": "# Resume body\n",
        "target_companies.md": "## Tier 1\n- Acme\n",
        "business_sector_employers_reference.md": "## Cats\n",
        "prefilter_rules.yaml": "hard_rejects: {}\n",
        "in_domain_patterns.yaml": "positive: []\n",
        "reject_reasons.yaml": "reasons:\n  - 'Other'\ntitle_signal_reasons: []\n",
        "display_name.txt": "Test",
        "timezone.txt": "America/Los_Angeles",
        "ntfy_topic.txt": "test-topic",
    }
    with pytest.raises(OnboardingSmokeCheckFailed):
        inj_mod.inject(base_root, found, openrouter_api_key="bad-key")

    # AC#2: not a single config file may exist after the failed finalize
    assert not (base_root / "candidate_context" / "profile.md").exists()
    assert not (base_root / "candidate_context" / "master_resume.md").exists()
    assert not (base_root / "config" / "target_companies.md").exists()
    assert not (base_root / "config" / "prefilter_rules.yaml").exists()
    assert not (base_root / "data" / ".env").exists()
    # And of course, the sentinel must not exist
    assert not (base_root / "data" / ".onboarding-complete").exists()


# ── Route: /finalize returns HTTP 402 when smoke check reports 402 ───────


def test_finalize_returns_402_when_smoke_check_hits_payment_required(
    client: TestClient, base_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC#1: the route surfaces HTTP 402 (not 400) when the user is out
    of OpenRouter credit. Response body names the recovery path."""
    import findajob.onboarding.injector as inj_mod

    monkeypatch.setattr(inj_mod, "verify_openrouter_key", lambda _k: (False, _PAYMENT_REQUIRED_MSG))

    sid = _prepare_finalize_ready_session(base_root)
    resp = client.post(f"/onboarding/interview/{sid}/finalize")

    assert resp.status_code == 402
    # The recovery path must be discoverable from the response
    assert "openrouter.ai/credits" in resp.text.lower() or "402" in resp.text


def test_finalize_402_leaves_no_partial_config_files(
    client: TestClient, base_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC#2 + #624 reproducer: a 402 during finalize must not leave any
    config files on disk. The previous behavior committed files first
    and verified after, so a 402 left blank/orphaned files that then
    broke /onboarding/feed-config and the next interview re-start."""
    import findajob.onboarding.injector as inj_mod

    monkeypatch.setattr(inj_mod, "verify_openrouter_key", lambda _k: (False, _PAYMENT_REQUIRED_MSG))

    sid = _prepare_finalize_ready_session(base_root)
    client.post(f"/onboarding/interview/{sid}/finalize")

    assert not (base_root / "candidate_context" / "profile.md").exists()
    assert not (base_root / "config" / "target_companies.md").exists()
    assert not (base_root / "config" / "prefilter_rules.yaml").exists()
    # data/.env carries the secret API key — most security-sensitive write.
    # A failed finalize must NOT leave the key on disk under a half-onboarded
    # state where the operator might assume the stack is unconfigured.
    assert not (base_root / "data" / ".env").exists()
    assert not (base_root / "data" / ".onboarding-complete").exists()

    # Session must not be marked complete — it stays resumable
    assert _read_session_completed_at(base_root, sid) is None


def test_finalize_session_resumable_after_402_recovery(
    client: TestClient, base_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC#3: after a 402, the user adds credits and clicks Finalize
    again. The second call must succeed — files written, session
    marked complete, redirect to the next gate."""
    import findajob.onboarding.injector as inj_mod

    # First call: simulate out-of-credit
    calls: list[str] = []

    def _flaky_verify(_k: str) -> tuple[bool, str | None]:
        if not calls:
            calls.append("first")
            return False, _PAYMENT_REQUIRED_MSG
        calls.append("retry")
        return True, None

    monkeypatch.setattr(inj_mod, "verify_openrouter_key", _flaky_verify)

    sid = _prepare_finalize_ready_session(base_root)

    # First Finalize click → 402, no files
    resp1 = client.post(f"/onboarding/interview/{sid}/finalize")
    assert resp1.status_code == 402
    assert not (base_root / "candidate_context" / "profile.md").exists()
    assert _read_session_completed_at(base_root, sid) is None

    # Operator adds credits, retries — second Finalize click → success
    resp2 = client.post(f"/onboarding/interview/{sid}/finalize")
    assert resp2.status_code == 303
    assert resp2.headers["location"].startswith("/onboarding/")
    assert (base_root / "candidate_context" / "profile.md").exists()
    assert _read_session_completed_at(base_root, sid) is not None
    assert calls == ["first", "retry"]
