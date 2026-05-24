"""Tests for scorer_prefilter.py — deterministic pre-filter logic."""

import pytest

from findajob import config_loader
from findajob.scorer_prefilter import (
    _excluded_employer_match,
    _hard_reject_match,
    _in_domain_match,
    prefilter_score,
)

# ── _hard_reject_match ────────────────────────────────────────────────────────


class TestHardRejectMatch:
    """Stage 1: title regex hard reject, with DC context override."""

    @pytest.mark.parametrize(
        "title",
        [
            "Software Engineer",
            "Senior Software Engineer",
            "Software Developer",
            "Software Architect",
            "SWE II",
            "SDE III",
        ],
    )
    def test_software_engineering(self, title):
        assert _hard_reject_match(title) is not None

    @pytest.mark.parametrize(
        "title",
        [
            "Registered Nurse",
            "Nursing Manager",
            "Clinical Manager",
            "Patient Care Coordinator",
            "Pharmaceutical Sales Rep",
        ],
    )
    def test_healthcare(self, title):
        assert _hard_reject_match(title) is not None

    @pytest.mark.parametrize(
        "title",
        [
            "Sales Manager",
            "Account Executive",
            "Enterprise Sales Leader",
            "Sales Representative",
            "Field Sales Associate",
            "Business Development Manager",
        ],
    )
    def test_sales(self, title):
        assert _hard_reject_match(title) is not None

    @pytest.mark.parametrize(
        "title",
        [
            "Network Engineer",
            "Network Architect",
            "NOC Engineer",
            "Connectivity Engineer",
        ],
    )
    def test_networking(self, title):
        assert _hard_reject_match(title) is not None

    @pytest.mark.parametrize(
        "title",
        [
            "Supply Chain Analyst",
            "Supply Chain Manager",
            "Procurement Manager",
            "Logistics Manager",
            "Warehouse Manager",
            "Inventory Manager",
        ],
    )
    def test_supply_chain(self, title):
        assert _hard_reject_match(title) is not None

    @pytest.mark.parametrize(
        "title",
        [
            "Security Analyst",
            "SOC Analyst",
            "Cybersecurity Engineer",
            "Information Security Manager",
            "Threat Detection Specialist",
        ],
    )
    def test_security(self, title):
        assert _hard_reject_match(title) is not None

    @pytest.mark.parametrize(
        "title",
        [
            "Financial Analyst",
            "Compliance Officer",
            "Legal Counsel",
            "Human Resources Manager",
            "Recruiter",
            "Marketing Manager",
            "Talent Acquisition Specialist",
        ],
    )
    def test_finance_legal_hr(self, title):
        assert _hard_reject_match(title) is not None

    @pytest.mark.parametrize(
        "title",
        [
            "Electrical Engineer",
            "Mechanical Engineering Lead",
            "Firmware Engineer",
            "Controls Engineer",
            "Hardware Design Engineer",
        ],
    )
    def test_hardware_design(self, title):
        assert _hard_reject_match(title) is not None

    @pytest.mark.parametrize(
        "title",
        [
            "Construction Manager",
            "MEP Superintendent",
            "Site Superintendent",
            "General Superintendent",
        ],
    )
    def test_construction(self, title):
        assert _hard_reject_match(title) is not None

    @pytest.mark.parametrize(
        "title",
        [
            "Manufacturing Engineer",
            "Plant Manager",
            "Production Planner",
            "Quality Engineer",
            "Process Engineer",
        ],
    )
    def test_manufacturing_quality(self, title):
        assert _hard_reject_match(title) is not None

    @pytest.mark.parametrize(
        "title",
        [
            "Systems Administrator",
            "Storage Engineer",
            "Site Reliability Engineer",
            "DevOps Engineer",
            "Data Engineer",
            "Kernel Engineer",
        ],
    )
    def test_sysadmin_sre_devops(self, title):
        assert _hard_reject_match(title) is not None

    @pytest.mark.parametrize(
        "title",
        [
            "General Manager",
            "Maintenance Technician",
            "Custodial Supervisor",
            "Building Manager",
            "Office Manager",
            "Workplace Manager",
        ],
    )
    def test_facilities_general(self, title):
        assert _hard_reject_match(title) is not None

    def test_dc_context_override_suppresses_reject(self):
        """DC context in title suppresses hard reject -- job may be in-domain."""
        assert _hard_reject_match("Software Engineer - Data Center Operations") is None
        assert _hard_reject_match("Network Engineer, Datacenter") is None
        assert _hard_reject_match("Maintenance Technician - Data Center") is None
        assert _hard_reject_match("General Manager - DC Operations") is None

    @pytest.mark.parametrize(
        "title",
        [
            "Data Center Operations Manager",
            "NPI Program Manager",
            "Infrastructure Operations Director",
            "Lab Operations Lead",
            "Product Manager",
            "Chief of Staff",
            "Operations Manager",
        ],
    )
    def test_negative_not_hard_reject(self, title):
        assert _hard_reject_match(title) is None

    def test_empty_string(self):
        assert _hard_reject_match("") is None

    def test_returns_matched_text(self):
        """Return value is the matched string, not just truthy."""
        result = _hard_reject_match("Senior Software Engineer II")
        assert isinstance(result, str)
        assert "software engineer" in result.lower()


