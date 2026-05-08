"""Tests for pure functions in scripts/utils.py."""

import json

import pytest

from findajob import utils as utils_mod
from findajob.utils import (
    _clean_profile_field,
    build_outreach_filename,
    build_prep_filenames,
    extract_json_payload,
    is_aggregator_company,
    is_ingest_noise_title,
    is_valid_company,
    jd_is_usable,
    load_voice_samples,
    log_event,
    safe_filename_part,
    strip_jd_boilerplate,
)

# ── log_event ──────────────────────────────────────────────────────────────


class TestLogEvent:
    def test_creates_missing_parent_dir(self, tmp_path, monkeypatch):
        log_path = tmp_path / "nonexistent" / "pipeline.jsonl"
        assert not log_path.parent.exists()
        monkeypatch.setattr(utils_mod, "LOG_PATH", str(log_path))

        log_event("fresh_install_smoke", source="test")

        assert log_path.parent.is_dir()
        assert log_path.exists()
        entry = json.loads(log_path.read_text().strip())
        assert entry["event"] == "fresh_install_smoke"
        assert entry["source"] == "test"
        assert "ts" in entry


# ── jd_is_usable ───────────────────────────────────────────────────────────


class TestJdIsUsable:
    def test_none_returns_false(self):
        assert jd_is_usable(None) is False

    def test_empty_string_returns_false(self):
        assert jd_is_usable("") is False

    def test_whitespace_only_returns_false(self):
        assert jd_is_usable("   \n\t  ") is False

    def test_short_text_returns_false(self):
        assert jd_is_usable("Short JD text") is False

    def test_exactly_29_chars_returns_false(self):
        assert jd_is_usable("a" * 29) is False

    def test_exactly_30_chars_returns_true(self):
        assert jd_is_usable("a" * 30) is True

    @pytest.mark.parametrize(
        "wall_signal",
        [
            "You need to enable JavaScript to run this application.",
            "403 Forbidden - Access to this resource is denied",
            "Access Denied. Please log in to continue.",
            "Job not found. This posting may have expired.",
            "This job may have been filled already.",
            "We're signing you in to view this content.",
            "Sign in to see full job description.",
            "Enable JavaScript to run this app properly.",
            "Our careers site has moved to a new URL.",
            "Detected cross-site request forgeries attempt.",
        ],
    )
    def test_wall_signals_return_false(self, wall_signal):
        # Pad to exceed 30-char minimum
        text = wall_signal + " " * max(0, 31 - len(wall_signal))
        assert jd_is_usable(text) is False

    def test_wall_signal_case_insensitive(self):
        assert jd_is_usable("YOU NEED TO ENABLE JAVASCRIPT to view this page content here") is False

    def test_real_jd_returns_true(self):
        jd = (
            "We are looking for a Data Center Operations Manager to oversee "
            "daily operations of our hyperscale facilities. The ideal candidate "
            "has 10+ years of experience managing critical infrastructure, "
            "including power, cooling, and network systems. "
            "Responsibilities include capacity planning, vendor management, "
            "and ensuring 99.999% uptime across all facilities."
        )
        assert jd_is_usable(jd) is True


# ── _clean_profile_field ────────────────────────────────────────────────────


class TestCleanProfileField:
    def test_strips_asterisks(self):
        assert _clean_profile_field("**Jane**") == "Jane"

    def test_strips_backticks(self):
        assert _clean_profile_field("`Jane`") == "Jane"

    def test_strips_whitespace(self):
        assert _clean_profile_field("  Jane  ") == "Jane"

    def test_combined_formatting(self):
        assert _clean_profile_field(" **`Jane`** ") == "Jane"

    def test_none_returns_empty(self):
        assert _clean_profile_field(None) == ""

    def test_empty_string_returns_empty(self):
        assert _clean_profile_field("") == ""

    def test_plain_text_unchanged(self):
        assert _clean_profile_field("Jane Smith") == "Jane Smith"

    def test_inner_formatting_preserved(self):
        # Only outer wrapping is stripped
        assert _clean_profile_field("**First** Last") == "First** Last"


# ── is_aggregator_company ───────────────────────────────────────────────────


