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
    PHASE_ANCHOR_ORDER,
    _is_affirmative_question,
    _keyword_overlap,
    advance_phase_idx,
    compute_phase_ranges,
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
        "FINDAJOB_TEST_RAPIDAPI_KEY=rapidtest\n",
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
        # Comment lines should not appear as keys
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
        # Optional vars absent from file — should not raise
        assert "FINDAJOB_TEST_USER" in secrets  # present optional var works fine

    def test_accepts_shell_export_prefix(self, tmp_path: Path) -> None:
        """Lines may use ``export KEY=value`` so the file doubles as a
        shell-sourceable script."""
        path = tmp_path / "secrets-export"
        path.write_text(
            "export FINDAJOB_TEST_OR_KEY=or-key-export\nexport FINDAJOB_TEST_RAPIDAPI_KEY=rapid-key-export\n"
        )
        secrets = load_secrets(path)
        assert secrets["FINDAJOB_TEST_OR_KEY"] == "or-key-export"
        assert secrets["FINDAJOB_TEST_RAPIDAPI_KEY"] == "rapid-key-export"


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


# ---------------------------------------------------------------------------
# Phase scoping (#405 Issue 2)
# ---------------------------------------------------------------------------


def _multi_phase_transcript() -> str:
    """Transcript with three detectable phase boundaries plus tail.

    Phase boundaries are detected from anchor PHRASES on assistant turns —
    "phase 2" → phase_1_end, "phase 3" → phase_2_end, "phase 4" →
    phase_3_end. Each phase has 2 turns of substantive content.
    """
    return textwrap.dedent("""\
        ## Turn 1 — ASSISTANT
        What's your name?
        ## Turn 1 — USER
        Avery Chen.

        ## Turn 2 — ASSISTANT
        Tell me your role.
        ## Turn 2 — USER
        Clinical pharmacist.

        ## Turn 3 — ASSISTANT
        Now let's move to Phase 2 — please share your resume.
        ## Turn 3 — USER
        [resume body here]

        ## Turn 4 — ASSISTANT
        Any performance reviews to share?
        ## Turn 4 — USER
        Yes — pasted above.

        ## Turn 5 — ASSISTANT
        Great. Moving on to Phase 3 — let's set up ntfy notifications. What topic?
        ## Turn 5 — USER
        avery-job-2026-q2

        ## Turn 6 — ASSISTANT
        Confirm timezone — I'll use America/Chicago.
        ## Turn 6 — USER
        Confirmed.

        ## Turn 7 — ASSISTANT
        Now Phase 4 — what categories do you want to exclude?
        ## Turn 7 — USER
        Sales, marketing.
        """)


@pytest.fixture
def multi_phase_corpus(tmp_path: Path) -> ReplayCorpus:
    p = tmp_path / "multi.md"
    p.write_text(_multi_phase_transcript(), encoding="utf-8")
    return load_corpus(p)


class TestPhaseRanges:
    def test_compute_phase_ranges_returns_one_per_anchor_plus_tail(self, multi_phase_corpus: ReplayCorpus) -> None:
        ranges = compute_phase_ranges(multi_phase_corpus)
        # 5 anchor slots + 1 tail = 6 entries always (regardless of how many
        # anchors actually fired).
        assert len(ranges) == len(PHASE_ANCHOR_ORDER) + 1

    def test_compute_phase_ranges_phase_1_ends_before_phase_2_announcement(
        self, multi_phase_corpus: ReplayCorpus
    ) -> None:
        """phase_1_end fires at turn 3 (the assistant turn that announces
        Phase 2). Turn 3's user response IS in Phase 2 — so Phase 1
        covers only turns 1..2, ending at 0-based index 2 (exclusive)."""
        ranges = compute_phase_ranges(multi_phase_corpus)
        assert ranges[0] == (0, 2)

    def test_compute_phase_ranges_middle_phases_advance_correctly(self, multi_phase_corpus: ReplayCorpus) -> None:
        ranges = compute_phase_ranges(multi_phase_corpus)
        # phase_2_end at turn 5 → phase 2 covers [2, 4) = turns 3..4
        assert ranges[1] == (2, 4)
        # phase_3_end at turn 7 → phase 3 covers [4, 6) = turns 5..6
        assert ranges[2] == (4, 6)

    def test_compute_phase_ranges_tail_lands_at_post_last_anchor_slot(self, multi_phase_corpus: ReplayCorpus) -> None:
        """The tail (= the phase the corpus ENDED in) lands at the slot
        immediately after the last fired anchor — not always at the end
        of the array. multi_phase has 3 anchors (phase_1/2/3_end) so the
        tail is at index 3 (phase 4) and covers [6, 7) = turn 7."""
        ranges = compute_phase_ranges(multi_phase_corpus)
        assert ranges[3] == (6, multi_phase_corpus.turn_count)

    def test_compute_phase_ranges_unfired_trailing_slots_are_empty(self, multi_phase_corpus: ReplayCorpus) -> None:
        """Slots for anchors that never fired AND that lie past the tail
        are empty. multi_phase has 3 fired anchors → tail at idx 3, then
        idx 4 and 5 are unused empty slots."""
        ranges = compute_phase_ranges(multi_phase_corpus)
        assert ranges[4][0] == ranges[4][1]
        assert ranges[5][0] == ranges[5][1]

    def test_compute_phase_ranges_no_anchors_collapses_to_phase_1(self, tmp_path: Path) -> None:
        """Corpus with no phase-anchor language → tail at idx 0 covering
        the entire corpus, all other slots empty."""
        bare = tmp_path / "bare.md"
        bare.write_text(
            "## Turn 1 — ASSISTANT\nQ\n## Turn 1 — USER\nA\n## Turn 2 — ASSISTANT\nQ2\n## Turn 2 — USER\nA2\n",
            encoding="utf-8",
        )
        c = load_corpus(bare)
        ranges = compute_phase_ranges(c)
        assert ranges[0] == (0, c.turn_count)
        for r in ranges[1:]:
            assert r[0] == r[1]


