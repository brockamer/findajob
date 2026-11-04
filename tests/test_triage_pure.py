"""Tests for pure functions in cleaning.py (extracted from triage.py)."""

from findajob.cleaning import (
    clean_company,
    clean_title,
    extract_linkedin_job_id,
    fingerprint,
    normalize,
)

# ── normalize() ──────────────────────────────────────────────────────────────


class TestNormalize:
    def test_lowercases(self):
        assert normalize("Hello World") == "hello world"

    def test_abbreviation_sr(self):
        assert "senior" in normalize("Sr. Engineer")

    def test_abbreviation_eng(self):
        assert "engineer" in normalize("Sr. Eng.")

    def test_abbreviation_dc(self):
        assert "data center" in normalize("DC Ops")

    def test_abbreviation_ops(self):
        assert "operations" in normalize("DC Ops")

    def test_combined_abbreviations(self):
        result = normalize("Sr. Eng. at DC Ops")
        assert "senior" in result
        assert "engineer" in result
        assert "data center" in result
        assert "operations" in result

    def test_non_alphanum_stripped(self):
        assert normalize("Hello, World!") == "hello world"

    def test_whitespace_collapsed(self):
        assert normalize("hello   world") == "hello world"

    def test_empty_string(self):
        assert normalize("") == ""

    def test_only_whitespace(self):
        assert normalize("   ") == ""

    def test_abbreviation_vp(self):
        assert normalize("VP of Ops") == "vice president of operations"

    def test_abbreviation_hw_sw(self):
        result = normalize("HW/SW Engineer")
        assert "hardware" in result
        assert "software" in result
        assert "engineer" in result

    def test_abbreviation_jr(self):
        assert "junior" in normalize("Jr. Developer")

    def test_abbreviation_mgr(self):
        assert "manager" in normalize("Mgr.")

    def test_abbreviation_dir(self):
        assert "director" in normalize("Dir. of Eng.")

    def test_abbreviation_infra(self):
        assert "infrastructure" in normalize("Infra Eng.")

    def test_abbreviation_svp(self):
        assert "senior vice president" in normalize("SVP of Operations")

    def test_abbreviation_tpm(self):
        assert "technical program manager" in normalize("TPM")

    def test_abbreviation_mfg(self):
        assert "manufacturing" in normalize("Mfg Engineer")

    def test_abbreviation_pgm(self):
        assert "program" in normalize("Pgm Manager")


# ── fingerprint() ────────────────────────────────────────────────────────────


class TestFingerprint:
    def test_deterministic(self):
        a = fingerprint("Software Engineer", "Google")
        b = fingerprint("Software Engineer", "Google")
        assert a == b

    def test_different_inputs_differ(self):
        a = fingerprint("Software Engineer", "Google")
        b = fingerprint("Product Manager", "Meta")
        assert a != b

    def test_location_matters(self):
        a = fingerprint("Engineer", "Google", "NYC")
        b = fingerprint("Engineer", "Google", "SF")
        assert a != b

    def test_empty_location_same_as_omitted(self):
        a = fingerprint("Engineer", "Google", "")
        b = fingerprint("Engineer", "Google")
        assert a == b

    def test_returns_hex_string(self):
        fp = fingerprint("Eng", "Co")
        assert isinstance(fp, str)
        assert len(fp) == 16
        assert all(c in "0123456789abcdef" for c in fp)

    def test_case_insensitive(self):
        a = fingerprint("Software Engineer", "Google")
        b = fingerprint("software engineer", "google")
        assert a == b

    def test_abbreviation_normalization(self):
        a = fingerprint("Sr. Eng.", "Google")
        b = fingerprint("Senior Engineer", "Google")
        assert a == b


# ── clean_title() ────────────────────────────────────────────────────────────


class TestCleanTitle:
    def test_strips_days_ago(self):
        assert clean_title("Software Engineer2 days ago") == "Software Engineer"

    def test_strips_salary(self):
        assert clean_title("Engineer$140K - $180K") == "Engineer"

    def test_strips_easy_apply(self):
        assert clean_title("ManagerEasy Apply") == "Manager"

    def test_strips_dot_separator(self):
        result = clean_title("Engineer · Google · San Francisco")
        assert result == "Engineer"

    def test_clean_title_unchanged(self):
        assert clean_title("Software Engineer") == "Software Engineer"

    def test_strips_quick_apply(self):
        assert clean_title("AnalystQuick Apply") == "Analyst"

    def test_strips_actively_recruiting(self):
        assert clean_title("DesignerActively recruiting") == "Designer"

    def test_strips_hours_ago(self):
        assert clean_title("Manager5 hours ago") == "Manager"

    def test_strips_remote_dash(self):
        assert clean_title("Engineer - Remote") == "Engineer"

    def test_strips_hybrid_dash(self):
        assert clean_title("Engineer - Hybrid") == "Engineer"

    def test_strips_via_board(self):
        assert clean_title("Senior Dev Jobs via Dice · More info") == "Senior Dev"

    def test_preserves_internal_content(self):
        assert clean_title("Senior Software Engineer II") == "Senior Software Engineer II"


# ── clean_company() ──────────────────────────────────────────────────────────


class TestCleanCompany:
    def test_strips_dash_location(self):
        assert clean_company("Google – Mountain View, CA") == "Google"

    def test_strips_days_ago(self):
        assert clean_company("Meta3 days ago") == "Meta"

    def test_strips_connections(self):
        assert clean_company("Google12 connections") == "Google"

    def test_empty_string(self):
        assert clean_company("") == ""

    def test_none_input(self):
        assert clean_company(None) == ""

    def test_clean_company_unchanged(self):
        assert clean_company("Anthropic") == "Anthropic"

    def test_strips_dot_separator(self):
        assert clean_company("Google · Sunnyvale") == "Google"

    def test_strips_easy_apply(self):
        assert clean_company("MetaEasy Apply") == "Meta"

    def test_strips_actively_recruiting(self):
        assert clean_company("GoogleActively recruiting") == "Google"

    def test_strips_country_suffix(self):
        # Regex matches ", SingleWordCity, Country" pattern
        assert clean_company("Meta, Sunnyvale, United States") == "Meta"


# ── extract_linkedin_job_id() ────────────────────────────────────────────────


class TestExtractLinkedinJobId:
    def test_standard_url(self):
        url = "https://linkedin.com/jobs/view/1234567890"
        assert extract_linkedin_job_id(url) == "1234567890"

    def test_with_comm_prefix(self):
        url = "https://linkedin.com/comm/jobs/view/1234567890"
        assert extract_linkedin_job_id(url) == "1234567890"

    def test_non_linkedin_url(self):
        assert extract_linkedin_job_id("https://indeed.com/viewjob?id=123") is None

    def test_none_input(self):
        assert extract_linkedin_job_id(None) is None

    def test_empty_string(self):
        assert extract_linkedin_job_id("") is None

    def test_url_with_query_params(self):
        url = "https://www.linkedin.com/jobs/view/9876543210?refId=abc123"
        assert extract_linkedin_job_id(url) == "9876543210"

    def test_www_prefix(self):
        url = "https://www.linkedin.com/jobs/view/5555555555"
        assert extract_linkedin_job_id(url) == "5555555555"

    def test_random_string(self):
        assert extract_linkedin_job_id("not a url at all") is None
