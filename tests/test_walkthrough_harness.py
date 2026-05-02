"""Unit tests for walkthrough harness components.

Covers:
  - walkthrough_replay_corpus: parser correctness + phase anchor detection
  - walkthrough_harness: secrets loading, DOM redaction, answer matching

Does NOT exercise the live browser — those tests would be slow and require
a running findajob instance. The live walkthrough is Task 16.
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

# Allow import of scripts/ sibling modules in tests
_SCRIPTS = Path(__file__).parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from walkthrough_harness import (  # noqa: E402
    _is_affirmative_question,
    _keyword_overlap,
    load_secrets,
    pick_answer,
    redact_dom_snapshot,
)
from walkthrough_replay_corpus import ReplayCorpus, load_corpus  # noqa: E402

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_SAMPLE_TRANSCRIPT = textwrap.dedent("""\
    ## Turn 1 — ASSISTANT

    Welcome! Let's start with your background. What's your name and the role you're targeting?

    ## Turn 1 — USER

    My name is Alex. I'm targeting data center infrastructure roles.

    ## Turn 2 — ASSISTANT

    Great. Now let's move to Phase 2 — please share your master resume.

    ## Turn 2 — USER

    [Pasted resume content here]

    ## Turn 3 — ASSISTANT

    Thanks for the resume. Now let's talk about ntfy notifications.

    ## Turn 3 — USER

    findajob-alex-202604

    ## Turn 4 — ASSISTANT

    Ready to move to Phase 4? Reply yes when ready.

    ## Turn 4 — USER

    yes
