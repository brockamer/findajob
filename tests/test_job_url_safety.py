"""Job-URL handling: scheme allowlist, host matching, no prep-time fetch.

Covers ``findajob.urlcheck``, the Gmail anchor-to-source match, the
server-side JD fetch, manual ingest, and the prep orchestrator's short-JD path.
"""

from __future__ import annotations

import sqlite3
import subprocess
from pathlib import Path

import pytest

from findajob import urlcheck

# ── urlcheck ──


@pytest.mark.parametrize(
    "url",
    ["https://example.com/job/1", "http://example.com/job/1", "HTTPS://Example.com/x"],
)
def test_is_http_url_accepts_web_links(url: str) -> None:
    assert urlcheck.is_http_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "file:///app/data/.env",
        "ftp://example.com/x",
        "javascript:alert(1)",
        "data:text/html,hi",
        "speculative://acme/1/req",
        "https://",
        "//example.com/x",
        "example.com/x",
        "",
        None,
    ],
)
def test_is_http_url_rejects_other_schemes_and_hostless(url: object) -> None:
    assert not urlcheck.is_http_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/x",
        "https://localhost/x",
        "https://127.0.0.1/x",
        "https://10.0.0.5/x",
        "https://169.254.169.254/latest/meta-data",
        "https://[::1]/x",
        "https://printer.local/x",
        "file:///etc/passwd",
    ],
)
def test_is_fetchable_url_rejects_non_https_and_internal_hosts(url: str) -> None:
    assert not urlcheck.is_fetchable_url(url)


def test_is_fetchable_url_accepts_public_https() -> None:
    assert urlcheck.is_fetchable_url("https://boards.example.com/jobs/1")
    assert urlcheck.is_fetchable_url("https://93.184.216.34/x")


def test_url_matches_compares_host_not_substring() -> None:
    assert urlcheck.url_matches("https://www.indeed.com/viewjob?jk=1", "indeed.com", "/viewjob")
    assert urlcheck.url_matches("https://indeed.com/viewjob", "indeed.com", "/viewjob")
    assert not urlcheck.url_matches("https://evil.example/?u=indeed.com/viewjob", "indeed.com", "/viewjob")
    assert not urlcheck.url_matches("https://indeed.com.evil.example/viewjob", "indeed.com", "/viewjob")
    assert not urlcheck.url_matches("https://notindeed.com/viewjob", "indeed.com", "/viewjob")
    assert not urlcheck.url_matches("https://www.indeed.com/other", "indeed.com", "/viewjob")
    assert not urlcheck.url_matches("file://indeed.com/viewjob", "indeed.com", "/viewjob")


def _fake_dns(monkeypatch: pytest.MonkeyPatch, mapping: dict[str, str]) -> None:
    def fake_getaddrinfo(host, port, *a, **k):
        if host not in mapping:
            raise OSError("no such host")
        return [(2, 1, 6, "", (mapping[host], 0))]

    monkeypatch.setattr(urlcheck.socket, "getaddrinfo", fake_getaddrinfo)


def test_resolves_to_public_rejects_dns_name_pointing_inside(monkeypatch) -> None:
    _fake_dns(
        monkeypatch, {"good.example": "93.184.216.34", "sneaky.example": "10.0.0.5", "meta.example": "169.254.169.254"}
    )
    assert urlcheck.resolves_to_public("https://good.example/x")
    assert not urlcheck.resolves_to_public("https://sneaky.example/x")
    assert not urlcheck.resolves_to_public("https://meta.example/x")
    assert not urlcheck.resolves_to_public("https://unresolvable.example/x")  # fail closed


# ── Gmail anchors ──


def _anchor_html(href: str) -> str:
    return f'<html><body><a href="{href}">Senior Widget Engineer</a><p>Acme Corp</p></body></html>'


def test_email_anchor_on_source_host_is_ingested() -> None:
    from findajob.fetchers import _extract_jobs_from_html

    jobs = _extract_jobs_from_html(_anchor_html("https://www.linkedin.com/comm/jobs/view/123456/"))
    assert [j["source"] for j in jobs] == ["gmail_linkedin"]


@pytest.mark.parametrize(
    "href",
    [
        "https://internal.example/fetch?u=linkedin.com/jobs/view/1",
        "http://10.0.0.5:8080/admin#indeed.com/viewjob",
        "https://linkedin.com.evil.example/jobs/view/1",
        "file:///app/data/.env?x=indeed.com/viewjob",
    ],
)
def test_email_anchor_whose_host_is_not_the_source_host_is_not_ingested(href: str) -> None:
    from findajob.fetchers import _extract_jobs_from_html

    assert _extract_jobs_from_html(_anchor_html(href)) == []