# ── _in_domain_match ──────────────────────────────────────────────────────────


class TestInDomainMatch:
    """Stage 2: in-domain title detection with poison word suppression."""

    @pytest.mark.parametrize(
        "title",
        [
            "Data Center Operations Manager",
            "Datacenter Operations Manager",
            "DC Ops Manager",
            "DC Operations Lead",
            "NPI Program Manager",
            "NPI Manager",
            "Hardware Operations Manager",
            "Hardware Bring-Up Engineer",
            "Hardware NPI Lead",
            "Infrastructure Operations Manager",
            "Infrastructure Operations Director",
            "Lab Operations Manager",
            "Lab Operations Lead",
            "Site Operations Manager",
            "Engineering Operations Manager",
            "Field Operations Manager",
            "Operational Readiness Lead",
            "Data Center Site Manager",
            "Data Center Engineer",
            "Data Center Technician",
        ],
    )
    def test_positive(self, title):
        assert _in_domain_match(title) is True

    def test_poison_workplace_services(self):
        """Poison words suppress an otherwise in-domain match."""
        assert _in_domain_match("Site Operations Manager - Workplace Services") is False

    def test_poison_custodial(self):
        assert _in_domain_match("Data Center Operations Manager - Custodial") is False

    def test_poison_janitorial(self):
        assert _in_domain_match("DC Ops - Janitorial") is False

    def test_poison_facilities_only(self):
        assert _in_domain_match("Site Operations Manager (Facilities Only)") is False

    @pytest.mark.parametrize(
        "title",
        [
            "Marketing Manager",
            "Product Designer",
            "Software Engineer",
            "Sales Representative",
            "Financial Analyst",
            "Operations Manager",  # no DC qualifier
        ],
    )
    def test_negative(self, title):
        assert _in_domain_match(title) is False

    def test_empty_string(self):
        assert _in_domain_match("") is False


# ── prefilter_score ───────────────────────────────────────────────────────────


