"""Tests for findajob.discoverer.runner.

runner.run() calls openrouter.complete() directly. All mocks target the wrapper
or the HTTP boundary.
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

from findajob.discoverer.runner import run
from findajob.llm.openrouter import CompletionResult, OpenRouterError

# ---------------------------------------------------------------------------
# Shared schema / constants
# ---------------------------------------------------------------------------

COST_LOG_SCHEMA = """
CREATE TABLE cost_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT,
    operation TEXT NOT NULL,
    model TEXT NOT NULL,
    latency_ms INTEGER,
    success INTEGER DEFAULT 1,
    error_message TEXT,
    logged_at TEXT DEFAULT (datetime('now')),
    input_tokens INTEGER,
    output_tokens INTEGER,
    cost_usd REAL
);
"""

# Fake key satisfies OPENROUTER_API_KEY guard without a real network call.
_FAKE_API_KEY = {"OPENROUTER_API_KEY": "sk-or-v1-test"}

VALID_LLM_OUTPUT = """\
# Discovered Companies — generated 2026-04-26

## Cluster: Direct domain match

- **Alpha Co** — channel=greenhouse. Reasoning: Direct match. Citations: [1].
- **Beta Inc** — channel=ashby. Reasoning: Hiring shape aligns. Citations: [2].

## Cluster: Transferable-competency adjacency

- **Gamma LLC** — channel=lever. Reasoning: Adjacent industry. Citations: [3].

## References