class TestIsAggregatorCompany:
    @pytest.mark.parametrize(
        "company",
        [
            "Jobs via Dice",
            "jobs via dice",
            "Job via LinkedIn",
            "Posted via Greenhouse",
            "Robert Half Technology",
            "Staffmark Group",
            "Randstad USA",
            "Adecco Staffing",
            "Manpower Group",
            "Insight Global",
            "Kforce Inc",
            "Dice Engineering",
        ],
    )
    def test_aggregators_return_true(self, company):
        assert is_aggregator_company(company) is True

    @pytest.mark.parametrize(
        "company",
        ["Google", "Meta", "Anthropic", "Small Startup LLC"],
    )
    def test_real_companies_return_false(self, company):
        assert is_aggregator_company(company) is False

    def test_empty_string_returns_false(self):
        assert is_aggregator_company("") is False

    def test_none_returns_false(self):
        assert is_aggregator_company(None) is False

    def test_whitespace_company_returns_false(self):
        assert is_aggregator_company("   ") is False


# ── is_valid_company ────────────────────────────────────────────────────────


class TestIsValidCompany:
    def test_empty_string_is_invalid(self):
        assert is_valid_company("") is False

    def test_none_is_invalid(self):
        assert is_valid_company(None) is False

    def test_whitespace_is_invalid(self):
        assert is_valid_company("   ") is False

    def test_aggregator_is_invalid(self):
        assert is_valid_company("Jobs via Dice") is False

    def test_real_company_is_valid(self):
        assert is_valid_company("Google") is True

    def test_another_real_company(self):
        assert is_valid_company("Acme Corp") is True


# ── is_ingest_noise_title ───────────────────────────────────────────────────


class TestIsIngestNoiseTitle:
    def test_jobs_similar_prefix(self):
        assert is_ingest_noise_title("Jobs similar to Software Engineer") is True

    def test_job_similar_to_exact(self):
        assert is_ingest_noise_title("job similar to") is True

    def test_case_insensitive(self):
        assert is_ingest_noise_title("JOBS SIMILAR to Data Analyst") is True

    def test_real_title_returns_false(self):
        assert is_ingest_noise_title("Software Engineer") is False

    def test_another_real_title(self):
        assert is_ingest_noise_title("Data Center Manager") is False

    def test_empty_string_returns_false(self):
        assert is_ingest_noise_title("") is False

    def test_none_returns_false(self):
        assert is_ingest_noise_title(None) is False


# ── safe_filename_part ──────────────────────────────────────────────────────


class TestSafeFilenamePart:
    def test_slash_removed(self):
        assert safe_filename_part("Google/Alphabet") == "GoogleAlphabet"

    def test_keeps_allowed_chars(self):
        result = safe_filename_part("Acme & Co., Inc.")
        # Keeps &, comma, period (except trailing period gets stripped)
        assert result == "Acme & Co., Inc"

    def test_truncation(self):
        long_string = "A" * 100
        assert len(safe_filename_part(long_string, max_len=80)) <= 80

    def test_custom_max_len(self):
        long_string = "A" * 50
        assert len(safe_filename_part(long_string, max_len=20)) <= 20

    def test_trailing_period_stripped(self):
        assert safe_filename_part("Google Inc.") == "Google Inc"

    def test_trailing_comma_stripped(self):
        assert safe_filename_part("Google,") == "Google"

    def test_trailing_dash_stripped(self):
        assert safe_filename_part("Google-") == "Google"

    def test_whitespace_collapsed(self):
        assert safe_filename_part("Google   Cloud   Platform") == "Google Cloud Platform"

    def test_none_returns_empty(self):
        assert safe_filename_part(None) == ""

    def test_empty_returns_empty(self):
        assert safe_filename_part("") == ""

    def test_special_chars_removed(self):
        # Parens, brackets, colons should be removed
        assert safe_filename_part("Title (Remote) [NYC]: Senior") == "Title Remote NYC Senior"

    def test_hyphen_preserved(self):
        assert safe_filename_part("Full-Stack Engineer") == "Full-Stack Engineer"

    def test_ampersand_preserved(self):
        assert safe_filename_part("R&D Manager") == "R&D Manager"


# ── build_prep_filenames ────────────────────────────────────────────────────