# ── Server-side JD fetch ──


class _FakeResponse:
    def __init__(self, status: int = 200, body: str = "", location: str | None = None) -> None:
        self.status_code = status
        self.headers = {"Location": location} if location else {}
        self._body = body.encode()
        self.encoding = "utf-8"

    @property
    def is_redirect(self) -> bool:
        return "Location" in self.headers and self.status_code in (301, 302, 303, 307, 308)

    def iter_content(self, chunk_size: int = 65536):
        yield self._body

    def close(self) -> None:
        pass


@pytest.fixture(autouse=True)
def _public_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every test host resolves to a public address unless a test says otherwise."""

    def fake_getaddrinfo(host, port, *a, **k):
        return [(2, 1, 6, "", ("93.184.216.34", 0))]

    monkeypatch.setattr(urlcheck.socket, "getaddrinfo", fake_getaddrinfo)


@pytest.fixture()
def no_subprocess(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Record subprocess.run calls; pandoc is replaced by an identity stub."""
    calls: list[list[str]] = []

    def fake_run(cmd, *args, **kwargs):
        calls.append(list(cmd))
        if "pandoc" in str(cmd[0]).lower() or "-t" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout=kwargs.get("input", ""), stderr="")
        raise AssertionError(f"unexpected subprocess: {cmd}")

    monkeypatch.setattr("findajob.fetchers.subprocess.run", fake_run)
    return calls


def test_fetch_jd_refuses_file_url_without_any_fetch(monkeypatch, no_subprocess) -> None:
    import requests

    from findajob.fetchers import fetch_jd

    def boom(*a, **k):
        raise AssertionError("network fetch attempted")

    monkeypatch.setattr(requests.Session, "get", boom)
    monkeypatch.setattr(requests, "get", boom)

    out = fetch_jd({"source": "gmail_ziprecruiter", "url": "file:///app/data/.env"})
    assert out.startswith("[ERROR fetching JD")
    assert not any(c and c[0] == "curl" for c in no_subprocess)


def test_fetch_jd_never_shells_out_to_curl(monkeypatch, no_subprocess) -> None:
    import requests

    from findajob.fetchers import fetch_jd

    monkeypatch.setattr(requests, "get", lambda url, **k: _FakeResponse(body="<p>Real job text</p>"))
    out = fetch_jd({"source": "gmail_ziprecruiter", "url": "https://www.ziprecruiter.com/jobs/1"})
    assert "Real job text" in out
    assert not any(c and c[0] == "curl" for c in no_subprocess)


def test_fetch_jd_does_not_follow_redirect_to_internal_host(monkeypatch, no_subprocess) -> None:
    import requests

    from findajob.fetchers import fetch_jd

    seen: list[str] = []

    def fake_get(url, **kwargs):
        seen.append(url)
        assert kwargs.get("allow_redirects") is False
        return _FakeResponse(302, location="https://169.254.169.254/latest/meta-data")

    monkeypatch.setattr(requests, "get", fake_get)
    out = fetch_jd({"source": "gmail_ziprecruiter", "url": "https://www.ziprecruiter.com/jobs/1"})
    assert out.startswith("[ERROR fetching JD")
    assert seen == ["https://www.ziprecruiter.com/jobs/1"]


def test_fetch_jd_does_not_follow_redirect_to_name_resolving_inside(monkeypatch, no_subprocess) -> None:
    import requests

    from findajob.fetchers import fetch_jd

    def fake_getaddrinfo(host, port, *a, **k):
        return [(2, 1, 6, "", ("10.0.0.5" if host == "sneaky.example" else "93.184.216.34", 0))]

    monkeypatch.setattr(urlcheck.socket, "getaddrinfo", fake_getaddrinfo)
    seen: list[str] = []

    def fake_get(url, **kwargs):
        seen.append(url)
        return _FakeResponse(302, location="https://sneaky.example/admin")

    monkeypatch.setattr(requests, "get", fake_get)
    out = fetch_jd({"source": "gmail_ziprecruiter", "url": "https://www.ziprecruiter.com/jobs/1"})
    assert out.startswith("[ERROR fetching JD")
    assert seen == ["https://www.ziprecruiter.com/jobs/1"]


