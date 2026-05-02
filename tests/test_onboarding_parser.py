"""Unit tests for the onboarding emission parser (#148)."""

from __future__ import annotations

from findajob.onboarding.parser import ALLOWED_FILENAMES, OPTIONAL_FILENAMES, parse_emission


def _wrap(name: str, body: str) -> str:
    return f"<<<FILE: {name}>>>\n{body}\n<<<END FILE: {name}>>>"


_CLEAN_BLOCKS = {
    "profile.md": "# Profile\nalice\n",
    "master_resume.md": "# Resume\n## Contact\nalice\n",
    "target_companies.md": "## Tier 1 — Active Focus\n- Acme\n- Example Corp\n",
    "business_sector_employers_reference.md": "## Categories\n### Foo\n",
    "jsearch_queries.txt": "senior backend engineer\n",
    "prefilter_rules.yaml": "hard_rejects:\n  spam:\n    - '\\bspam\\b'\n",
    "in_domain_patterns.yaml": "positive:\n  - '\\bbackend\\s+engineer\\b'\n",
    "display_name.txt": "Test Operator",
    "timezone.txt": "America/Los_Angeles",
    "ntfy_topic.txt": "tester-jobsearch-2026-17",
}


def _clean_emission() -> str:
    return "\n\n".join(_wrap(n, b) for n, b in _CLEAN_BLOCKS.items())


def test_allowed_filenames_are_exactly_nine() -> None:
    """#283: jsearch_queries.txt moved to OPTIONAL (was 10, now 9 ALLOWED).

    Pre-#283: profile.md, master_resume.md, target_companies.md,
    business_sector_employers_reference.md, jsearch_queries.txt,
    prefilter_rules.yaml, in_domain_patterns.yaml, display_name.txt,
    timezone.txt, ntfy_topic.txt.
    """
    assert len(ALLOWED_FILENAMES) == 9
    assert "jsearch_queries.txt" not in ALLOWED_FILENAMES
    assert "jsearch_queries.txt" in OPTIONAL_FILENAMES


def test_clean_emission_all_required_found() -> None:
    result = parse_emission(_clean_emission())
    assert set(result.found) == set(_CLEAN_BLOCKS)
    assert result.missing == []
    assert result.unknown == []
    for name, body in _CLEAN_BLOCKS.items():
        assert result.found[name] == body


def test_embedded_in_transcript_is_still_parsed() -> None:
    blob = (
        "User: paste the emission please\n"
        "Assistant: Here we go.\n\n" + _clean_emission() + "\n\nReply **next** to continue.\n"
    )
    result = parse_emission(blob)
    assert set(result.found) == set(_CLEAN_BLOCKS)
    assert result.missing == []


def test_missing_block_is_reported() -> None:
    partial_blocks = {k: v for k, v in _CLEAN_BLOCKS.items() if k != "in_domain_patterns.yaml"}
    blob = "\n\n".join(_wrap(n, b) for n, b in partial_blocks.items())
    result = parse_emission(blob)
    assert "in_domain_patterns.yaml" in result.missing
    assert len(result.found) == len(_CLEAN_BLOCKS) - 1


def test_duplicate_last_wins() -> None:
    blob = (
        _wrap("profile.md", "first draft\n")
        + "\n\n"
        + "\n\n".join(_wrap(n, b) for n, b in _CLEAN_BLOCKS.items() if n != "profile.md")
        + "\n\n"
        + _wrap("profile.md", "second draft\n")
    )
    result = parse_emission(blob)
    assert result.found["profile.md"] == "second draft\n"
    assert result.missing == []


def test_unknown_filename_goes_to_unknown() -> None:
    blob = _clean_emission() + "\n\n" + _wrap("secrets.env", "API_KEY=...\n")
    result = parse_emission(blob)
    assert "secrets.env" in result.unknown
    assert "secrets.env" not in result.found
    assert set(result.found) == set(_CLEAN_BLOCKS)


def test_code_fence_trailing_newline_after_close_is_stripped() -> None:
    # Common LLM output: closing fence followed by a newline
    fenced = "```markdown\ncontent\n``` \n"
    blob = (
        _wrap("profile.md", fenced)
        + "\n\n"
        + "\n\n".join(_wrap(n, b) for n, b in _CLEAN_BLOCKS.items() if n != "profile.md")
    )
    result = parse_emission(blob)
    assert result.found["profile.md"] == "content\n"


def test_code_fence_no_trailing_newline_after_close_is_stripped() -> None:
    # Closing fence at end of string with no trailing newline (\Z case)
    fenced = "```markdown\ncontent\n```"
    blob = (
        _wrap("profile.md", fenced)
        + "\n\n"
        + "\n\n".join(_wrap(n, b) for n, b in _CLEAN_BLOCKS.items() if n != "profile.md")
    )
    result = parse_emission(blob)
    assert result.found["profile.md"] == "content\n"


def test_no_fences_content_passes_through_unchanged() -> None:
    # Body with mid-content backticks (inline code, not a fence) must be byte-for-byte identical
    body = "Some text with `inline` backticks\n"
    blob = (
        _wrap("profile.md", body)
        + "\n\n"
        + "\n\n".join(_wrap(n, b) for n, b in _CLEAN_BLOCKS.items() if n != "profile.md")
    )
    result = parse_emission(blob)
    assert result.found["profile.md"] == body


def test_crlf_line_endings_parse() -> None:
    blob = _clean_emission().replace("\n", "\r\n")
    result = parse_emission(blob)
    assert set(result.found) == set(_CLEAN_BLOCKS)
    assert result.missing == []


