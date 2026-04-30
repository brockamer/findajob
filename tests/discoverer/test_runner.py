import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from findajob.discoverer.runner import run

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


def _stub_subprocess_run(stdout: str, returncode: int = 0):
    completed = MagicMock(spec=subprocess.CompletedProcess)
    completed.stdout = stdout
    completed.stderr = ""
    completed.returncode = returncode
    return MagicMock(return_value=completed)


def test_run_happy_path_writes_both_files_and_returns_success(tmp_path: Path) -> None:
    _setup_profile(tmp_path)
    with patch("findajob.discoverer.runner.subprocess.run", _stub_subprocess_run(VALID_LLM_OUTPUT)):
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
    with patch("findajob.discoverer.runner.subprocess.run", _stub_subprocess_run(output)):
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
    with patch("findajob.discoverer.runner.subprocess.run", _stub_subprocess_run(bad_output)):
        result = run(tmp_path, ntfy_enabled=False)
    assert result.success is False
    assert result.error and "at least 3 companies" in result.error.lower()
    assert not (tmp_path / "candidate_context" / "discovered_companies.md").exists()
    assert not (tmp_path / "candidate_context" / "discovered_companies.json").exists()


def test_run_subprocess_failure_returns_failure(tmp_path: Path) -> None:
    _setup_profile(tmp_path)
    with patch(
        "findajob.discoverer.runner.subprocess.run",
        _stub_subprocess_run("", returncode=1),
    ):
        result = run(tmp_path, ntfy_enabled=False)
    assert result.success is False
    assert result.error is not None


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
        "findajob.discoverer.runner.subprocess.run",
        _stub_subprocess_run("INSUFFICIENT_PROFILE"),
    ):
        result = run(tmp_path, ntfy_enabled=False)
    assert result.success is False
    assert (cc / "discovered_companies.md").read_text() == "LAST GOOD\n"
    assert (cc / "discovered_companies.json").read_text() == '{"companies": []}\n'


def test_run_emits_ntfy_when_threshold_breached(tmp_path: Path, monkeypatch) -> None:
    _setup_profile(tmp_path)
    monkeypatch.setenv("DISCOVERY_COST_THRESHOLD_USD", "1.00")
    notify_mock = MagicMock()
    with (
        patch("findajob.discoverer.runner.subprocess.run", _stub_subprocess_run(VALID_LLM_OUTPUT)),
        patch("findajob.discoverer.runner._extract_cost_usd", return_value=5.50),
        patch("findajob.discoverer.runner._send_ntfy", notify_mock),
    ):
        result = run(tmp_path, ntfy_enabled=True)
    assert result.success is True
    assert notify_mock.called
    title, body = notify_mock.call_args.args[:2]
    assert "cost" in body.lower()


def test_run_does_not_emit_ntfy_when_disabled(tmp_path: Path) -> None:
    _setup_profile(tmp_path)
    notify_mock = MagicMock()
    with (
        patch("findajob.discoverer.runner.subprocess.run", _stub_subprocess_run("INSUFFICIENT_PROFILE")),
        patch("findajob.discoverer.runner._send_ntfy", notify_mock),
    ):
        run(tmp_path, ntfy_enabled=False)
    assert not notify_mock.called


def test_run_success_emits_summary_ntfy_with_count_and_top_names(tmp_path: Path) -> None:
    _setup_profile(tmp_path)
    notify_mock = MagicMock()
    with (
        patch("findajob.discoverer.runner.subprocess.run", _stub_subprocess_run(VALID_LLM_OUTPUT)),
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
        patch("findajob.discoverer.runner.subprocess.run", _stub_subprocess_run(VALID_LLM_OUTPUT)),
        patch("findajob.discoverer.runner._send_ntfy", notify_mock),
    ):
        result = run(tmp_path, ntfy_enabled=False)
    assert result.success is True
    assert not notify_mock.called


def test_run_failure_paths_do_not_emit_success_ntfy(tmp_path: Path) -> None:
    """Existing failure ntfys (timeout / aichat-failure / parse-error) must remain
    the only signal on the failure path; the new success ntfy must NOT fire there."""
    _setup_profile(tmp_path)
    notify_mock = MagicMock()
    with (
        patch("findajob.discoverer.runner.subprocess.run", _stub_subprocess_run("", returncode=1)),
        patch("findajob.discoverer.runner._send_ntfy", notify_mock),
    ):
        run(tmp_path, ntfy_enabled=True)
    titles = [call.args[0] for call in notify_mock.call_args_list]
    assert not any(t.startswith("findajob: discovered") for t in titles)
    assert any("aichat" in t for t in titles)


def test_send_success_ntfy_zero_count_uses_sentinel_body() -> None:
    from findajob.discoverer.runner import _send_success_ntfy

    notify_mock = MagicMock()
    with patch("findajob.discoverer.runner._send_ntfy", notify_mock):
        _send_success_ntfy([])
    assert notify_mock.call_count == 1
    title, body = notify_mock.call_args.args[:2]
    assert title == "findajob: discovered 0 companies"
    assert body == "(no novel companies surfaced this run)"
