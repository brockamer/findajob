"""Tests for findajob.onboarding.voice_processor (#262)."""

from __future__ import annotations

from unittest.mock import patch

from findajob.onboarding.voice_processor import (
    clean_voice_samples,
    process_voice_samples,
    redact_voice_samples,
)

# ── clean_voice_samples ─────────────────────────────────────────────────────


class TestCleanVoiceSamples:
    def test_empty_input_returns_empty(self) -> None:
        assert clean_voice_samples("") == ""

    def test_whitespace_only_returns_empty(self) -> None:
        assert clean_voice_samples("   \n\n   \t  \n") == ""

    def test_pure_prose_passes_through_unchanged(self) -> None:
        prose = "This is a regular sentence. So is this one.\n\nNew paragraph here."
        assert clean_voice_samples(prose) == prose

    def test_strips_atx_headers(self) -> None:
        text = "# Big Header\n\nReal prose here.\n\n## Subhead\n\nMore prose."
        result = clean_voice_samples(text)
        assert "Big Header" not in result
        assert "Subhead" not in result
        assert "Real prose here." in result
        assert "More prose." in result

    def test_strips_yaml_frontmatter(self) -> None:
        text = "---\ntitle: My Post\ndate: 2025-01-01\n---\nReal prose follows."
        assert clean_voice_samples(text) == "Real prose follows."

    def test_strips_md_images(self) -> None:
        text = "Before image. ![alt text](https://example.com/x.png) After image."
        result = clean_voice_samples(text)
        assert "![" not in result
        assert "alt text" not in result
        assert "Before image." in result
        assert "After image." in result

    def test_strips_html_img_tags(self) -> None:
        text = 'Before <img src="x.png" alt="thing"> after.'
        result = clean_voice_samples(text)
        assert "<img" not in result
        assert "Before" in result
        assert "after." in result

    def test_strips_bracket_image_placeholders(self) -> None:
        text = "Before [image: a photo of something] after."
        result = clean_voice_samples(text)
        assert "[image:" not in result
        assert "Before" in result
        assert "after." in result

    def test_link_syntax_keeps_visible_text(self) -> None:
        text = "Check out [my favorite tool](https://example.com) for this."
        result = clean_voice_samples(text)
        assert "https://example.com" not in result
        assert "my favorite tool" in result

    def test_strips_bold_keeps_content(self) -> None:
        text = "This is **really important** to know."
        result = clean_voice_samples(text)
        assert "**" not in result
        assert "really important" in result

    def test_strips_italic_keeps_content(self) -> None:
        text = "This was *deeply* meaningful."
        result = clean_voice_samples(text)
        assert "deeply" in result
        # Asterisks around 'deeply' should be gone
        assert "*deeply*" not in result

    def test_strips_inline_code_keeps_content(self) -> None:
        text = "I ran `npm install` just like that."
        result = clean_voice_samples(text)
        assert "`" not in result
        assert "npm install" in result

    def test_strips_blockquote_marker_keeps_content(self) -> None:
        text = "> This is a quoted line.\n> And another."
        result = clean_voice_samples(text)
        assert "> " not in result
        assert "This is a quoted line." in result
        assert "And another." in result

    def test_strips_horizontal_rules(self) -> None:
        text = "Above the rule.\n\n---\n\nBelow the rule."
        result = clean_voice_samples(text)
        # Three or more dashes on their own line should be gone
        assert "\n---\n" not in result
        assert "Above the rule." in result
        assert "Below the rule." in result

    def test_strips_fenced_code_blocks(self) -> None:
        text = "Before code.\n\n```python\nprint('hello')\n```\n\nAfter code."
        result = clean_voice_samples(text)
        assert "print" not in result
        assert "```" not in result
        assert "Before code." in result
        assert "After code." in result

    def test_strips_footnote_markers(self) -> None:
        text = "This needs a citation[^1] for context."
        result = clean_voice_samples(text)
        assert "[^1]" not in result
        assert "This needs a citation for context." in result

    def test_strips_html_tags(self) -> None:
        text = "Before <span class='x'>some text</span> after."
        result = clean_voice_samples(text)
        assert "<span" not in result
        assert "</span>" not in result
        assert "some text" in result

    def test_strips_table_rows(self) -> None:
        text = "Before table.\n\n| col | col |\n|---|---|\n| a | b |\n\nAfter table."
        result = clean_voice_samples(text)
        assert "| col" not in result
        assert "Before table." in result
        assert "After table." in result

    def test_collapses_excessive_blank_lines(self) -> None:
        text = "Para one.\n\n\n\n\nPara two."
        result = clean_voice_samples(text)
        assert "\n\n\n" not in result
        assert "Para one." in result
        assert "Para two." in result

    def test_preserves_em_dashes(self) -> None:
        text = "This — exactly this — is voice signal."
        assert "—" in clean_voice_samples(text)

    def test_preserves_typos_and_idioms(self) -> None:
        # "different that" is a typo but voice signal
        text = "no different that any other drug, ya know"
        assert clean_voice_samples(text) == text

    def test_preserves_parenthetical_asides(self) -> None:
        text = "I went to see the doctor (who turned out to be a quack)."
        assert clean_voice_samples(text) == text

    def test_strict_only_structural_text_returns_empty(self) -> None:
        # All structure, no prose
        text = "# Header\n\n## Sub\n\n---\n\n```\ncode\n```"
        result = clean_voice_samples(text)
        assert result == ""

    def test_combined_real_world_blog_export(self) -> None:
        # A small composite resembling a blog export
        raw = (
            "---\n"
            "title: My Post\n"
            "date: 2025-04-01\n"
            "---\n"
            "# The Big Idea\n"
            "\n"
            "Today I want to talk about [recovery](https://example.com/recovery), "
            "a topic that **matters deeply** to me.\n"
            "\n"
            "![image of a sunrise](sunrise.jpg)\n"
            "\n"
            "## My Story\n"
            "\n"
            "I lived for quite a while in this house. Things got bad — *really bad* — "
            "before they got better.\n"
            "\n"
            "> Loss, for me, was a motivator.\n"
            "\n"
            "That quote captures it.\n"
        )
        result = clean_voice_samples(raw)
        # Stripped
        assert "title: My Post" not in result
        assert "The Big Idea" not in result
        assert "My Story" not in result
        assert "https://example.com" not in result
        assert "sunrise.jpg" not in result
        assert "**" not in result
        assert "*really bad*" not in result
        assert "> " not in result
        # Kept (with formatting marks removed)
        assert "Today I want to talk about recovery" in result
        assert "matters deeply" in result
        assert "I lived for quite a while in this house" in result
        assert "really bad" in result  # italics stripped, content kept
        assert "Loss, for me, was a motivator." in result
        assert "That quote captures it." in result


