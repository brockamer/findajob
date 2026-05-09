"""Regression tests for findajob.find_contacts — #184 / #164.

Missing connections.csv must degrade silently (no find_contacts_error event).
True parse/IO errors must still log.

Module path moved from ``scripts.find_contacts`` to ``findajob.find_contacts``
in #557 (M3+ extraction).
"""

from __future__ import annotations

import pytest


@pytest.fixture
def fc(monkeypatch, tmp_path):
    """Import find_contacts module with CONNECTIONS redirected to a tmp path."""
    from findajob import find_contacts as mod

    csv_path = tmp_path / "connections.csv"
    monkeypatch.setattr(mod, "CONNECTIONS", str(csv_path))
    return mod, csv_path


def test_missing_file_returns_empty_with_no_error_logged(fc, monkeypatch):
    mod, csv_path = fc
    assert not csv_path.exists()

    events: list[tuple] = []
    monkeypatch.setattr(mod, "log_event", lambda name, **kw: events.append((name, kw)))

    result = mod.find_contacts("Meta")

    assert result == []
    assert not any(e[0] == "find_contacts_error" for e in events), events


def test_present_file_with_match_returns_contact(fc, monkeypatch):
    mod, csv_path = fc
    csv_path.write_text(
        "First Name,Last Name,Company,Position,Connected On,URL\n"
        "Ada,Lovelace,Meta Platforms,Engineering Director,01 Jan 2020,https://example.com/ada\n"
    )

    events: list[tuple] = []
    monkeypatch.setattr(mod, "log_event", lambda name, **kw: events.append((name, kw)))

    result = mod.find_contacts("Meta")

    assert len(result) == 1
    assert result[0]["name"] == "Ada Lovelace"
    assert not any(e[0] == "find_contacts_error" for e in events)


def test_malformed_csv_still_logs_error(fc, monkeypatch):
    """True parse/IO errors must still surface — only FileNotFoundError is silenced."""
    mod, csv_path = fc
    # CSV missing the 'First Name' column — DictReader will yield rows but
    # row['First Name'] raises KeyError inside the loop.
    csv_path.write_text(
        "Given Name,Last Name,Company,Position,Connected On,URL\n"
        "Ada,Lovelace,Meta,Director,01 Jan 2020,https://example.com/ada\n"
    )

    events: list[tuple] = []
    monkeypatch.setattr(mod, "log_event", lambda name, **kw: events.append((name, kw)))

    result = mod.find_contacts("Meta")

    assert result == []
    assert any(e[0] == "find_contacts_error" for e in events), events
