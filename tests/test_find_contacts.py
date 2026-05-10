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


def test_company_match_blank_guard():
    from findajob.find_contacts import company_match

    assert company_match("", "Meta") is False
    assert company_match("Meta", "") is False
    assert company_match("", "") is False


def test_company_match_legitimate_suffix_stripped():
    from findajob.find_contacts import company_match

    # Suffix-strip variants of the same company still match.
    assert company_match("Apple", "Apple Inc.") is True
    assert company_match("Apple Inc.", "Apple") is True
    assert company_match("Acme LLC", "Acme Corp") is True
    assert company_match("OpenAI", "OpenAI") is True


def test_company_match_rejects_prefix_collision():
    from findajob.find_contacts import company_match

    # The headline #497 case: substring containment was matching these.
    assert company_match("Apple", "GreenApple Inc.") is False
    assert company_match("GreenApple Inc.", "Apple") is False


def test_company_match_rejects_short_string_substring():
    from findajob.find_contacts import company_match

    # "AI" is a real 2-letter company; substring `in` was matching every
    # row containing "ai" anywhere (Airbus, Maine-based startups, …).
    assert company_match("AI", "AIRBUS Inc.") is False
    assert company_match("Open", "OpenAI") is False
    # Negative direction also rejected
    assert company_match("AIRBUS", "AI") is False


def test_company_match_handles_hyphens_and_dots_correctly():
    from findajob.find_contacts import company_match

    # Hyphens are non-word characters — \b boundaries fire around them.
    assert company_match("Coca-Cola", "Coca-Cola Company") is True
    # Dots same — but a leading "co." abbreviation should not collide.
    assert company_match("Co", "Cocacola") is False


def test_company_match_multi_word_companies():
    from findajob.find_contacts import company_match

    # Multi-word search inside larger contact name still matches.
    assert company_match("Two Sigma", "Two Sigma Investments LLC") is True
    # But isolated word from search shouldn't match unrelated multi-word company.
    assert company_match("Two", "Two Sigma") is True  # "two" is a real word in "two sigma"
    assert company_match("Sigma", "Two Sigma") is True  # "sigma" is a real word
    # And cross-word substrings rejected.
    assert company_match("woSigma", "Two Sigma") is False


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