class TestPrefilterScore:
    """Integration tests for the public prefilter_score API."""

    def test_stage1_hard_reject(self):
        """Software engineer title -> score 1 hard reject."""
        result, reason = prefilter_score("Software Engineer", "Google", True)
        assert result is not None
        assert result["relevance_score"] == 1
        assert result["score_status"] == "scored"
        assert "hard reject" in reason.lower()

    def test_stage1_dc_override_falls_through(self):
        """DC context in a hard-reject title -> falls through to LLM."""
        result, reason = prefilter_score("Software Engineer - Data Center Ops", "Google", True)
        assert result is None
        assert reason is None

    def test_stage2_in_domain_no_jd(self):
        """In-domain title, no JD -> score 5."""
        result, reason = prefilter_score("Data Center Operations Manager", "Acme Corp", jd_usable=False)
        assert result is not None
        assert result["relevance_score"] == 5
        assert result["interview_likelihood"] == 4
        assert "Tier 1" not in reason

    def test_stage2_skipped_when_jd_usable(self):
        """In-domain title with usable JD -> falls through to LLM."""
        result, reason = prefilter_score("Data Center Operations Manager", "Google", jd_usable=True)
        assert result is None
        assert reason is None

    def test_fallthrough_no_match(self):
        """Non-matching title -> falls through to LLM."""
        result, reason = prefilter_score("Product Manager", "Walmart", jd_usable=True)
        assert result is None
        assert reason is None

    def test_fallthrough_non_domain_no_jd(self):
        """Non-domain title with no JD still falls through (not in-domain)."""
        result, reason = prefilter_score("Product Manager", "Walmart", jd_usable=False)
        assert result is None
        assert reason is None

    def test_none_title_handled(self):
        """None title should not crash."""
        result, reason = prefilter_score(None, "Google", True)
        assert result is None
        assert reason is None

    def test_empty_title_handled(self):
        result, reason = prefilter_score("", "Google", True)
        assert result is None
        assert reason is None

    def test_result_dict_shape_hard_reject(self):
        """Hard reject result has all expected keys."""
        result, _ = prefilter_score("Registered Nurse", "Hospital Corp", True)
        expected_keys = {
            "score_status",
            "relevance_score",
            "interview_likelihood",
            "strengths_alignment",
            "industry_sector",
            "comp_estimate",
            "ai_notes",
            "score_flag_reason",
            "remote_status",
            "scored_by",
            "company_tier",
        }
        assert set(result.keys()) == expected_keys
        assert result["remote_status"] == "Unknown"
        assert result["industry_sector"] is None
        assert result["comp_estimate"] is None

    def test_result_dict_shape_in_domain(self):
        """In-domain result has all expected keys."""
        result, _ = prefilter_score("NPI Program Manager", "Acme Corp", jd_usable=False)
        expected_keys = {
            "score_status",
            "relevance_score",
            "interview_likelihood",
            "strengths_alignment",
            "industry_sector",
            "comp_estimate",
            "ai_notes",
            "score_flag_reason",
            "remote_status",
            "scored_by",
            "company_tier",
        }
        assert set(result.keys()) == expected_keys
        assert result["score_flag_reason"] is None  # not flagged

    def test_stage1_takes_priority_over_stage2(self):
        """A hard-reject title that is NOT DC-overridden rejects even without JD."""
        result, reason = prefilter_score("Network Engineer", "Meta", jd_usable=False)
        assert result is not None
        assert result["relevance_score"] == 1
        assert "hard reject" in reason.lower()

    def test_whitespace_title_stripped(self):
        """Leading/trailing whitespace on title should not affect matching."""
        result, reason = prefilter_score("  Software Engineer  ", "Acme", True)
        assert result is not None
        assert result["relevance_score"] == 1


# ── _excluded_employer_match ──────────────────────────────────────────────────