class TestBuildPrepFilenames:
    def setup_method(self):
        self.result = build_prep_filenames(
            company="Google",
            title="Data Center Ops Manager",
            timestamp_fn="20260412-143000",
            file_prefix="TestUser",
        )

    def test_returns_exactly_10_keys(self):
        expected_keys = {
            "resume_md",
            "resume_docx",
            "cover_md",
            "cover_docx",
            "briefing_md",
            "briefing_docx",
            "changes_md",
            "critique_md",
            "jd_txt",
            "checklist_md",
        }
        assert set(self.result.keys()) == expected_keys

    def test_critique_md_format(self):
        assert self.result["critique_md"] == (
            "TestUser Critique - Google - Data Center Ops Manager - 20260412-143000.md"
        )

    def test_resume_md_format(self):
        assert self.result["resume_md"] == "TestUser Resume - Google - Data Center Ops Manager - 20260412-143000.md"

    def test_resume_docx_format(self):
        assert self.result["resume_docx"] == "TestUser Resume - Google - Data Center Ops Manager - 20260412-143000.docx"

    def test_cover_md_format(self):
        assert self.result["cover_md"] == "TestUser Cover - Google - Data Center Ops Manager - 20260412-143000.md"

    def test_cover_docx_format(self):
        assert self.result["cover_docx"] == "TestUser Cover - Google - Data Center Ops Manager - 20260412-143000.docx"

    def test_briefing_md_format(self):
        assert self.result["briefing_md"] == "TestUser Briefing - Google - Data Center Ops Manager - 20260412-143000.md"

    def test_changes_md_format(self):
        assert self.result["changes_md"] == (
            "TestUser Resume Changes - Google - Data Center Ops Manager - 20260412-143000.md"
        )

    def test_jd_txt_has_no_prefix_or_timestamp(self):
        assert self.result["jd_txt"] == "JD - Google - Data Center Ops Manager.txt"

    def test_checklist_md_has_no_prefix_or_timestamp(self):
        assert self.result["checklist_md"] == "Review Checklist - Google - Data Center Ops Manager.md"

    def test_company_sanitized(self):
        result = build_prep_filenames("Google/Alphabet", "SWE", "20260101-000000", "X")
        assert "/" not in result["resume_md"]

    def test_company_truncated_to_40(self):
        long_co = "A" * 80
        result = build_prep_filenames(long_co, "Title", "20260101-000000", "X")
        # The company portion should be truncated
        assert long_co not in result["resume_md"]


# ── build_outreach_filename ─────────────────────────────────────────────────


class TestBuildOutreachFilename:
    def test_basic_format(self):
        result = build_outreach_filename(
            contact_name="Jane Smith",
            company="Google",
            timestamp_fn="20260412-143000",
            file_prefix="TestUser",
        )
        assert result == "TestUser Outreach to Jane Smith - Google - 20260412-143000.txt"

    def test_special_chars_in_contact_sanitized(self):
        result = build_outreach_filename("Jane (Sr.)", "Google", "20260412-143000", "TestUser")
        assert "(" not in result
        assert ")" not in result

    def test_special_chars_in_company_sanitized(self):
        result = build_outreach_filename("Jane", "Google/Alphabet", "20260412-143000", "TestUser")
        assert "/" not in result


# ── strip_jd_boilerplate ───────────────────────────────────────────────────


