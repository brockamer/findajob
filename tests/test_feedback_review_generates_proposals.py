import sqlite3

from findajob.db.migrate import apply_pending
from findajob.notifications import feedback_review


def test_generate_step_inserts_when_candidates_exist(tmp_path, monkeypatch):
    db = tmp_path / "pipeline.db"
    c = sqlite3.connect(db)
    c.row_factory = sqlite3.Row
    apply_pending(c)
    for t in ("Fleet Readiness Manager", "Fleet Readiness Lead", "SFBA Fleet Readiness"):
        c.execute(
            "INSERT INTO feedback_log (job_id, title, company, relevance_score, reject_reason) "
            "VALUES (?, ?, 'Acme', 9, 'Skills Mismatch')",
            (t, t),
        )
    c.commit()
    c.close()
    import findajob.analyze_feedback as af

    monkeypatch.setattr(af, "load_reject_reasons", lambda: (("Skills Mismatch",), frozenset({"Skills Mismatch"})))

    n = feedback_review.refresh_filter_proposals(str(db))
    assert n >= 1