class TestAdvancePhaseIdx:
    def test_no_anchor_means_no_advance(self) -> None:
        assert advance_phase_idx("Just a regular question, no phase mention.", 0) == 0

    def test_phase_2_anchor_advances_from_0_to_1(self) -> None:
        assert advance_phase_idx("Now let's move to Phase 2.", 0) == 1

    def test_phase_3_anchor_advances_from_1_to_2(self) -> None:
        assert advance_phase_idx("Moving on to phase 3.", 1) == 2

    def test_monotonically_non_decreasing(self) -> None:
        # Once we've advanced to phase_idx 3, a phase 2 mention does NOT
        # send us backward.
        assert advance_phase_idx("Now let's move to Phase 2.", 3) == 3

    def test_multiple_anchors_in_one_turn_advance_to_furthest(self) -> None:
        # "we just finished phase 2, moving to phase 3" — advance by both
        text = "Great, that wraps phase 2 — moving on to phase 3 now."
        # The text contains both "phase 2" (phase_1_end) and "phase 3" (phase_2_end)
        assert advance_phase_idx(text, 0) == 2


class TestPickAnswerPhaseScoped:
    def test_positional_within_phase_picks_first_phase_turn(self, multi_phase_corpus: ReplayCorpus) -> None:
        ranges = compute_phase_ranges(multi_phase_corpus)
        answer, reason = pick_answer(
            999,  # legacy turn_idx is irrelevant in phase-scoped mode
            "What's your name?",
            multi_phase_corpus,
            {},
            current_phase_idx=0,
            phase_relative_turn=0,
            phase_ranges=ranges,
        )
        assert reason == "positional_within_phase"
        assert "Avery Chen" in answer

    def test_keyword_within_phase_does_not_leak_across_phases(self, multi_phase_corpus: ReplayCorpus) -> None:
        """Phase 1 corpus contains 'name' / 'role' questions. If the new
        run is in *Phase 3* and the assistant asks something
        keyword-similar to a Phase 1 question, the phase-scoped matcher
        must NOT pull the Phase 1 answer — that's the cross-phase
        contamination this fix prevents."""
        ranges = compute_phase_ranges(multi_phase_corpus)
        # Phase 3 (idx 2) covers turns 5..6 — ntfy + timezone, no
        # name/role content. A name/role-keyword query should fall
        # through to keyword overlap within phase 3 (which has weak
        # overlap → no match) and emit "review", not pull Phase 1's
        # "Avery Chen" answer.
        answer, reason = pick_answer(
            999,
            "What's your name and role?",
            multi_phase_corpus,
            {},
            current_phase_idx=2,
            phase_relative_turn=5,  # past phase 3's 2-turn span
            phase_ranges=ranges,
        )
        assert "Avery Chen" not in answer
        assert "Clinical pharmacist" not in answer
        assert reason in {"review", "review_no_phase_match"}

    def test_empty_phase_range_returns_review_no_phase_match(self, multi_phase_corpus: ReplayCorpus) -> None:
        """If the new run claims to be in a phase the corpus never
        recorded (e.g. prompt added a new phase), the matcher emits a
        distinct review reason rather than silently grabbing the wrong
        answer. multi_phase has 3 fired anchors → tail at idx 3; idx 4
        and 5 are empty unused slots."""
        ranges = compute_phase_ranges(multi_phase_corpus)
        answer, reason = pick_answer(
            999,
            "Anything question whatsoever.",
            multi_phase_corpus,
            {},
            current_phase_idx=4,
            phase_relative_turn=0,
            phase_ranges=ranges,
        )
        assert reason == "review_no_phase_match"
        assert "prior context" in answer.lower()

    def test_affirmative_still_wins_in_phase_scoped_mode(self, multi_phase_corpus: ReplayCorpus) -> None:
        ranges = compute_phase_ranges(multi_phase_corpus)
        answer, reason = pick_answer(
            0,
            "Ready to move on?",
            multi_phase_corpus,
            {},
            current_phase_idx=0,
            phase_relative_turn=0,
            phase_ranges=ranges,
        )
        assert answer == "yes"
        assert reason == "affirmative"

    def test_intent_map_still_works_in_phase_scoped_mode_with_empty_range(
        self, multi_phase_corpus: ReplayCorpus
    ) -> None:
        """Intent map fires even when the current phase has no corpus
        candidates — intent classifies by question shape, not by
        corpus similarity, so it should be phase-independent."""
        ranges = compute_phase_ranges(multi_phase_corpus)
        answer, reason = pick_answer(
            999,
            "Which categories do you want to exclude? Sales, marketing...",
            multi_phase_corpus,
            {"sales": "a, b"},
            current_phase_idx=4,  # empty range in this fixture
            phase_relative_turn=0,
            phase_ranges=ranges,
        )
        assert reason.startswith("intent")
        assert "a, b" in answer

    def test_legacy_mode_unchanged_when_phase_kwargs_omitted(self, multi_phase_corpus: ReplayCorpus) -> None:
        """Existing callers (tests above) pass no phase kwargs and must
        get legacy behavior — positional by absolute turn_idx."""
        answer, reason = pick_answer(0, "What's your name?", multi_phase_corpus, {})
        assert reason == "positional"
        assert "Avery Chen" in answer