class TestStripJdBoilerplate:
    def _make_jd(self, body_paragraphs, boilerplate_paragraphs):
        """Helper: join body + boilerplate with double newlines."""
        return "\n\n".join(body_paragraphs + boilerplate_paragraphs)

    def test_none_returns_empty(self):
        assert strip_jd_boilerplate(None) == ""

    def test_empty_returns_empty(self):
        assert strip_jd_boilerplate("") == ""

    def test_short_text_passthrough(self):
        short = "This is a short JD under 200 chars."
        assert strip_jd_boilerplate(short) == short

    def test_single_paragraph_passthrough(self):
        text = "x" * 300  # long but no paragraph breaks
        assert strip_jd_boilerplate(text) == text

    def test_strips_trailing_eeo_paragraph(self):
        body = "We are looking for a senior engineer to join our team. " * 8
        boilerplate = "We are an equal opportunity employer and do not discriminate on any basis."
        text = body.strip() + "\n\n" + boilerplate
        result = strip_jd_boilerplate(text)
        assert "equal opportunity employer" not in result
        assert "senior engineer" in result

    def test_strips_multiple_trailing_boilerplate(self):
        body = "Responsibilities include managing data center operations and infrastructure. " * 6
        bp1 = "We are an equal opportunity employer."
        bp2 = "All qualified applicants will receive consideration for employment."
        bp3 = "Reasonable accommodation available upon request."
        text = body.strip() + "\n\n" + bp1 + "\n\n" + bp2 + "\n\n" + bp3
        result = strip_jd_boilerplate(text)
        assert "equal opportunity" not in result
        assert "qualified applicants" not in result
        assert "Reasonable accommodation" not in result
        assert "data center operations" in result

    def test_stops_at_non_boilerplate_paragraph(self):
        p1 = "Requirements: 10+ years of experience in infrastructure operations."
        p2 = "Nice to have: experience with GPU clusters and AI workloads."
        p3 = "We are an equal opportunity employer."
        text = (p1 + " ") * 3 + "\n\n" + p2 + "\n\n" + p3
        result = strip_jd_boilerplate(text)
        assert "Nice to have" in result
        assert "equal opportunity" not in result

    def test_never_removes_more_than_40_percent(self):
        # Craft text where boilerplate is >40% of content
        body = "Job description. " * 5  # short body
        boilerplate = ("We are an equal opportunity employer and do not discriminate. " * 10).strip()
        text = body.strip() + "\n\n" + boilerplate
        result = strip_jd_boilerplate(text)
        # Safety check triggered, returns original
        assert len(result) >= len(text) * 0.6

    def test_never_drops_below_200_chars(self):
        # Body is ~210 chars, boilerplate is ~60 chars
        body = "A" * 210
        boilerplate = "We are an equal opportunity employer."
        text = body + "\n\n" + boilerplate
        result = strip_jd_boilerplate(text)
        assert len(result) >= 200

    def test_non_boilerplate_jd_unchanged(self):
        p1 = "We are hiring a senior data center technician."
        p2 = "Responsibilities include rack and stack, cabling, and power management."
        p3 = "Requirements: 5+ years hands-on DC experience, DCIM tools, strong troubleshooting."
        text = (p1 + " " + p1 + " " + p1) + "\n\n" + (p2 + " " + p2) + "\n\n" + (p3 + " " + p3)
        result = strip_jd_boilerplate(text)
        assert result == text

    def test_drug_free_workplace_stripped(self):
        body = "Looking for a facilities manager to oversee operations. " * 8
        boilerplate = "We maintain a drug-free workplace policy for all employees."
        text = body.strip() + "\n\n" + boilerplate
        result = strip_jd_boilerplate(text)
        assert "drug-free workplace" not in result

    def test_how_to_apply_stripped(self):
        body = "Join our infrastructure team to build next-gen data centers. " * 8
        boilerplate = "How to apply: submit your resume through our careers portal."
        text = body.strip() + "\n\n" + boilerplate
        result = strip_jd_boilerplate(text)
        assert "How to apply" not in result

    def test_benefits_header_stripped(self):
        body = "We need an ops manager for our cloud infrastructure team. " * 8
        boilerplate = "Benefits: health, dental, vision, 401k match, unlimited PTO."
        text = body.strip() + "\n\n" + boilerplate
        result = strip_jd_boilerplate(text)
        assert "Benefits:" not in result


# ── load_voice_samples ─────────────────────────────────────────────────────


class TestLoadVoiceSamples:
    def test_missing_dir_returns_empty(self, tmp_path):
        missing = tmp_path / "nope"
        assert load_voice_samples(samples_dir=str(missing)) == ""

    def test_empty_dir_returns_empty(self, tmp_path):
        assert load_voice_samples(samples_dir=str(tmp_path)) == ""

    def test_only_readme_returns_empty(self, tmp_path):
        (tmp_path / "README.md").write_text("# voice samples\nThis is just docs.")
        (tmp_path / "readme.txt").write_text("more docs")
        assert load_voice_samples(samples_dir=str(tmp_path)) == ""

    def test_single_md_file(self, tmp_path):
        (tmp_path / "voice-samples.md").write_text("My one true voice.")
        assert load_voice_samples(samples_dir=str(tmp_path)) == "My one true voice."

    def test_single_txt_file(self, tmp_path):
        (tmp_path / "essay.txt").write_text("Plain text voice.")
        assert load_voice_samples(samples_dir=str(tmp_path)) == "Plain text voice."

    def test_multiple_files_concatenated_alphabetically(self, tmp_path):
        (tmp_path / "b.md").write_text("Second.")
        (tmp_path / "a.md").write_text("First.")
        (tmp_path / "c.txt").write_text("Third.")
        assert load_voice_samples(samples_dir=str(tmp_path)) == "First.\n\nSecond.\n\nThird."

    def test_readme_excluded_from_concatenation(self, tmp_path):
        (tmp_path / "README.md").write_text("Skip me.")
        (tmp_path / "voice-samples.md").write_text("Real voice.")
        assert load_voice_samples(samples_dir=str(tmp_path)) == "Real voice."

    def test_non_md_non_txt_ignored(self, tmp_path):
        (tmp_path / "voice-samples.md").write_text("Real voice.")
        (tmp_path / "image.png").write_text("binary garbage")
        (tmp_path / "data.json").write_text('{"x": 1}')
        assert load_voice_samples(samples_dir=str(tmp_path)) == "Real voice."

    def test_empty_files_filtered(self, tmp_path):
        (tmp_path / "a.md").write_text("")
        (tmp_path / "b.md").write_text("   \n\n  ")
        (tmp_path / "c.md").write_text("Real content.")
        assert load_voice_samples(samples_dir=str(tmp_path)) == "Real content."

    def test_max_chars_caps_output(self, tmp_path):
        (tmp_path / "a.md").write_text("x" * 50000)
        out = load_voice_samples(samples_dir=str(tmp_path), max_chars=100)
        assert len(out) == 100
        assert out == "x" * 100

    def test_max_chars_default_is_32k(self, tmp_path):
        (tmp_path / "a.md").write_text("y" * 50000)
        out = load_voice_samples(samples_dir=str(tmp_path))
        assert len(out) == 32000

    def test_under_cap_unchanged(self, tmp_path):
        (tmp_path / "a.md").write_text("short content")
        assert load_voice_samples(samples_dir=str(tmp_path), max_chars=1000) == "short content"