def test_fetch_jd_does_not_follow_redirect_to_file_scheme(monkeypatch, no_subprocess) -> None:
    import requests

    from findajob.fetchers import fetch_jd

    seen: list[str] = []

    def fake_get(url, **kwargs):
        seen.append(url)
        return _FakeResponse(302, location="file:///app/data/.env")

    monkeypatch.setattr(requests, "get", fake_get)
    out = fetch_jd({"source": "gmail_ziprecruiter", "url": "https://www.ziprecruiter.com/jobs/1"})
    assert out.startswith("[ERROR fetching JD")
    assert len(seen) == 1


# ── Manual ingest ──


@pytest.mark.parametrize(
    "url", ["file:///app/data/.env", "javascript:alert(1)", "ftp://example.com/x", "example.com/x"]
)
def test_ingest_manual_job_rejects_non_http_url(url: str) -> None:
    from findajob.ingest import InvalidJobUrl, ingest_manual_job

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    with pytest.raises(InvalidJobUrl):
        ingest_manual_job(
            conn,
            company="Acme",
            title="Widget Engineer",
            url=url,
            raw_jd_text="x" * 80,
            source="web_manual",
        )


# ── Prep: a short stored JD is a failure, never a fetch ──

SCHEMA = """
CREATE TABLE jobs (
    id TEXT PRIMARY KEY, fingerprint TEXT UNIQUE NOT NULL, url TEXT NOT NULL,
    title TEXT NOT NULL, company TEXT NOT NULL, location TEXT DEFAULT '',
    source TEXT NOT NULL DEFAULT 'test', raw_jd_text TEXT, relevance_score INTEGER,
    stage TEXT DEFAULT 'discovered', stage_updated TEXT, apply_flag INTEGER DEFAULT 0,
    reject_reason TEXT DEFAULT '', prep_folder_path TEXT, fit_score REAL,
    probability_score REAL, updated_at TEXT DEFAULT (datetime('now')),
    synthetic INTEGER NOT NULL DEFAULT 0, speculative_briefing_folder TEXT
);
CREATE TABLE audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL, field_changed TEXT NOT NULL,
    old_value TEXT, new_value TEXT, changed_at TEXT DEFAULT (datetime('now')),
    changed_by TEXT DEFAULT 'system'
);
"""
JOB_ID = "url-safety-1"


@pytest.fixture()
def prep_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import findajob.prep.orchestrator as orch

    (tmp_path / "candidate_context").mkdir()
    (tmp_path / "candidate_context" / "profile.md").write_text("# Profile\n")
    (tmp_path / "candidate_context" / "master_resume.md").write_text("# Resume\n")
    (tmp_path / "companies").mkdir()
    (tmp_path / "data").mkdir()
    db_path = tmp_path / "data" / "pipeline.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(SCHEMA)
    conn.close()

    events: list[tuple[str, dict]] = []
    ntfy: list[tuple] = []
    subprocess_calls: list = []
    monkeypatch.setattr(orch, "BASE", str(tmp_path))
    monkeypatch.setattr(orch, "DB_PATH", str(db_path))
    monkeypatch.setattr(orch, "PROFILE_PATH", str(tmp_path / "candidate_context" / "profile.md"))
    monkeypatch.setattr(orch, "MASTER_RESUME_PATH", str(tmp_path / "candidate_context" / "master_resume.md"))
    monkeypatch.setattr(orch, "log_event", lambda name, **kw: events.append((name, kw)))
    monkeypatch.setattr("findajob.actions.log_event", lambda name, **kw: events.append((name, kw)))
    monkeypatch.setattr(orch, "ntfy_send", lambda *a, **k: ntfy.append((a, k)))
    monkeypatch.setattr(orch, "read_file_prefix", lambda: "TST")
    monkeypatch.setattr(orch, "quarantine_stale_prep_folders", lambda *a, **k: None)
    monkeypatch.setattr(
        orch, "run_role", lambda *a, **k: (_ for _ in ()).throw(AssertionError("LLM call after failed JD check"))
    )
    monkeypatch.setattr(orch.subprocess, "run", lambda cmd, *a, **k: subprocess_calls.append(cmd))
    return db_path, events, ntfy, subprocess_calls