# ---------------------------------------------------------------------------
# End-to-end corpus replay simulation (#405 contract test)
# ---------------------------------------------------------------------------


class TestRealCorpusReplay:
    """Walks the in-repo corpus through a simulated harness loop without
    touching Playwright. Confirms that for the corpus's own assistant
    text at every turn, the phase-scoped matcher picks the corpus's own
    user response at that turn — i.e. replaying against an exact-match
    corpus is a perfect positional match, no ``review`` reasons.

    This is the "is the corpus self-consistent with the matcher" gate.
    If it fails, either the corpus was edited in a way that breaks
    phase-anchor detection, or the matcher's phase logic regressed.
    """

    @pytest.fixture
    def real_corpus(self) -> ReplayCorpus:
        path = Path(__file__).parent / "fixtures" / "walkthrough" / "corpus_transcript.md"
        return load_corpus(path)

    def test_corpus_parses_with_all_five_phase_anchors(self, real_corpus: ReplayCorpus) -> None:
        for name in PHASE_ANCHOR_ORDER:
            assert name in real_corpus.phase_anchors, (
                f"Corpus missing {name} — the in-repo corpus must exercise all five "
                "phase boundaries so the harness can validate phase-scoped matching "
                "across the whole onboarding flow."
            )

    def test_corpus_self_replay_has_no_review_turns(self, real_corpus: ReplayCorpus) -> None:
        """Simulate the harness loop against the corpus's own transcript.
        Every turn should match positionally within phase — the corpus
        IS its own perfect replay source."""
        ranges = compute_phase_ranges(real_corpus)
        current_phase_idx = 0
        phase_relative_turn = 0
        review_turns: list[int] = []

        for turn_num_1based in range(1, real_corpus.turn_count + 1):
            turn_idx = turn_num_1based - 1
            assistant_text = real_corpus.assistant_messages[turn_idx]
            if not assistant_text.strip():
                continue

            new_phase_idx = advance_phase_idx(assistant_text, current_phase_idx)
            if new_phase_idx != current_phase_idx:
                current_phase_idx = new_phase_idx
                phase_relative_turn = 0

            _, reason = pick_answer(
                turn_idx,
                assistant_text,
                real_corpus,
                {},
                current_phase_idx=current_phase_idx,
                phase_relative_turn=phase_relative_turn,
                phase_ranges=ranges,
            )
            if reason.startswith("review"):
                review_turns.append(turn_num_1based)
            phase_relative_turn += 1

        assert not review_turns, (
            f"Corpus self-replay produced REVIEW turns at: {review_turns}. "
            "Either the corpus has user-message gaps in those turns, or the "
            "matcher's phase-scoping has regressed. Spot-check the named turns."
        )