# ── extract_json_payload ───────────────────────────────────────────────────


class TestExtractJsonPayload:
    """Recovery shapes for LLM scorer responses (#278). The scorer prompt
    asks for JSON only; reality is sometimes prose, sometimes fenced,
    sometimes both. The extractor handles each known shape so the parser
    that runs after it sees clean JSON.
    """

    def test_plain_json_unchanged(self):
        text = '{"relevance_score": 7}'
        assert extract_json_payload(text) == text

    def test_strips_whole_response_fence_with_json_lang(self):
        text = '```json\n{"relevance_score": 7}\n```'
        assert extract_json_payload(text) == '{"relevance_score": 7}'

    def test_strips_whole_response_fence_no_lang(self):
        text = '```\n{"relevance_score": 7}\n```'
        assert extract_json_payload(text) == '{"relevance_score": 7}'

    def test_extracts_fenced_json_block_inside_prose(self):
        text = (
            "Looking at this role, here's the analysis:\n\n"
            "```json\n"
            '{"relevance_score": 5}\n'
            "```\n\n"
            "Notes: the fit is moderate."
        )
        assert json.loads(extract_json_payload(text)) == {"relevance_score": 5}

    def test_extracts_bare_json_after_prose(self):
        # The exact failure mode reported in #278: prose at lines 1–2,
        # then JSON starting on line 3. Original parser saw "Looking..."
        # on char 0 and bombed at "char 50".
        text = (
            'Looking at this job posting,\n\nthe scoring output is:\n{"relevance_score": 6, "interview_likelihood": 4}'
        )
        assert json.loads(extract_json_payload(text)) == {
            "relevance_score": 6,
            "interview_likelihood": 4,
        }

    def test_extracts_bare_json_array_after_prose(self):
        text = "Here are the results:\n[1, 2, 3]"
        assert json.loads(extract_json_payload(text)) == [1, 2, 3]

    def test_falls_through_to_input_when_no_json_present(self):
        # Pure prose — extractor returns the input unchanged so the
        # downstream parser surfaces a meaningful JSONDecodeError.
        text = "I cannot evaluate this role without more information."
        assert extract_json_payload(text) == text

    def test_handles_leading_whitespace(self):
        text = '   \n  {"relevance_score": 7}'
        assert json.loads(extract_json_payload(text)) == {"relevance_score": 7}


# ── is_synthetic_job ───────────────────────────────────────────────────────


def test_is_synthetic_job_true_for_flag_one():
    from findajob.utils import is_synthetic_job

    assert is_synthetic_job({"synthetic": 1}) is True


def test_is_synthetic_job_false_for_flag_zero():
    from findajob.utils import is_synthetic_job

    assert is_synthetic_job({"synthetic": 0}) is False


def test_is_synthetic_job_false_when_key_missing():
    from findajob.utils import is_synthetic_job

    # Legacy / partial dicts default to non-synthetic.
    assert is_synthetic_job({}) is False


def test_is_synthetic_job_truthy_string_treated_as_true():
    from findajob.utils import is_synthetic_job

    # SQLite returns 1/0 as int but be defensive against driver quirks.
    assert is_synthetic_job({"synthetic": "1"}) is True