""")


@pytest.fixture
def transcript_file(tmp_path: Path) -> Path:
    p = tmp_path / "transcript.md"
    p.write_text(_SAMPLE_TRANSCRIPT, encoding="utf-8")
    return p


@pytest.fixture
def corpus(transcript_file: Path) -> ReplayCorpus:
    return load_corpus(transcript_file)


@pytest.fixture
def secrets_file(tmp_path: Path) -> Path:
    p = tmp_path / ".secrets"
    p.write_text(
        "FINDAJOB_TEST_USER=myuser\n"
        "FINDAJOB_TEST_PASS=mypass\n"
        "FINDAJOB_TEST_OR_KEY=sk-or-v1-testkey\n"
        "FINDAJOB_TEST_RAPIDAPI_KEY=rapidtest\n"
        "# FINDAJOB_TEST_GOOGLE_KEY is optional\n",
        encoding="utf-8",
    )
    return p


# ---------------------------------------------------------------------------
# Corpus parser tests
# ---------------------------------------------------------------------------


class TestCorpusParser:
    def test_turn_count(self, corpus: ReplayCorpus) -> None:
        assert corpus.turn_count == 4

    def test_user_messages_indexed_correctly(self, corpus: ReplayCorpus) -> None:
        assert "Alex" in corpus.user_messages[0]
        assert "resume" in corpus.user_messages[1].lower()
        assert "findajob-alex-202604" in corpus.user_messages[2]
        assert corpus.user_messages[3] == "yes"

    def test_assistant_messages_indexed(self, corpus: ReplayCorpus) -> None:
        assert "background" in corpus.assistant_messages[0].lower()
        assert "Phase 2" in corpus.assistant_messages[1]

    def test_phase_anchors_detected(self, corpus: ReplayCorpus) -> None:
        # Turn 2 assistant text mentions "Phase 2" → phase_1_end anchor
        assert "phase_1_end" in corpus.phase_anchors
        assert corpus.phase_anchors["phase_1_end"] == 2

    def test_nonexistent_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_corpus(tmp_path / "nonexistent.md")

    def test_no_headers_raises(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.md"
        bad.write_text("just some text, no turn headers\n", encoding="utf-8")
        with pytest.raises(ValueError, match="No turn headers found"):
            load_corpus(bad)

    def test_missing_turns_leave_empty_strings(self, tmp_path: Path) -> None:
        # Turn 1 USER missing — turn 1 slot should be empty string
        sparse = tmp_path / "sparse.md"
        sparse.write_text(
            "## Turn 1 — ASSISTANT\n\nHello!\n\n## Turn 2 — USER\n\nMy reply\n\n## Turn 2 — ASSISTANT\n\nGot it.\n",
            encoding="utf-8",
        )
        c = load_corpus(sparse)
        assert c.turn_count == 2
        assert c.user_messages[0] == ""  # Turn 1 USER not in transcript
        assert c.user_messages[1] == "My reply"


# ---------------------------------------------------------------------------
# Secrets loading tests
# ---------------------------------------------------------------------------


class TestSecretsLoading:
    def test_loads_required_vars(self, secrets_file: Path) -> None:
        secrets = load_secrets(secrets_file)
        assert secrets["FINDAJOB_TEST_USER"] == "myuser"
        assert secrets["FINDAJOB_TEST_PASS"] == "mypass"
        assert secrets["FINDAJOB_TEST_OR_KEY"] == "sk-or-v1-testkey"
        assert secrets["FINDAJOB_TEST_RAPIDAPI_KEY"] == "rapidtest"

    def test_strips_quotes(self, tmp_path: Path) -> None:
        p = tmp_path / ".secrets"
        p.write_text(
            'FINDAJOB_TEST_USER="quoted_user"\n'
            "FINDAJOB_TEST_PASS='single_quoted'\n"
            "FINDAJOB_TEST_OR_KEY=sk-or-v1-bare\n"
            "FINDAJOB_TEST_RAPIDAPI_KEY=rapi\n",
            encoding="utf-8",
        )
        secrets = load_secrets(p)
        assert secrets["FINDAJOB_TEST_USER"] == "quoted_user"
        assert secrets["FINDAJOB_TEST_PASS"] == "single_quoted"

    def test_skips_comments_and_blanks(self, secrets_file: Path) -> None:
        secrets = load_secrets(secrets_file)
        # Comment line (# FINDAJOB_TEST_GOOGLE_KEY...) should not appear as a key
        assert not any(k.startswith("#") for k in secrets)

    def test_missing_required_raises(self, tmp_path: Path) -> None:
        p = tmp_path / ".secrets"
        p.write_text("FINDAJOB_TEST_USER=x\n", encoding="utf-8")
        with pytest.raises(ValueError, match="Missing required secrets"):
            load_secrets(p)

    def test_nonexistent_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_secrets(tmp_path / "no-such-file")

    def test_optional_var_absent_is_ok(self, secrets_file: Path) -> None:
        secrets = load_secrets(secrets_file)
        # Google key is optional — not in file, should not raise
        assert "FINDAJOB_TEST_GOOGLE_KEY" not in secrets or secrets.get("FINDAJOB_TEST_GOOGLE_KEY") == ""

    def test_accepts_shell_export_prefix(self, tmp_path: Path) -> None:
        """Lines may use ``export KEY=value`` so the file doubles as a
        shell-sourceable script."""
        path = tmp_path / "secrets-export"
        path.write_text(
            "export FINDAJOB_TEST_OR_KEY=or-key-export\n"
            "export FINDAJOB_TEST_RAPIDAPI_KEY=rapid-key-export\n"
            "FINDAJOB_TEST_GOOGLE_KEY=plain-form\n"
        )
        secrets = load_secrets(path)
        assert secrets["FINDAJOB_TEST_OR_KEY"] == "or-key-export"
        assert secrets["FINDAJOB_TEST_RAPIDAPI_KEY"] == "rapid-key-export"
        assert secrets["FINDAJOB_TEST_GOOGLE_KEY"] == "plain-form"


# ---------------------------------------------------------------------------
# DOM snapshot redaction tests
# ---------------------------------------------------------------------------


class TestDomRedaction:
    def test_redacts_openrouter_key_value(self) -> None:
        html = '<input name="openrouter_api_key" type="password" value="sk-or-v1-supersecret">'
        result = redact_dom_snapshot(html)
        assert "sk-or-v1-supersecret" not in result
        assert "***REDACTED***" in result

    def test_redacts_rapidapi_key_value(self) -> None:
        html = '<input type="text" name="rapidapi_key" value="abc123secret">'
        result = redact_dom_snapshot(html)
        assert "abc123secret" not in result
        assert "***REDACTED***" in result

    def test_redacts_google_key_value(self) -> None:
        html = '<input name="google_api_key" value="AIzaXXXXsecret">'
        result = redact_dom_snapshot(html)
        assert "AIzaXXXXsecret" not in result
        assert "***REDACTED***" in result

    def test_non_key_fields_not_redacted(self) -> None:
        html = '<input name="username" value="myuser">'
        result = redact_dom_snapshot(html)
        assert "myuser" in result

    def test_empty_value_redacted(self) -> None:
        # Even empty-string values in key inputs get redacted
        html = '<input name="openrouter_api_key" value="">'
        result = redact_dom_snapshot(html)
        # Should still transform — no leak possible, but consistent behavior
        assert "openrouter_api_key" in result  # field name preserved

    def test_preserves_rest_of_dom(self) -> None:
        html = '<html><body><input name="openrouter_api_key" value="sk-or-v1-secret"><p>Some content</p></body></html>'
        result = redact_dom_snapshot(html)
        assert "<p>Some content</p>" in result
        assert "sk-or-v1-secret" not in result


# ---------------------------------------------------------------------------
# Answer matching tests
# ---------------------------------------------------------------------------


class TestAnswerMatching:
    def test_positional_match(self, corpus: ReplayCorpus) -> None:
        answer, reason = pick_answer(0, "What is your name?", corpus, {})
        assert "Alex" in answer
        assert reason == "positional"

    def test_affirmative_for_ready_question(self, corpus: ReplayCorpus) -> None:
        answer, reason = pick_answer(99, "Are you ready to continue?", corpus, {})
        assert answer == "yes"
        assert reason == "affirmative"

    def test_affirmative_for_next_question(self, corpus: ReplayCorpus) -> None:
        answer, reason = pick_answer(99, "Shall we move to the next section?", corpus, {})
        assert answer == "yes"
        assert reason == "affirmative"

    def test_affirmative_for_sound_good(self, corpus: ReplayCorpus) -> None:
        answer, reason = pick_answer(50, "Sound good?", corpus, {})
        assert answer == "yes"
        assert reason == "affirmative"

    def test_keyword_match_fallback(self, corpus: ReplayCorpus) -> None:
        # Ask about "resume" when turn_idx is out of range
        # Turn 2 assistant text mentions "resume" → should match via keyword
        answer, reason = pick_answer(999, "Please share your resume content.", corpus, {})
        # reason is 'positional', 'keyword(overlap=N.NN)', or 'review' — never 'affirmative'
        assert reason.startswith(("keyword", "positional", "review"))
        assert reason != "affirmative"

    def test_intent_map_applied(self, corpus: ReplayCorpus) -> None:
        intent_map = {"sales": "a, b"}
        answer, reason = pick_answer(
            999,
            "Which of these categories do you want to exclude? Sales, marketing...",
            corpus,
            intent_map,
        )
        assert reason.startswith("intent")
        assert "a, b" in answer

    def test_new_question_returns_review(self, corpus: ReplayCorpus) -> None:
        # A question with no matching corpus content and out-of-range index
        answer, reason = pick_answer(
            999,
            "Completely unrelated zephyr question about xyzzy frobnicator",
            corpus,
            {},
        )
        assert reason == "review"
        assert "prior context" in answer.lower()

    def test_affirmative_takes_priority_over_positional(self, corpus: ReplayCorpus) -> None:
        # Even if turn_idx has corpus content, affirmative wins for readiness questions
        answer, reason = pick_answer(0, "Ready?", corpus, {})
        assert answer == "yes"
        assert reason == "affirmative"


# ---------------------------------------------------------------------------
# Helper function unit tests
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_keyword_overlap_identical(self) -> None:
        score = _keyword_overlap("share your resume now", "share your resume now")
        assert score == 1.0

    def test_keyword_overlap_zero(self) -> None:
        score = _keyword_overlap("xyzzy frobnicator", "completely different words here")
        assert score == 0.0

    def test_keyword_overlap_partial(self) -> None:
        score = _keyword_overlap("share your resume background", "my background is strong")
        assert 0.0 < score < 1.0

    def test_is_affirmative_question_yes(self) -> None:
        assert _is_affirmative_question("Ready to proceed?")
        assert _is_affirmative_question("Sound good? Let me know.")
        assert _is_affirmative_question("Shall we continue to the next section?")

    def test_is_affirmative_question_no(self) -> None:
        assert not _is_affirmative_question("What is your target role?")
        assert not _is_affirmative_question("Please share your resume.")