def test_dangling_open_delimiter_is_missing() -> None:
    partial = _wrap("profile.md", "ok\n")
    # Dangling open for master_resume with no close
    dangling = "<<<FILE: master_resume.md>>>\nstarted but not finished\n"
    blob = (
        partial
        + "\n\n"
        + dangling
        + "\n\n"
        + "\n\n".join(_wrap(n, b) for n, b in _CLEAN_BLOCKS.items() if n not in ("profile.md", "master_resume.md"))
    )
    result = parse_emission(blob)
    assert "master_resume.md" in result.missing
    assert "profile.md" in result.found


def test_blank_input_returns_all_missing() -> None:
    result = parse_emission("")
    assert result.found == {}
    # missing must be exactly the ALLOWED set (jsearch_queries.txt is OPTIONAL → not missing)
    assert set(result.missing) == set(ALLOWED_FILENAMES)
    assert result.unknown == []


# ── OPTIONAL_FILENAMES (#262 voice samples) ─────────────────────────────────


def test_optional_filenames_includes_voice_samples() -> None:
    assert "voice-samples.md" in OPTIONAL_FILENAMES


def test_voice_samples_absent_does_not_appear_in_missing() -> None:
    """Optional files never trigger a missing-required failure."""
    result = parse_emission(_clean_emission())
    assert result.missing == []
    assert "voice-samples.md" not in result.missing
    assert "voice-samples.md" not in result.found


def test_voice_samples_present_appears_in_found() -> None:
    """When the user provides voice samples, parser puts them in found."""
    body = "I lived for quite a while in this house. Real prose follows."
    blob = _clean_emission() + "\n\n" + _wrap("voice-samples.md", body)
    result = parse_emission(blob)
    assert "voice-samples.md" in result.found
    assert result.found["voice-samples.md"] == body
    assert result.missing == []


def test_voice_samples_does_not_pollute_unknown() -> None:
    """voice-samples.md is recognized, so it's not 'unknown'."""
    body = "Some prose here."
    blob = _clean_emission() + "\n\n" + _wrap("voice-samples.md", body)
    result = parse_emission(blob)
    assert "voice-samples.md" not in result.unknown


def test_truly_unknown_file_still_lands_in_unknown() -> None:
    """Sanity: an actual unknown filename still goes to unknown."""
    blob = _clean_emission() + "\n\n" + _wrap("not-a-real-file.txt", "garbage")
    result = parse_emission(blob)
    assert "not-a-real-file.txt" in result.unknown


# ── #283 new OPTIONAL filenames ──────────────────────────────────────────────


def test_jsearch_queries_now_optional_not_in_missing_when_absent() -> None:
    """#283: jsearch_queries.txt moved ALLOWED → OPTIONAL; absence is no longer a 'missing'."""
    partial_blocks = {k: v for k, v in _CLEAN_BLOCKS.items() if k != "jsearch_queries.txt"}
    blob = "\n\n".join(_wrap(n, b) for n, b in partial_blocks.items())
    result = parse_emission(blob)
    assert "jsearch_queries.txt" not in result.missing
    assert result.missing == []  # all 9 remaining ALLOWED present


def test_feed_urls_txt_recognized_as_optional() -> None:
    """#283: feed-urls.txt is a new OPTIONAL filename."""
    blocks = dict(_CLEAN_BLOCKS)
    blocks["feed-urls.txt"] = "https://boards.greenhouse.io/acme\nhttps://jobs.lever.co/example\n"
    blob = "\n\n".join(_wrap(n, b) for n, b in blocks.items())
    result = parse_emission(blob)
    assert "feed-urls.txt" in result.found
    assert result.found["feed-urls.txt"] == "https://boards.greenhouse.io/acme\nhttps://jobs.lever.co/example\n"
    assert result.unknown == []


def test_linkedin_alerts_md_recognized_as_optional() -> None:
    """#283: linkedin-alerts.md is a new OPTIONAL filename."""
    blocks = dict(_CLEAN_BLOCKS)
    blocks["linkedin-alerts.md"] = "# LinkedIn alerts\n- [ ] Step 1\n"
    blob = "\n\n".join(_wrap(n, b) for n, b in blocks.items())
    result = parse_emission(blob)
    assert "linkedin-alerts.md" in result.found
    assert "linkedin-alerts.md" not in result.unknown


def test_all_three_new_optionals_together() -> None:
    """#283: jsearch_queries.txt + feed-urls.txt + linkedin-alerts.md present together — none flagged unknown."""
    blocks = dict(_CLEAN_BLOCKS)
    blocks["feed-urls.txt"] = "https://boards.greenhouse.io/acme\n"
    blocks["linkedin-alerts.md"] = "# LinkedIn alerts\n"
    blob = "\n\n".join(_wrap(n, b) for n, b in blocks.items())
    result = parse_emission(blob)
    assert {"jsearch_queries.txt", "feed-urls.txt", "linkedin-alerts.md"} <= set(result.found.keys())
    assert result.unknown == []


def test_rapidapi_feed_txt_recognized_as_optional() -> None:
    """#408: rapidapi_feed.txt is a new OPTIONAL filename emitted by Section 3h."""
    blocks = dict(_CLEAN_BLOCKS)
    blocks["rapidapi_feed.txt"] = "jsearch\n"
    blob = "\n\n".join(_wrap(n, b) for n, b in blocks.items())
    result = parse_emission(blob)
    assert "rapidapi_feed.txt" in result.found
    assert result.found["rapidapi_feed.txt"].strip() == "jsearch"
    assert result.unknown == []