# ── redact_voice_samples ─────────────────────────────────────────────────────


class TestRedactVoiceSamples:
    def test_empty_input_returns_empty_success(self) -> None:
        assert redact_voice_samples("") == ("", True)

    def test_llm_failure_returns_cleaned_with_false(self) -> None:
        with patch("findajob.onboarding.voice_processor.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 1
            mock_run.return_value.stdout = ""
            mock_run.return_value.stderr = "rate limited"
            text, ok = redact_voice_samples("Some prose here.")
        assert text == "Some prose here."
        assert ok is False

    def test_llm_empty_output_returns_cleaned_with_false(self) -> None:
        with patch("findajob.onboarding.voice_processor.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = ""
            text, ok = redact_voice_samples("Some prose here.")
        assert text == "Some prose here."
        assert ok is False

    def test_llm_timeout_returns_cleaned_with_false(self) -> None:
        import subprocess

        with patch("findajob.onboarding.voice_processor.subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired(cmd="aichat", timeout=120)
            text, ok = redact_voice_samples("Some prose here.")
        assert text == "Some prose here."
        assert ok is False

    def test_llm_missing_binary_returns_cleaned_with_false(self) -> None:
        with patch("findajob.onboarding.voice_processor.subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError()
            text, ok = redact_voice_samples("Some prose here.")
        assert text == "Some prose here."
        assert ok is False

    def test_llm_success_returns_redacted_with_true(self) -> None:
        with patch("findajob.onboarding.voice_processor.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = "Generalized prose output.\n"
            text, ok = redact_voice_samples("Original prose.")
        assert text == "Generalized prose output."
        assert ok is True


# ── process_voice_samples ────────────────────────────────────────────────────


class TestProcessVoiceSamples:
    def test_redact_disabled_skips_llm_call(self) -> None:
        with patch("findajob.onboarding.voice_processor.subprocess.run") as mock_run:
            text, ok = process_voice_samples("# Header\n\nReal prose here.", redact=False)
            assert mock_run.call_count == 0
        assert "# Header" not in text
        assert "Real prose here." in text
        assert ok is True

    def test_empty_after_cleaning_returns_empty_success(self) -> None:
        # Input is all structural, cleaning empties it, redact never runs
        with patch("findajob.onboarding.voice_processor.subprocess.run") as mock_run:
            text, ok = process_voice_samples("# Just A Header\n\n---\n\n```\ncode\n```")
            assert mock_run.call_count == 0
        assert text == ""
        assert ok is True

    def test_clean_then_redact_pipeline(self) -> None:
        with patch("findajob.onboarding.voice_processor.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = "Generalized output."
            text, ok = process_voice_samples("# Header\n\nReal prose with details.", redact=True)
            assert mock_run.call_count == 1
            # Verify the cleaned (no header) text was sent to LLM, not the raw
            sent_prompt = mock_run.call_args[0][0][-1]
            assert "# Header" not in sent_prompt
            assert "Real prose with details." in sent_prompt
        assert text == "Generalized output."
        assert ok is True

    def test_redact_failure_returns_cleaned_with_false(self) -> None:
        with patch("findajob.onboarding.voice_processor.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 1
            mock_run.return_value.stdout = ""
            text, ok = process_voice_samples("# Header\n\nReal prose here.", redact=True)
        # Header stripped, LLM failed, cleaned text returned
        assert "# Header" not in text
        assert "Real prose here." in text
        assert ok is False