[1] https://alpha.example.com
[2] https://beta.example.com
[3] https://gamma.example.com
"""

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _setup_cost_log_db(db_path: Path) -> None:
    """Initialize a minimal cost_log schema mirroring scripts/init_db.py."""
    conn = sqlite3.connect(db_path)
    conn.executescript(COST_LOG_SCHEMA)
    conn.close()


def _setup_profile(base_root: Path) -> Path:
    cc = base_root / "candidate_context"
    cc.mkdir(parents=True, exist_ok=True)
    p = cc / "profile.md"
    p.write_text(
        "## Identity\nName: T\n\n## Core Competencies\n- A\n\n"
        "## Career Summary\nx\n\n## Target Roles\nr\n\n"
        "## Target Companies / Organizations\nAcme.\n",
        encoding="utf-8",
    )
    return p


def _stub_complete(text: str = VALID_LLM_OUTPUT, *, cost: float = 0.02, finish_reason: str | None = None):
    """Return a callable that always returns the given CompletionResult."""
    result = CompletionResult(
        text=text,
        prompt_tokens=500,
        completion_tokens=200,
        cached_tokens=0,
        cost_usd=cost,
        generation_id="gen-test-1",
        finish_reason=finish_reason,
    )
    return MagicMock(return_value=result)


def _stub_openrouter_response(
    *,
    content: str = VALID_LLM_OUTPUT,
    cost: float = 0.02,
    prompt_tokens: int = 500,
    completion_tokens: int = 200,
    cached_tokens: int = 0,
):
    """HTTP-level stub: returns an object that mimics an open urllib response."""
    body = json.dumps(
        {
            "id": "gen-test-1",
            "choices": [{"message": {"content": content}}],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "cost": cost,
                "prompt_tokens_details": {"cached_tokens": cached_tokens},
            },
        }
    ).encode("utf-8")

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def read(self):
            return body

    return _Resp()


# ---------------------------------------------------------------------------
# Core happy-path tests
# ---------------------------------------------------------------------------


def test_run_happy_path_writes_both_files_and_returns_success(tmp_path: Path) -> None:
    _setup_profile(tmp_path)
    with patch("findajob.discoverer.runner.complete", _stub_complete()):
        result = run(tmp_path, ntfy_enabled=False)
    assert result.success is True
    assert result.count == 3
    assert result.error is None
    md = (tmp_path / "candidate_context" / "discovered_companies.md").read_text()
    assert "Alpha Co" in md
    payload = json.loads((tmp_path / "candidate_context" / "discovered_companies.json").read_text())
    assert len(payload["companies"]) == 3


def test_run_strips_think_blocks_before_parser(tmp_path: Path) -> None:
    _setup_profile(tmp_path)
    output = "<think>I'm reasoning.</think>\n" + VALID_LLM_OUTPUT
    with patch("findajob.discoverer.runner.complete", _stub_complete(output)):
        result = run(tmp_path, ntfy_enabled=False)
    assert result.success is True
    md = (tmp_path / "candidate_context" / "discovered_companies.md").read_text()
    assert "<think>" not in md


def test_run_parse_failure_returns_failure_and_leaves_disk_untouched(tmp_path: Path) -> None:
    _setup_profile(tmp_path)
    bad_output = (
        "## Cluster: Direct domain match\n"
        "- **A** — channel=greenhouse. Reasoning: x. Citations: [1].\n"
        "## References\n[1] https://example.com"
    )
    with patch("findajob.discoverer.runner.complete", _stub_complete(bad_output)):
        result = run(tmp_path, ntfy_enabled=False)
    assert result.success is False
    assert result.error and "at least 3 companies" in result.error.lower()
    assert not (tmp_path / "candidate_context" / "discovered_companies.md").exists()
    assert not (tmp_path / "candidate_context" / "discovered_companies.json").exists()


def test_run_openrouter_error_returns_failure(tmp_path: Path) -> None:
    """OpenRouterError (replaces old subprocess returncode!=0) returns failure."""
    _setup_profile(tmp_path)
    with patch(
        "findajob.discoverer.runner.complete",
        side_effect=OpenRouterError("upstream error", kind="upstream"),
    ):
        result = run(tmp_path, ntfy_enabled=False)
    assert result.success is False
    assert result.error is not None


# ---------------------------------------------------------------------------
# #737: discoverer.runner no longer emits openrouter_truncated directly.
# The wrapper does (tests/test_openrouter_truncation_log.py covers the
# wrapper-level emission). The tests below guard the runner-side contract:
# (1) the runner module no longer emits openrouter_truncated under any
# finish_reason, and (2) the wrapper's emission survives the runner's
# OpenRouterError catch — i.e. discovery_failed still fires alongside.
# ---------------------------------------------------------------------------


def test_runner_does_not_emit_truncated_on_finish_reason_length(tmp_path: Path) -> None:
    """Regression guard against #678-shape duplicate-emission resurfacing.

    Pre-#737 the runner emitted openrouter_truncated locally on
    finish_reason='length'. After centralization the wrapper owns the
    emission and the runner must not duplicate it — mocking complete()
    bypasses the wrapper, so any openrouter_truncated event captured here
    would mean the local emission has been re-added by mistake.
    """
    _setup_profile(tmp_path)
    events: list[tuple[str, dict]] = []

    def _capture(event: str, **kw):
        events.append((event, kw))

    with (
        patch(
            "findajob.discoverer.runner.complete",
            _stub_complete(finish_reason="length"),
        ),
        patch("findajob.discoverer.runner.log_event", side_effect=_capture),
    ):
        run(tmp_path, ntfy_enabled=False)

    # The runner module must not produce this event itself anymore.
    truncated = [e for e, _ in events if e == "openrouter_truncated"]
    assert truncated == [], (
        "discoverer.runner must not emit openrouter_truncated directly after #737 — "
        "the wrapper owns the emission. Local emission resurfaced?"
    )


def test_runner_emits_via_wrapper_on_http_boundary_null_content(tmp_path: Path) -> None:
    """End-to-end shape regression for the centralized emission (#737).

    Drives the REAL wrapper boundary (urlopen) — no complete() mock — so the
    full chain runs: HTTP body → _parse_response → OpenRouterError raise →
    wrapper-side openrouter_truncated emit → runner-side catch →
    discovery_failed.

    Two patch sites: ``findajob.discoverer.runner.log_event`` to capture
    discovery_failed, and ``findajob.llm.openrouter.log_event`` to capture
    the centralized openrouter_truncated. Both feed the same list so order
    is preserved. Without the openrouter-side patch the wrapper's emission
    would silently fall through to the real log_event (and pollute
    ``logs/pipeline.jsonl`` during the test run).

    This is the same production-shape fixture captured in the pipeline.jsonl
    event in #678:
        {"event": "discovery_failed", "message": "Content not a string: NoneType; finish_reason=length"}
    """
    _setup_profile(tmp_path)
    body = json.dumps(
        {
            "id": "gen-test-trunc",
            "choices": [
                {
                    "message": {"role": "assistant", "content": None},
                    "finish_reason": "length",
                }
            ],
            "usage": {
                "prompt_tokens": 500,
                "completion_tokens": 4096,
                "cost": 0.04,
                "prompt_tokens_details": {"cached_tokens": 0},
            },
        }
    ).encode("utf-8")

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def read(self):
            return body

    events: list[tuple[str, dict]] = []

    def _capture(event: str, **kw):
        events.append((event, kw))

    with (
        patch.dict(os.environ, _FAKE_API_KEY),
        patch("findajob.llm.openrouter.urllib.request.urlopen", return_value=_Resp()),
        # #737: patch BOTH module bindings of log_event. The wrapper imports
        # log_event into findajob.llm.openrouter; the runner imports it into
        # findajob.discoverer.runner. After the move, openrouter_truncated
        # fires from the openrouter module, discovery_failed from the runner.
        patch("findajob.llm.openrouter.log_event", side_effect=_capture),
        patch("findajob.discoverer.runner.log_event", side_effect=_capture),
        patch("findajob.llm.openrouter._check_call_gate", return_value=None),
    ):
        result = run(tmp_path, ntfy_enabled=False)

    # Both events must fire, in order: truncated first (from the wrapper,
    # before the OpenRouterError is re-raised), then discovery_failed
    # (from the runner's catch).
    event_names = [e for e, _ in events]
    assert "openrouter_truncated" in event_names, (
        "wrapper-side openrouter_truncated did not fire on null-content + length response; "
        "centralized emission may be broken"
    )
    assert "discovery_failed" in event_names
    assert event_names.index("openrouter_truncated") < event_names.index("discovery_failed")

    truncated = next((kw for e, kw in events if e == "openrouter_truncated"), None)
    assert truncated is not None
    assert truncated["role"] == "company_discoverer"
    # #737 schema fix: completion_tokens is now int (was null pre-#737 on the
    # discoverer's null-content branch). Reads from response.usage.
    assert truncated["completion_tokens"] == 4096
    assert truncated["content_chars"] == 0  # null-content discriminator

    assert result.success is False


def test_run_missing_profile_returns_failure(tmp_path: Path) -> None:
    # No profile.md at all
    result = run(tmp_path, ntfy_enabled=False)
    assert result.success is False
    assert result.error is not None
    assert "profile" in result.error.lower()


def test_run_does_not_overwrite_last_good_on_failure(tmp_path: Path) -> None:
    _setup_profile(tmp_path)
    cc = tmp_path / "candidate_context"
    (cc / "discovered_companies.md").write_text("LAST GOOD\n")
    (cc / "discovered_companies.json").write_text('{"companies": []}\n')
    with patch(
        "findajob.discoverer.runner.complete",
        _stub_complete("INSUFFICIENT_PROFILE"),
    ):
        result = run(tmp_path, ntfy_enabled=False)
    assert result.success is False
    assert (cc / "discovered_companies.md").read_text() == "LAST GOOD\n"
    assert (cc / "discovered_companies.json").read_text() == '{"companies": []}\n'


# ---------------------------------------------------------------------------
# Wrapper kwarg-capture test (no cached_prefix, no pin_provider)
# ---------------------------------------------------------------------------


def test_company_discoverer_uses_wrapper_and_no_cache_args(tmp_path: Path) -> None:
    """company_discoverer routes through complete(); no cached_prefix/pin_provider
    (Perplexity ignores cache_control — passing them would be misleading)."""
    _setup_profile(tmp_path)
    captured: dict = {}

    def _fake_complete(role, prompt, **kwargs):
        captured.update(role=role, **kwargs)
        return CompletionResult(
            text=VALID_LLM_OUTPUT,
            prompt_tokens=500,
            completion_tokens=200,
            cached_tokens=0,
            cost_usd=0.01,
            generation_id="g",
        )

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(COST_LOG_SCHEMA)

    with patch("findajob.discoverer.runner.complete", _fake_complete):
        result = run(tmp_path, ntfy_enabled=False)

    assert result.success is True
    assert captured.get("role") == "company_discoverer"
    # Perplexity doesn't honor cache_control — these must NOT be passed.
    assert captured.get("cached_prefix") is None
    assert captured.get("pin_provider") is None


def test_company_discoverer_cost_log_from_api(tmp_path: Path) -> None:
    """Cost written to cost_log comes from result.cost_usd, not a heuristic."""
    _setup_profile(tmp_path)
    captured: dict = {}

    def _fake_complete(role, prompt, **kwargs):
        captured.update(role=role, **kwargs)
        return CompletionResult(
            text=VALID_LLM_OUTPUT,
            prompt_tokens=500,
            completion_tokens=200,
            cached_tokens=0,
            cost_usd=0.01,
            generation_id="g",
        )

    db_path = tmp_path / "pipeline.db"
    _setup_cost_log_db(db_path)

    with patch("findajob.discoverer.runner.complete", _fake_complete):
        result = run(tmp_path, ntfy_enabled=False, db_path=db_path)

    assert result.success is True
    assert result.cost_usd == 0.01
    rows = sqlite3.connect(db_path).execute("SELECT operation, cost_usd FROM cost_log").fetchall()
    assert any(r[0] == "company_discoverer" and r[1] == 0.01 for r in rows)


# ---------------------------------------------------------------------------
# HTTP-boundary test (wrapper integration)
# ---------------------------------------------------------------------------


def test_company_discoverer_http_boundary(tmp_path: Path) -> None:
    """End-to-end wrapper integration: mocked at urlopen, cost flows into cost_log."""
    _setup_profile(tmp_path)
    db_path = tmp_path / "pipeline.db"
    _setup_cost_log_db(db_path)

    with (
        patch.dict(os.environ, _FAKE_API_KEY),
        patch(
            "findajob.llm.openrouter.urllib.request.urlopen",
            return_value=_stub_openrouter_response(cost=0.05),
        ),
    ):
        result = run(tmp_path, ntfy_enabled=False, db_path=db_path)

    assert result.success is True
    assert result.cost_usd == 0.05
    conn = sqlite3.connect(db_path)
    rows = conn.execute("SELECT operation, cost_usd, input_tokens, output_tokens FROM cost_log").fetchall()
    conn.close()
    assert len(rows) == 1
    operation, cost_usd, input_tokens, output_tokens = rows[0]
    assert operation == "company_discoverer"
    assert cost_usd == 0.05
    assert input_tokens == 500
    assert output_tokens == 200


# ---------------------------------------------------------------------------
# Ntfy tests
# ---------------------------------------------------------------------------


def test_run_emits_ntfy_when_threshold_breached(tmp_path: Path, monkeypatch) -> None:
    _setup_profile(tmp_path)
    monkeypatch.setenv("DISCOVERY_COST_THRESHOLD_USD", "1.00")
    notify_mock = MagicMock()
    with (
        patch("findajob.discoverer.runner.complete", _stub_complete(cost=5.50)),
        patch("findajob.discoverer.runner._send_ntfy", notify_mock),
    ):
        result = run(tmp_path, ntfy_enabled=True)
    assert result.success is True
    breach_calls = [call for call in notify_mock.call_args_list if call.args[0] == "discovery: cost exceeded threshold"]
    assert len(breach_calls) == 1
    body = breach_calls[0].args[1]
    assert "$5.50" in body
    assert "$1.00" in body


def test_run_does_not_emit_breach_ntfy_when_below_threshold(tmp_path: Path, monkeypatch) -> None:
    """Cost ≤ threshold must NOT fire the breach ntfy (success summary still does)."""
    _setup_profile(tmp_path)
    monkeypatch.setenv("DISCOVERY_COST_THRESHOLD_USD", "1.00")
    notify_mock = MagicMock()
    with (
        patch("findajob.discoverer.runner.complete", _stub_complete(cost=0.50)),
        patch("findajob.discoverer.runner._send_ntfy", notify_mock),
    ):
        result = run(tmp_path, ntfy_enabled=True)
    assert result.success is True
    breach_calls = [call for call in notify_mock.call_args_list if call.args[0] == "discovery: cost exceeded threshold"]
    assert breach_calls == []


def test_run_does_not_emit_ntfy_when_disabled(tmp_path: Path) -> None:
    _setup_profile(tmp_path)
    notify_mock = MagicMock()
    with (
        patch("findajob.discoverer.runner.complete", _stub_complete("INSUFFICIENT_PROFILE")),
        patch("findajob.discoverer.runner._send_ntfy", notify_mock),
    ):
        run(tmp_path, ntfy_enabled=False)
    assert not notify_mock.called


def test_run_success_emits_summary_ntfy_with_count_and_top_names(tmp_path: Path) -> None:
    _setup_profile(tmp_path)
    notify_mock = MagicMock()
    with (
        patch("findajob.discoverer.runner.complete", _stub_complete()),
        patch("findajob.discoverer.runner._send_ntfy", notify_mock),
    ):
        result = run(tmp_path, ntfy_enabled=True)
    assert result.success is True
    titles_bodies = [call.args[:2] for call in notify_mock.call_args_list]
    success_calls = [(t, b) for t, b in titles_bodies if t.startswith("findajob: discovered")]
    assert len(success_calls) == 1
    title, body = success_calls[0]
    assert title == "findajob: discovered 3 companies"
    assert "Alpha Co" in body
    assert "Beta Inc" in body
    assert "Gamma LLC" in body


def test_run_success_ntfy_suppressed_when_disabled(tmp_path: Path) -> None:
    _setup_profile(tmp_path)
    notify_mock = MagicMock()
    with (
        patch("findajob.discoverer.runner.complete", _stub_complete()),
        patch("findajob.discoverer.runner._send_ntfy", notify_mock),
    ):
        result = run(tmp_path, ntfy_enabled=False)
    assert result.success is True
    assert not notify_mock.called


def test_run_failure_paths_do_not_emit_success_ntfy(tmp_path: Path) -> None:
    """On OpenRouterError the success ntfy must not fire; a failure ntfy fires instead."""
    _setup_profile(tmp_path)
    notify_mock = MagicMock()
    with (
        patch(
            "findajob.discoverer.runner.complete",
            side_effect=OpenRouterError("upstream error", kind="upstream"),
        ),
        patch("findajob.discoverer.runner._send_ntfy", notify_mock),
    ):
        run(tmp_path, ntfy_enabled=True)
    titles = [call.args[0] for call in notify_mock.call_args_list]
    assert not any(t.startswith("findajob: discovered") for t in titles)
    # The failure ntfy should still fire — check at least one call happened
    assert len(titles) >= 1


# ---------------------------------------------------------------------------
# Cost-log tests
# ---------------------------------------------------------------------------


def test_run_writes_cost_log_row_on_success(tmp_path: Path) -> None:
    """Successful run inserts one cost_log row with API-authoritative cost_usd."""
    _setup_profile(tmp_path)
    db_path = tmp_path / "pipeline.db"
    _setup_cost_log_db(db_path)
    with patch("findajob.discoverer.runner.complete", _stub_complete(cost=0.02)):
        result = run(tmp_path, ntfy_enabled=False, db_path=db_path)
    assert result.success is True
    conn = sqlite3.connect(db_path)
    rows = conn.execute("SELECT operation, model, cost_usd, latency_ms, success FROM cost_log").fetchall()
    conn.close()
    assert len(rows) == 1
    operation, model, cost_usd, latency_ms, success = rows[0]
    assert operation == "company_discoverer"
    assert model.startswith("openrouter:")
    assert cost_usd == 0.02  # exact API-authoritative value, not heuristic
    assert success == 1


def test_run_does_not_write_cost_log_on_openrouter_failure(tmp_path: Path) -> None:
    """OpenRouterError (replaces old subprocess failure) does NOT write a cost_log row."""
    _setup_profile(tmp_path)
    db_path = tmp_path / "pipeline.db"
    _setup_cost_log_db(db_path)
    with patch(
        "findajob.discoverer.runner.complete",
        side_effect=OpenRouterError("upstream error", kind="upstream"),
    ):
        run(tmp_path, ntfy_enabled=False, db_path=db_path)
    conn = sqlite3.connect(db_path)
    count = conn.execute("SELECT COUNT(*) FROM cost_log").fetchone()[0]
    conn.close()
    assert count == 0


def test_run_succeeds_when_db_path_does_not_exist(tmp_path: Path) -> None:
    """A missing or unwritable DB must NOT break the discovery run —
    cost-tracking is best-effort and never raises.
    """
    _setup_profile(tmp_path)
    bogus_db = tmp_path / "no" / "such" / "dir" / "pipeline.db"
    with patch("findajob.discoverer.runner.complete", _stub_complete()):
        result = run(tmp_path, ntfy_enabled=False, db_path=bogus_db)
    assert result.success is True


# ---------------------------------------------------------------------------
# _send_success_ntfy unit test
# ---------------------------------------------------------------------------


def test_send_success_ntfy_zero_count_uses_sentinel_body() -> None:
    from findajob.discoverer.runner import _send_success_ntfy

    notify_mock = MagicMock()
    with patch("findajob.discoverer.runner._send_ntfy", notify_mock):
        _send_success_ntfy([])
    assert notify_mock.call_count == 1
    title, body = notify_mock.call_args.args[:2]
    assert title == "findajob: discovered 0 companies"
    assert body == "(no novel companies surfaced this run)"