class TestExcludedEmployerMatch:
    """#84 — Stage 1 company exclusion via excluded_employers.yaml."""

    @pytest.mark.parametrize(
        "company",
        ["Excluded Corp", "excluded corp", "EXCLUDED CORP", "  Excluded Corp  "],
    )
    def test_exact_case_insensitive(self, company):
        assert _excluded_employer_match(company) is not None

    def test_exact_with_punctuation(self):
        assert _excluded_employer_match("ExampleCo, Inc.") is not None

    @pytest.mark.parametrize(
        "company",
        ["State of California", "state of texas", "STATE OF OREGON"],
    )
    def test_regex_match(self, company):
        assert _excluded_employer_match(company) is not None

    def test_regex_substring(self):
        """Regex `\\bholdings\\b` matches any company with 'holdings' as a word."""
        assert _excluded_employer_match("Acme Holdings LLC") is not None

    @pytest.mark.parametrize(
        "company",
        ["Google", "Meta", "Acme Inc", "Workday"],
    )
    def test_no_match(self, company):
        assert _excluded_employer_match(company) is None

    def test_empty_string(self):
        assert _excluded_employer_match("") is None

    def test_whitespace_only(self):
        assert _excluded_employer_match("   ") is None

    def test_missing_file_no_op(self, monkeypatch, tmp_path):
        """Missing config file → no-op (no match for anything)."""
        monkeypatch.setattr(config_loader, "_EXCLUDED_EMPLOYERS_PATH", tmp_path / "nonexistent.yaml")
        config_loader._reset_cache()
        assert _excluded_employer_match("Excluded Corp") is None
        assert _excluded_employer_match("Anything") is None

    def test_empty_file_no_op(self, monkeypatch, tmp_path):
        """Empty config file → no-op."""
        empty = tmp_path / "empty.yaml"
        empty.write_text("")
        monkeypatch.setattr(config_loader, "_EXCLUDED_EMPLOYERS_PATH", empty)
        config_loader._reset_cache()
        assert _excluded_employer_match("Excluded Corp") is None

    def test_empty_lists_no_op(self, monkeypatch, tmp_path):
        """File with empty `exact` and `regex` lists → no-op."""
        f = tmp_path / "ee.yaml"
        f.write_text("exact: []\nregex: []\n")
        monkeypatch.setattr(config_loader, "_EXCLUDED_EMPLOYERS_PATH", f)
        config_loader._reset_cache()
        assert _excluded_employer_match("Excluded Corp") is None

    def test_only_exact(self, monkeypatch, tmp_path):
        """`regex` key absent → exact-only matching still works."""
        f = tmp_path / "ee.yaml"
        f.write_text('exact:\n  - "Just Exact"\n')
        monkeypatch.setattr(config_loader, "_EXCLUDED_EMPLOYERS_PATH", f)
        config_loader._reset_cache()
        assert _excluded_employer_match("Just Exact") is not None
        assert _excluded_employer_match("Anything Else") is None

    def test_only_regex(self, monkeypatch, tmp_path):
        """`exact` key absent → regex-only matching still works."""
        f = tmp_path / "ee.yaml"
        f.write_text("regex:\n  - '^Acme.*'\n")
        monkeypatch.setattr(config_loader, "_EXCLUDED_EMPLOYERS_PATH", f)
        config_loader._reset_cache()
        assert _excluded_employer_match("Acme Holdings") is not None
        assert _excluded_employer_match("Other Co") is None


class TestPrefilterScoreExcludedEmployer:
    """#84 — integration of excluded-employer check into prefilter_score."""

    def test_excluded_employer_scored_one(self):
        result, reason = prefilter_score("Product Manager", "Excluded Corp", jd_usable=True)
        assert result is not None
        assert result["relevance_score"] == 1
        assert result["score_status"] == "scored"
        assert result["score_flag_reason"] == "excluded_employer"
        assert "Excluded Corp" in reason

    def test_excluded_employer_regex_match(self):
        result, reason = prefilter_score("Program Manager", "State of California", jd_usable=True)
        assert result is not None
        assert result["relevance_score"] == 1
        assert result["score_flag_reason"] == "excluded_employer"

    def test_title_reject_takes_priority_over_employer(self):
        """A hard-rejected title at an excluded employer reports as title-reject,
        preserving signal about WHY the job is filtered."""
        result, reason = prefilter_score("Software Engineer", "Excluded Corp", jd_usable=True)
        assert result is not None
        assert result["relevance_score"] == 1
        # Title-reject reason, NOT excluded_employer
        assert "hard reject" in reason.lower()
        assert result["score_flag_reason"] != "excluded_employer"

    def test_excluded_employer_skipped_when_no_match(self):
        """Non-excluded company falls through to normal flow."""
        result, _ = prefilter_score("Product Manager", "Google", jd_usable=True)
        assert result is None  # falls through to LLM


def test_stage1_hard_reject_returns_scored_by():
    from findajob.scorer_prefilter import prefilter_score

    result, _ = prefilter_score("Software Engineer at Acme", "Acme", jd_usable=True)
    if result is not None:
        assert result["scored_by"] == "prefilter_stage1"
        assert "company_tier" in result


def test_stage2_indomain_nojd_returns_scored_by():
    from findajob.scorer_prefilter import prefilter_score

    result, _ = prefilter_score("Data Center Operations Manager", "Acme", jd_usable=False)
    if result is not None:
        assert result["scored_by"] == "prefilter_stage2"
        assert "company_tier" in result
