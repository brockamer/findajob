import json
import sqlite3
import pytest
from findajob import config_loader
from findajob.db.migrate import apply_pending
from findajob import filter_proposals


@pytest.fixture
def conn(tmp_path, monkeypatch):
    c = sqlite3.connect(tmp_path / "p.db")
    c.row_factory = sqlite3.Row
    apply_pending(c)
    c.commit()
    monkeypatch.setattr(config_loader, "_RULES_PATH", tmp_path / "prefilter_rules.yaml")
    config_loader._reset_cache()
    yield c
    config_loader._reset_cache()
    c.close()


def _seed_proposal(c, pattern=r"\bfleet\s+readiness\b"):
    cur = c.execute(
        "INSERT INTO filter_proposals (pattern, pattern_norm, category, status) "
        "VALUES (?, ?, 'auto_added', 'pending')",
        (pattern, pattern),
    )
    c.commit()
    return cur.lastrowid


def _seed_job(c, jid, title, score, scored_by="llm", stage="scored"):
    c.execute(
        "INSERT INTO jobs (id, fingerprint, url, source, title, company, relevance_score, scored_by, stage) "
        "VALUES (?, ?, ?, 'test', ?, 'Acme', ?, ?, ?)",
        (jid, f"fp-{jid}", f"http://x/{jid}", title, score, scored_by, stage),
    )
    c.commit()


def test_apply_low_score_match_applies_without_confirm(conn):
    pid = _seed_proposal(conn)
    _seed_job(conn, "j1", "Fleet Readiness Manager", 5)

    result = filter_proposals.apply_proposal(conn, pid, r"\bfleet\s+readiness\b")
    assert result["result"] == "applied"

    job = conn.execute("SELECT relevance_score, scored_by, stage FROM jobs WHERE id='j1'").fetchone()
    assert job["relevance_score"] == 1
    assert job["scored_by"] == "prefilter_stage1"
    assert job["stage"] == "scored"

    prop = conn.execute("SELECT status, affected_jobs, config_change_id FROM filter_proposals WHERE id=?", (pid,)).fetchone()
    assert prop["status"] == "applied"
    affected = json.loads(prop["affected_jobs"])
    assert affected[0]["job_id"] == "j1" and affected[0]["old_score"] == 5
    assert prop["config_change_id"] is not None
    cc = conn.execute("SELECT changed_by, lever FROM config_changes WHERE id=?", (prop["config_change_id"],)).fetchone()
    assert cc["changed_by"] == "auto_tuner" and cc["lever"] == "prefilter_rules"
    assert conn.execute("SELECT COUNT(*) FROM feedback_log").fetchone()[0] == 0


def test_apply_needs_confirm_on_danger_then_force(conn):
    pid = _seed_proposal(conn)
    _seed_job(conn, "j1", "Fleet Readiness Director", 8)

    res = filter_proposals.apply_proposal(conn, pid, r"\bfleet\s+readiness\b")
    assert res["result"] == "needs_confirm" and res["danger_count"] == 1
    assert conn.execute("SELECT status FROM filter_proposals WHERE id=?", (pid,)).fetchone()["status"] == "pending"
    assert conn.execute("SELECT relevance_score FROM jobs WHERE id='j1'").fetchone()["relevance_score"] == 8

    res2 = filter_proposals.apply_proposal(conn, pid, r"\bfleet\s+readiness\b", force=True)
    assert res2["result"] == "applied"
    assert conn.execute("SELECT relevance_score FROM jobs WHERE id='j1'").fetchone()["relevance_score"] == 1


def test_apply_is_idempotent_on_non_pending(conn):
    pid = _seed_proposal(conn)
    filter_proposals.apply_proposal(conn, pid, r"\bfleet\s+readiness\b", force=True)
    assert filter_proposals.apply_proposal(conn, pid, r"\bfleet\s+readiness\b", force=True)["result"] == "noop"


def test_skip_marks_skipped(conn):
    pid = _seed_proposal(conn)
    assert filter_proposals.skip_proposal(conn, pid) is True
    assert conn.execute("SELECT status FROM filter_proposals WHERE id=?", (pid,)).fetchone()["status"] == "skipped"


def test_revert_restores_scores_and_removes_rule(conn):
    pid = _seed_proposal(conn)
    _seed_job(conn, "j1", "Fleet Readiness Manager", 8)
    filter_proposals.apply_proposal(conn, pid, r"\bfleet\s+readiness\b", force=True)

    assert filter_proposals.revert_proposal(conn, pid) is True
    job = conn.execute("SELECT relevance_score, scored_by FROM jobs WHERE id='j1'").fetchone()
    assert job["relevance_score"] == 8 and job["scored_by"] == "llm"
    assert conn.execute("SELECT status FROM filter_proposals WHERE id=?", (pid,)).fetchone()["status"] == "reverted"
    config_loader._reset_cache()
    reject_re, _ = config_loader.load_hard_reject_rules()
    assert not reject_re.search("Fleet Readiness Manager")
