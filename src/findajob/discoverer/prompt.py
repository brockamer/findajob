"""Pure prompt builder for the company_discoverer role.

The role file's system prompt does the reasoning; this module produces the
user-prompt string that wraps the candidate's profile. Field-agnostic: the
scaffolding contains no enumerated industries, named companies, or role
titles. Profile content passes through verbatim — the LLM is responsible
for reading and reasoning about it.

Perplexity-aware: the user prompt's OPENING SENTENCE drives the
single auto-generated search query (per docs.perplexity.ai/guides/
prompt-guide — system prompt is ignored by the search component).
We extract the first target-role bullet's headline + descriptor and
inline them in the opening sentence so the search query is
field-grounded.

Novelty-aware: the discoverer's value is finding companies the candidate
has NOT already named. The opener biases toward "emerging or less-
prominent" organizations, and we extract the candidate's static
``## Target Companies / Organizations`` section to pass as an explicit
exclusion list. Citations are OPTIONAL per row — over-strict per-row
URL requirements caused the model to refuse rather than recommend
training-data-known emerging companies; the operator is the
human-verification layer (the ``/config/`` editor allows hand-editing
``discovered_companies.md``).
"""

from __future__ import annotations

import re

_TEMPLATE = (
    "Identify 6-10 emerging or less-prominent organizations actively"
    " hiring {role_headline}{role_descriptor_clause}. Include a verifiable"
    " URL when you can confirm one (job posting, careers page, or hiring"
    " announcement); when you cannot confirm a URL, OMIT the citation"
    " entirely and rely on the company name + reasoning — the operator"
    " will hand-verify. Do NOT refuse to recommend a company just because"
    " no URL is in your search results.\n"
    "\n"
    "DO NOT recommend any company in the EXCLUSION LIST below. The candidate"
    " already knows these companies; recommending them adds no value. Your"
    " job is to widen the funnel beyond this list.\n"
    "\n"
    "EXCLUSION LIST (already on the candidate's target list — DO NOT recommend):\n"
    "{exclusion_list}\n"
    "\n"
    "The candidate's full target-role and competency context follows. After\n"
    "listing 6-10 NEW companies (none on the exclusion list), group them\n"
    "per your role's three-cluster taxonomy.\n"
    "\n"
    "Target roles the candidate is pursuing:\n"
    "{target_roles}\n"
    "\n"
    "Core competencies to anchor on:\n"
    "{core_competencies}\n"
    "\n"
    "=== BEGIN CANDIDATE PROFILE ===\n"
    "{profile}\n"
    "=== END CANDIDATE PROFILE ===\n"
    "\n"
    "Produce the markdown per your role's output format. If the candidate\n"
    "profile is missing both target roles and core competencies sections,\n"
    "respond with the literal text INSUFFICIENT_PROFILE and nothing else.\n"
)

_SECTION_RE_TEMPLATE = r"^##\s+{name}\s*$\n(?P<body>.*?)(?=^##\s|\Z)"

# Match the first bullet line that has a bold-marked headline. Captures the
# bold text and (optionally) the post-em-dash / post-hyphen descriptor up to
# the first sentence-ending punctuation or newline.
_FIRST_BULLET_RE = re.compile(
    r"^\s*-\s*\*\*(?P<headline>[^*]+?)\*\*\s*"
    r"(?:[—–-]\s*(?P<descriptor>[^\n.;]+))?",
    re.MULTILINE,
)


def _extract_section(profile_text: str, *aliases: str) -> str:
    """Return the body of the first matching ``## <name>`` section, stripped."""
    for name in aliases:
        pattern = _SECTION_RE_TEMPLATE.format(name=re.escape(name))
        match = re.search(pattern, profile_text, re.MULTILINE | re.DOTALL | re.IGNORECASE)
        if match:
            return match.group("body").strip()
    return ""


def _extract_role_anchor(target_roles_body: str) -> tuple[str, str]:
    """Parse the first bold-marked bullet's headline + descriptor."""
    match = _FIRST_BULLET_RE.search(target_roles_body)
    if not match:
        return ("people in the candidate's field", "")
    headline = match.group("headline").strip().rstrip(",.;:")
    descriptor = (match.group("descriptor") or "").strip().rstrip(",.;:")
    return (headline, descriptor)


def _placeholder_or(body: str, fallback: str) -> str:
    return body if body else fallback


def build_prompt(profile_text: str) -> str:
    """Return the user-prompt string for the company_discoverer role.

    Pure function: same input, same output. The opening sentence inlines
    the candidate's first target-role headline + descriptor so
    Perplexity's search query is field-grounded; the candidate's static
    ``## Target Companies / Organizations`` section is passed as an
    explicit exclusion list so the model surfaces NOVEL companies.
    """
    target_roles = _extract_section(profile_text, "Target Roles", "Target Role")
    core_competencies = _extract_section(profile_text, "Core Competencies", "Core Competency")
    exclusion_list = _extract_section(
        profile_text,
        "Target Companies / Organizations",
        "Target Companies/Organizations",
        "Target Companies",
        "Target Organizations",
    )
    headline, descriptor = _extract_role_anchor(target_roles)
    descriptor_clause = f" for {descriptor}" if descriptor else ""
    return _TEMPLATE.format(
        role_headline=headline,
        role_descriptor_clause=descriptor_clause,
        exclusion_list=_placeholder_or(exclusion_list, "(none specified)"),
        target_roles=_placeholder_or(target_roles, "(not specified in profile)"),
        core_competencies=_placeholder_or(core_competencies, "(not specified in profile)"),
        profile=profile_text.strip(),
    )
