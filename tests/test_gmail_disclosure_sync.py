"""Asserts the disclosure-language sync chain stays intact.

The Jinja partial templates/_gmail_disclosure.html is the single source
of truth. docs/setup/gmail.md must contain the marker comment so the
docs renderer knows where to substitute. If either drifts, this test
catches it.
"""

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PARTIAL = REPO / "src" / "findajob" / "web" / "templates" / "_gmail_disclosure.html"
DOC = REPO / "docs" / "setup" / "gmail.md"
MARKER = "<!-- gmail-disclosure-sync -->"


def test_disclosure_partial_exists():
    assert PARTIAL.exists()


def test_gmail_doc_exists():
    assert DOC.exists()


def test_gmail_doc_has_marker():
    assert MARKER in DOC.read_text(), (
        f"docs/setup/gmail.md must contain the {MARKER!r} comment marker "
        f"so the docs renderer can substitute the disclosure partial."
    )