def _seed(db_path: Path, *, url: str, jd: str, stage: str, synthetic: int = 0, prep_folder: str | None = None) -> None:
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO jobs (id, fingerprint, url, title, company, source, stage, raw_jd_text, synthetic, "
        "prep_folder_path) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (JOB_ID, "fp-" + JOB_ID, url, "Widget Engineer", "Acme", "test", stage, jd, synthetic, prep_folder),
    )
    conn.commit()
    conn.close()


def _stage(db_path: Path) -> str:
    conn = sqlite3.connect(str(db_path))
    try:
        return conn.execute("SELECT stage FROM jobs WHERE id=?", (JOB_ID,)).fetchone()[0]
    finally:
        conn.close()


def test_phase_a_short_jd_with_file_url_fails_with_event_and_no_fetch(prep_env) -> None:
    from findajob.prep.orchestrator import _run_prep_phase_a

    db_path, events, ntfy, subprocess_calls = prep_env
    _seed(db_path, url="file:///app/data/.env", jd="too short", stage="prep_in_progress")

    _run_prep_phase_a("Acme", "Widget Engineer", "file:///app/data/.env", JOB_ID)

    assert "prep_jd_unavailable" in [name for name, _ in events]
    assert not any(cmd and cmd[0] == "curl" for cmd in subprocess_calls)
    assert _stage(db_path) == "scored"
    assert ntfy, "operator must be told the prep failed"


def test_phase_b_short_jd_fails_with_event_and_no_fetch(prep_env, tmp_path) -> None:
    from findajob.prep.orchestrator import _run_prep_phase_b

    db_path, events, ntfy, subprocess_calls = prep_env
    folder = tmp_path / "companies" / "acme"
    folder.mkdir()
    _seed(
        db_path,
        url="file:///app/data/.env",
        jd="too short",
        stage="prep_in_progress",
        prep_folder=str(folder),
    )

    with pytest.raises(SystemExit):
        _run_prep_phase_b("Acme", "Widget Engineer", "file:///app/data/.env", JOB_ID)

    assert "prep_jd_unavailable" in [name for name, _ in events]
    assert not any(cmd and cmd[0] == "curl" for cmd in subprocess_calls)
    assert _stage(db_path) == "briefing_ready"


# ── `--` before job fields in subprocess launches ──


def test_positional_argv_strips_the_terminator_and_keeps_legacy_layout() -> None:
    from findajob.cliargs import positional_argv

    assert positional_argv(["p.py", "--phase=b", "--", "-x", "--help", "u", "id"]) == ["-x", "--help", "u", "id"]
    assert positional_argv(["p.py", "co", "ti", "u", "id", "--phase=b"]) == ["co", "ti", "u", "id", "--phase=b"]


def test_prep_launcher_puts_options_before_the_terminator_and_fields_after(monkeypatch) -> None:
    from findajob.web.routes import board_actions as ba

    captured: dict = {}

    class FakeProc:
        pid = 1

    def fake_popen(argv, **kwargs):
        captured["argv"] = argv
        return FakeProc()

    class FakeDb:
        def execute(self, *a, **k):
            return self

        def commit(self):
            pass

    monkeypatch.setattr(ba.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(ba, "record_start", lambda *a, **k: 7)
    job = {"id": "j1", "company": "-Acme", "title": "--phase=b", "url": "https://example.com/j"}
    ba._launch_prep_subprocess(FakeDb(), job, kind="prep_phase_b", extra_args=("--phase=b",))

    argv = captured["argv"]
    marker = argv.index("--")
    assert argv[marker - 1] == "--phase=b"
    assert argv[marker + 1 :] == ["-Acme", "--phase=b", "https://example.com/j", "j1"]


def test_prep_main_reads_option_like_fields_as_data(monkeypatch) -> None:
    import findajob.prep.orchestrator as orch

    seen: list[tuple] = []
    monkeypatch.setattr(orch, "load_env", lambda: None)
    monkeypatch.setattr(orch, "writeback_subprocess", lambda *_: __import__("contextlib").nullcontext())
    monkeypatch.setattr(orch, "_run_prep_phase_a", lambda *a: seen.append(("a", a)))
    monkeypatch.setattr(orch, "_run_prep_phase_b", lambda *a: seen.append(("b", a)))
    monkeypatch.setattr(
        "sys.argv", ["prep_application.py", "--phase=a", "--", "-Acme", "--help", "https://example.com/j", "j1"]
    )

    orch.main()

    assert seen == [("a", ("-Acme", "--help", "https://example.com/j", "j1"))]
