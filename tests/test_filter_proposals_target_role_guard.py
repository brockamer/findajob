import sqlite3

import pytest

from findajob import analyze_feedback, filter_proposals
from findajob.db.migrate import apply_pending
from findajob.discoverer.prompt import build_prompt

# Synthetic, field-neutral term — never a real job title — so the guard's
# behavior is exercised without depending on any real candidate profile.
_RECURRING_TITLE_BASE = "Widget Operations"

_PROFILE_NAMES_TARGET_ROLE = """
## Target Role
Widget Operations Lead for industrial manufacturing teams.
Open to: hybrid, remote
Not open to: unrelated field roles
"""

_PROFILE_NOT_OPEN_TO_ONLY = """
## Target Role
Gadget Assembly Lead for consumer electronics teams.
Open to: hybrid, remote
Not open to: widget operations roles
"""


@pytest.fixture
def conn(tmp_path, monkeypatch):
    c = sqlite3.connect(tmp_path / "p.db")
    c.row_factory = sqlite3.Row
    apply_pending(c)
    c.commit()
    # Title-signal reasons that drive the miner.
    monkeypatch.setattr(
        analyze_feedback,
        "load_reject_reasons",
        lambda: (("Skills Mismatch",), frozenset({"Skills Mismatch"})),
    )
    yield c
    c.close()


def _seed_rejection(c, title, reason="Skills Mismatch", score=9):
    c.execute(
        "INSERT INTO feedback_log (job_id, title, company, relevance_score, reject_reason) VALUES (?, ?, ?, ?, ?)",
        (f"id-{title}", title, "Acme", score, reason),
    )


def _seed_recurring_rejections(c):
    for t in (
        f"{_RECURRING_TITLE_BASE} Manager",
        f"{_RECURRING_TITLE_BASE} Lead",
        f"SFBA {_RECURRING_TITLE_BASE}",
    ):
        _seed_rejection(c, t)
    c.commit()


def test_target_role_guard_skips_candidate_matching_target_role(conn, monkeypatch):
    events = []
    monkeypatch.setattr(filter_proposals, "log_event", lambda name, **kw: events.append((name, kw)))
    _seed_recurring_rejections(conn)

    n = filter_proposals.generate_proposals(conn, profile_text=_PROFILE_NAMES_TARGET_ROLE)

    assert n == 0
    rows = conn.execute("SELECT * FROM filter_proposals").fetchall()
    assert len(rows) == 0
    assert any(name == "filter_proposal_skipped_target_role" for name, _ in events)
    skipped = [kw for name, kw in events if name == "filter_proposal_skipped_target_role"]
    assert any("widget" in kw["pattern"].lower() for kw in skipped)


def test_target_role_guard_keeps_candidate_matching_not_open_to(conn, monkeypatch):
    events = []
    monkeypatch.setattr(filter_proposals, "log_event", lambda name, **kw: events.append((name, kw)))
    _seed_recurring_rejections(conn)

    n = filter_proposals.generate_proposals(conn, profile_text=_PROFILE_NOT_OPEN_TO_ONLY)

    assert n >= 1
    rows = conn.execute("SELECT pattern, status FROM filter_proposals").fetchall()
    assert any("widget" in r["pattern"].lower() for r in rows)
    assert all(r["status"] == "pending" for r in rows)
    assert not any(name == "filter_proposal_skipped_target_role" for name, _ in events)


def test_target_role_guard_without_profile_queues_as_before(conn, monkeypatch, tmp_path):
    events = []
    monkeypatch.setattr(filter_proposals, "log_event", lambda name, **kw: events.append((name, kw)))
    # Point the default profile lookup at a path that does not exist.
    monkeypatch.setattr(filter_proposals, "BASE", str(tmp_path / "nonexistent-base"))
    _seed_recurring_rejections(conn)

    n = filter_proposals.generate_proposals(conn)

    assert n >= 1
    rows = conn.execute("SELECT pattern, status FROM filter_proposals").fetchall()
    assert any("widget" in r["pattern"].lower() for r in rows)
    assert all(r["status"] == "pending" for r in rows)
    assert not any(name == "filter_proposal_skipped_target_role" for name, _ in events)


_DISCOVERER_PROFILE = """
## Identity
Name: Test One

## Core Competencies
- Skill A
- Skill B

## Target Roles
- **Senior Person at Place** — making widgets and gizmos

## Target Companies / Organizations
Acme, Beta Co.
"""

# Golden output captured from build_prompt() against _DISCOVERER_PROFILE before
# the section-parsing regex moved into profile.py — pins that the move is a
# pure relocation, not a behavior change.
_DISCOVERER_PROMPT_GOLDEN = (
    "Identify 6-10 emerging or less-prominent organizations actively hiring Senior"
    " Person at Place for making widgets and gizmos. Include a verifiable URL when"
    " you can confirm one (job posting, careers page, or hiring announcement); when"
    " you cannot confirm a URL, OMIT the citation entirely and rely on the company"
    " name + reasoning — the operator will hand-verify. Do NOT refuse to recommend"
    " a company just because no URL is in your search results.\n"
    "\n"
    "DO NOT recommend any company in the EXCLUSION LIST below. The candidate"
    " already knows these companies; recommending them adds no value. Your job is"
    " to widen the funnel beyond this list.\n"
    "\n"
    "EXCLUSION LIST (already on the candidate's target list — DO NOT recommend):\n"
    "Acme, Beta Co.\n"
    "\n"
    "The candidate's full target-role and competency context follows. After\n"
    "listing 6-10 NEW companies (none on the exclusion list), group them\n"
    "per your role's three-cluster taxonomy.\n"
    "\n"
    "Target roles the candidate is pursuing:\n"
    "- **Senior Person at Place** — making widgets and gizmos\n"
    "\n"
    "Core competencies to anchor on:\n"
    "- Skill A\n"
    "- Skill B\n"
    "\n"
    "=== BEGIN CANDIDATE PROFILE ===\n"
    "## Identity\n"
    "Name: Test One\n"
    "\n"
    "## Core Competencies\n"
    "- Skill A\n"
    "- Skill B\n"
    "\n"
    "## Target Roles\n"
    "- **Senior Person at Place** — making widgets and gizmos\n"
    "\n"
    "## Target Companies / Organizations\n"
    "Acme, Beta Co.\n"
    "=== END CANDIDATE PROFILE ===\n"
    "\n"
    "Produce the markdown per your role's output format. If the candidate\n"
    "profile is missing both target roles and core competencies sections,\n"
    "respond with the literal text INSUFFICIENT_PROFILE and nothing else.\n"
)


def test_target_role_guard_discoverer_prompt_unchanged():
    assert build_prompt(_DISCOVERER_PROFILE) == _DISCOVERER_PROMPT_GOLDEN
