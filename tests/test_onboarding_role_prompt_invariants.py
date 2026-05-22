"""Static invariants for `config/roles/onboarding_interviewer.md` (#621).

The onboarding interviewer's role prompt is purely instructions to an LLM —
no Python parser to test against. The failure mode #621 documents (LLM
mixes letters / digits / bullets across questions in the same session) is
behavioral and only verifiable empirically via a full interview run.

These tests guard against the **structural cause** the bug originated from:
optional "letter OR digit" phrasing in the meta-rule, which gave the LLM
freedom to drift. Once locked to a single style with explicit prohibition
on the others, mid-session drift becomes the LLM's mistake to fix, not
the prompt's licence to grant.

Tests are static-only (regex grep over the file). They're cheap, run
without network, and fire if a future edit reintroduces the optionality
that caused #621.
"""

from __future__ import annotations

import re
from pathlib import Path

_ROLE_PATH = Path(__file__).parent.parent / "config" / "roles" / "onboarding_interviewer.md"


def _read_role() -> str:
    return _ROLE_PATH.read_text(encoding="utf-8")


# ── #621: option-prefix style is locked, not optional ────────────────────


def test_meta_rule_does_not_offer_letter_or_digit_choice() -> None:
    """The original bug source — "letter (a, b, c, …) or digit (1, 2, 3, …)"
    in the meta-rule — must not return. The LLM treated this as licence to
    pick a style per question, producing the mid-session drift testers saw."""
    text = _read_role()
    # The exact phrasing pre-#621 was "Number or letter every list of options"
    # plus "prefix each with a letter (a, b, c, …) or digit (1, 2, 3, …)".
    # Guard both forms.
    assert "Number or letter every list" not in text, (
        "The meta-rule must commit to one option-prefix style, not offer the LLM a choice. See #621."
    )
    # Detect "letter ... or digit" / "digit ... or letter" patterns within ~80
    # chars of each other in the meta-rule paragraph. False-positive-safe
    # because legitimate Phase-5 emit rules use long-form constructs like
    # "3g selection includes `a` (paid service) OR `c` (Gmail alerts)" — the
    # tokens "letter" and "digit" don't co-occur there.
    assert not re.search(
        r"\bletter\b[^.]{0,80}\bor\b[^.]{0,80}\bdigit\b",
        text,
        re.IGNORECASE,
    ), "meta-rule still offers letter-OR-digit style choice; see #621"
    assert not re.search(
        r"\bdigit\b[^.]{0,80}\bor\b[^.]{0,80}\bletter\b",
        text,
        re.IGNORECASE,
    ), "meta-rule still offers digit-OR-letter style choice; see #621"


def test_meta_rule_commits_to_letter_style() -> None:
    """The replacement instruction picks letters (a, b, c, …) — the style
    every actual question in the prompt already uses (sub-phase 3g source
    selection, Pass A/B category lists). The bullet "Letter every list of
    options" anchors the rule; absence means the meta-rule was dropped
    or weakened."""
    text = _read_role()
    assert "Letter every list of options" in text, (
        "meta-rule heading 'Letter every list of options' is missing; see #621"
    )


def test_meta_rule_forbids_bullets() -> None:
    """Bullets ("- foo", "* foo") were the third drift mode the tester
    observed. The meta-rule must explicitly forbid them — without that,
    the LLM falls back to its training-default list style on long lists."""
    text = _read_role()
    # Match the explicit prohibition phrase in the rewritten rule.
    assert re.search(
        r"Do NOT use\s+(digits|bullets)|never use\s+(digits|bullets)",
        text,
        re.IGNORECASE,
    ), "meta-rule must explicitly forbid digits and bullets to prevent drift; see #621"


# ── #753: opaque-token confirmation must echo the full value verbatim ────


def test_opaque_token_rule_present() -> None:
    """The general Phase-5 opaque-token rule must be present.

    The bug shape (#753): during the live #672 walkthrough, the interviewer
    confirmed the user's just-provided ntfy topic by showing "last 4 chars"
    — and hallucinated the substring by one character. Storage was correct,
    but the chat reflection was wrong. The fix is a prompt-level rule
    requiring full-string echo in backticks for any non-language token,
    plus an explicit anti-pattern against partial-character extraction.

    Both pieces must be present together — the positive rule alone passes
    a soft contains-check that wouldn't catch the bug class, and the
    anti-pattern alone gives the LLM no positive instruction to follow.
    """
    text = _read_role()
    # Positive rule: full-value echo in backticks. Heading match anchors the
    # rule's location in Phase 5 so a future doc-restructure that displaces
    # the rule fails loudly instead of quietly losing it.
    assert "Confirming opaque tokens" in text, "Phase-5 'Confirming opaque tokens' rule heading missing; see #753"
    assert re.search(
        r"full\s+value\s+verbatim",
        text,
        re.IGNORECASE,
    ), "opaque-token rule must instruct full-value verbatim echo; see #753"

    # Anti-pattern: explicit prohibition against last-N-characters or substring
    # reflection. The bare positive rule is too soft — LLMs route around it.
    assert re.search(
        r"Do NOT show only\s+(?:the\s+)?`?last\s+N\s+characters`?",
        text,
    ), "opaque-token rule must explicitly forbid 'last N characters' reflection; see #753"
    assert re.search(
        r"\bsubstring\b",
        text,
        re.IGNORECASE,
    ), "opaque-token rule must explicitly forbid substring reflection; see #753"


def test_ntfy_topic_section_references_opaque_token_rule() -> None:
    """The ntfy_topic.txt section was the documented failure site in #753;
    its 'Confirm before emitting' line must point at the opaque-token rule
    AND restate the anti-pattern locally. Both are required so the LLM
    hits the rule on the actual emission path, not just in the general
    Phase-5 preamble it may have lost focus on by Group 1 turn-45+.
    """
    text = _read_role()
    # Pull the ntfy_topic.txt block — between its enumerator and the next
    # blank-line-bounded section. Generous regex; anchor on the label.
    match = re.search(
        r"5\.\s+`ntfy_topic\.txt`.*?(?=\n\nGroup 2)",
        text,
        re.DOTALL,
    )
    assert match is not None, "could not locate ntfy_topic.txt section; prompt structure changed?"
    section = match.group(0)
    assert "Confirming opaque tokens" in section, (
        "ntfy_topic.txt section must reference the 'Confirming opaque tokens' rule; see #753"
    )
    assert re.search(
        r"full\s+value\s+verbatim",
        section,
        re.IGNORECASE,
    ), "ntfy_topic.txt section must restate the full-value-verbatim instruction locally; see #753"
    assert re.search(
        r"Do NOT show only\s+(?:the\s+)?`?last\s+N\s+characters`?",
        section,
    ), "ntfy_topic.txt section must restate the last-N-characters anti-pattern locally; see #753"
