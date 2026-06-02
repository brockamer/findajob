"""Regression tests for findajob.triage.contacts (#963).

The ingest-time contact matcher must route through the canonical
word-boundary ``company_match()`` in ``findajob.find_contacts`` rather than
the old substring ``in`` matcher (the #497 bug class). These tests drive the
real ingest entry point, ``triage.contacts.find_contacts``, against a seeded
connections.csv and assert it behaves identically to the prep path — same
word-boundary matching (AC1/AC4) and the same loud-but-non-crashing failure
on a renamed header (AC3).
"""

from __future__ import annotations

import pytest

_HEADER = "First Name,Last Name,Company,Position,Connected On,URL\n"


@pytest.fixture
def seeded(monkeypatch, tmp_path):
    """Redirect both the ingest and canonical CONNECTIONS to one tmp CSV.

    The ingest wrapper delegates to ``findajob.find_contacts``, so post-#963
    the CSV is read (and any error event logged) there. Pre-#963 the substring
    matcher read its own ``triage.contacts.CONNECTIONS``. Patching both —
    guarded by ``hasattr`` so it survives removal of the now-dead ingest
    constant — keeps the test meaningful across the red→green transition.

    Returns ``(ingest_module, csv_path, events)`` where ``events`` captures
    every ``log_event`` fired by the canonical reader.
    """
    from findajob import find_contacts as canonical
    from findajob.triage import contacts as ingest

    csv_path = tmp_path / "connections.csv"
    monkeypatch.setattr(canonical, "CONNECTIONS", str(csv_path))
    if hasattr(ingest, "CONNECTIONS"):
        monkeypatch.setattr(ingest, "CONNECTIONS", str(csv_path))

    events: list[tuple] = []
    monkeypatch.setattr(canonical, "log_event", lambda name, **kw: events.append((name, kw)))

    return ingest, csv_path, events


def test_ingest_rejects_prefix_collision(seeded):
    """#497 headline case on the INGEST path: 'Apple' must not match 'GreenApple'."""
    ingest, csv_path, _ = seeded
    csv_path.write_text(_HEADER + "Ada,Lovelace,GreenApple Inc.,Director,01 Jan 2020,https://e.com/a\n")

    assert ingest.find_contacts("Apple") == []


def test_ingest_rejects_short_string_substring(seeded):
    """'AI' must not match 'AIRBUS' at ingest — substring `in` matched every 'ai'."""
    ingest, csv_path, _ = seeded
    csv_path.write_text(_HEADER + "Grace,Hopper,AIRBUS Inc.,Engineer,01 Jan 2020,https://e.com/g\n")

    assert ingest.find_contacts("AI") == []


def test_ingest_legitimate_match_still_found(seeded):
    """A real same-company connection is still matched and formatted '<name> (<title>)'."""
    ingest, csv_path, _ = seeded
    csv_path.write_text(_HEADER + "Ada,Lovelace,Meta Platforms,Engineering Director,01 Jan 2020,https://e.com/a\n")

    assert ingest.find_contacts("Meta") == ["Ada Lovelace (Engineering Director)"]


def test_ingest_blank_or_none_company_no_error_event(seeded):
    """None/blank company returns [] WITHOUT firing find_contacts_error.

    The wrapper must guard before delegating: canonical ``company_match()`` has
    no None-guard, so ``canonical.find_contacts(None)`` would hit
    ``None.lower()`` -> AttributeError -> a spurious ``find_contacts_error`` on
    a perfectly normal empty-company job.
    """
    ingest, csv_path, events = seeded
    csv_path.write_text(_HEADER + "Ada,Lovelace,Meta,Director,01 Jan 2020,https://e.com/a\n")

    assert ingest.find_contacts(None) == []
    assert ingest.find_contacts("") == []
    assert ingest.find_contacts("   ") == []
    assert not any(e[0] == "find_contacts_error" for e in events), events


def test_ingest_renamed_header_fails_loudly_like_prep(seeded):
    """A renamed header logs find_contacts_error and returns [] on the ingest
    path — identical observable behavior to prep (AC3), not a silent swallow.
    """
    ingest, csv_path, events = seeded
    # 'Given Name' instead of 'First Name' — DictReader yields rows but the
    # canonical reader's row['First Name'] raises KeyError inside the loop.
    csv_path.write_text(
        "Given Name,Last Name,Company,Position,Connected On,URL\n"
        "Ada,Lovelace,Meta,Director,01 Jan 2020,https://e.com/a\n"
    )

    result = ingest.find_contacts("Meta")

    assert result == []
    assert any(e[0] == "find_contacts_error" for e in events), events
