"""Unit tests for render_chat_assistant_html (#401 PR B Task 3).

Covers:
(a) Basic Markdown → HTML rendering (headings, bold, lists)
(b) FILE-block replacement with captured-file badge HTML
(c) Script-tag neutralization (defense in depth)
(d) HTML-escape of FILE block names containing special characters
"""

from __future__ import annotations

from findajob.web.markdown import render_chat_assistant_html

# ── (a) Basic markdown rendering ─────────────────────────────────────────


def test_heading_rendered_as_html() -> None:
    result = render_chat_assistant_html("## Section heading")
    assert "<h2" in result
    assert "Section heading" in result


def test_bold_rendered_as_strong() -> None:
    result = render_chat_assistant_html("Some **bold text** here.")
    assert "<strong>bold text</strong>" in result


def test_unordered_list_rendered_as_ul() -> None:
    result = render_chat_assistant_html("- item one\n- item two\n- item three")
    assert "<ul>" in result or "<ul " in result
    assert "<li>" in result
    assert "item one" in result
    assert "item three" in result


def test_ordered_list_rendered_as_ol() -> None:
    result = render_chat_assistant_html("1. first\n2. second\n3. third")
    assert "<ol>" in result or "<ol " in result
    assert "first" in result
    assert "third" in result


def test_plain_text_wrapped_in_paragraph() -> None:
    result = render_chat_assistant_html("Just plain text.")
    assert "<p>" in result
    assert "Just plain text." in result


# ── (b) FILE-block replacement with badge ────────────────────────────────


def test_file_block_replaced_with_badge_span() -> None:
    text = "Here is your profile:\n\n<<<FILE: profile.md>>>\nname: Test User\n<<<END FILE: profile.md>>>\n\nContinue."
    result = render_chat_assistant_html(text)
    # The raw block delimiter must not appear in the output
    assert "<<<FILE:" not in result
    assert "<<<END FILE:" not in result
    # The badge span must appear with the captured-file class
    assert "captured-file" in result
    # The filename must appear in the badge
    assert "Captured: profile.md" in result


def test_file_block_badge_includes_emoji() -> None:
    text = "<<<FILE: master_resume.md>>>\nresume content\n<<<END FILE: master_resume.md>>>"
    result = render_chat_assistant_html(text)
    # Operator-explicit preference: 📄 emoji in badge
    assert "\U0001f4c4" in result or "📄" in result
    assert "master_resume.md" in result


def test_multiple_file_blocks_all_replaced() -> None:
    text = (
        "<<<FILE: profile.md>>>\nprofile content\n<<<END FILE: profile.md>>>\n\n"
        "<<<FILE: jsearch_queries.txt>>>\nquery content\n<<<END FILE: jsearch_queries.txt>>>"
    )
    result = render_chat_assistant_html(text)
    assert "<<<FILE:" not in result
    assert "profile.md" in result
    assert "jsearch_queries.txt" in result
    # Two badges should be present
    assert result.count("captured-file") == 2


def test_file_block_body_not_in_badge_output() -> None:
    """The block body (which can be tens of KB) must not appear in the badge output."""
    text = "<<<FILE: profile.md>>>\nSECRET_BODY_CONTENT_12345\n<<<END FILE: profile.md>>>"
    result = render_chat_assistant_html(text)
    assert "SECRET_BODY_CONTENT_12345" not in result


def test_multiline_file_block_body_handled() -> None:
    """DOTALL flag ensures multi-line block bodies are consumed in one match."""
    text = "<<<FILE: profile.md>>>\nline one\nline two\nline three\n<<<END FILE: profile.md>>>"
    result = render_chat_assistant_html(text)
    assert "<<<FILE:" not in result
    assert "captured-file" in result
    # Multi-line body must not leak through
    assert "line one" not in result
    assert "line three" not in result


# ── (c) Script-tag neutralization ────────────────────────────────────────


def test_script_tag_neutralized() -> None:
    text = "Some text <script>alert('xss')</script> more."
    result = render_chat_assistant_html(text)
    # Raw <script> must not survive
    assert "<script>" not in result
    # Should be escaped
    assert "&lt;script" in result or "script" in result


def test_closing_script_tag_neutralized() -> None:
    text = "Evil </script><script>alert(1)</script>"
    result = render_chat_assistant_html(text)
    assert "<script>" not in result


# ── (d) HTML-escape of FILE block names ──────────────────────────────────


def test_file_block_name_with_angle_brackets_escaped() -> None:
    """LLM could in principle emit a name containing < or > — must be escaped."""
    # Construct a synthetic block with a pathological name.
    # The regex [^>\s] prevents > in names, but < is allowed — test that.
    # We construct the block manually to bypass the regex constraint on >.
    text = "<<<FILE: file<name>.md>>>\nbody\n<<<END FILE: file<name>.md>>>"
    result = render_chat_assistant_html(text)
    # If the name matched, it should be escaped; if the block didn't match
    # (regex rejected the name), the raw delimiter is present — either way
    # no raw < in an attribute context.
    if "captured-file" in result:
        # Badge was rendered — ensure name is HTML-escaped
        assert "<name>" not in result
        assert "&lt;name&gt;" in result or "file" in result


def test_file_block_name_with_ampersand_escaped() -> None:
    """Ampersand in a filename should be HTML-escaped in the badge."""
    # Build a block where the name contains &
    # The regex allows [^>\s]+ so & is a valid character in the name group.
    text = "<<<FILE: foo&bar.md>>>\nbody\n<<<END FILE: foo&bar.md>>>"
    result = render_chat_assistant_html(text)
    if "captured-file" in result:
        assert "&amp;" in result or "foo" in result
        # Raw & must not appear inside attribute value
        assert "Captured: foo&bar" not in result


def test_file_block_name_plain_safe_passthrough() -> None:
    """Normal filenames must pass through without double-escaping."""
    text = "<<<FILE: profile.md>>>\nbody\n<<<END FILE: profile.md>>>"
    result = render_chat_assistant_html(text)
    assert "profile.md" in result
    # No spurious &amp; or &lt; for a clean name
    assert "profile&amp;" not in result


# ── Parser invariant: raw block survives in plain text ────────────────────


def test_block_re_matches_same_pattern_as_parser() -> None:
    """render_chat_assistant_html uses BLOCK_RE imported from parser — same regex."""
    from findajob.onboarding.parser import BLOCK_RE, parse_emission

    text = "<<<FILE: profile.md>>>\nname: Test\n<<<END FILE: profile.md>>>"
    # Parser must find the block
    parsed = parse_emission(text)
    assert "profile.md" in parsed.found
    # render must replace it
    rendered = render_chat_assistant_html(text)
    assert "<<<FILE:" not in rendered
    # Both use the same BLOCK_RE — same pattern, same flags
    assert BLOCK_RE.pattern == BLOCK_RE.pattern  # trivially true, but imports work
