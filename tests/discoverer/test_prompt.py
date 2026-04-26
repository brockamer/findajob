from findajob.discoverer.prompt import build_prompt


def _profile_a() -> str:
    return """
## Identity
Name: Test One

## Core Competencies
- Skill A
- Skill B

## Career Summary
Built thing X.

## Target Roles
- **Senior Person at Place** — making widgets and gizmos
- **Junior Person** — assisting

## Target Companies / Organizations
Acme, Beta Co, Gamma Inc.
"""


def _profile_b() -> str:
    return """
## Identity
Name: Test Two

## Core Competencies
- Different Skill C
- Different Skill D

## Career Summary
Solved problem Y.

## Target Roles
- **Lead Other at Other Place** — solving problem Z
- **Backup Other** — supporting

## Target Companies / Organizations
Delta, Epsilon Org, Zeta LLC.
"""


def _profile_singular_target_role() -> str:
    """Profile that uses ## Target Role (singular) instead of ## Target Roles."""
    return """
## Identity
Name: Test Three

## Core Competency
- Singular Skill E

## Career Summary
Did thing Z.

## Target Role
- **The One Role** — for the unique thing

## Target Companies / Organizations
Eta Corp.
"""


def test_build_prompt_includes_full_profile_verbatim() -> None:
    profile = _profile_a()
    prompt = build_prompt(profile)
    # The full profile passes through verbatim — the role file (system) does the reasoning.
    assert profile.strip() in prompt


def test_build_prompt_references_expected_sections_by_name() -> None:
    prompt = build_prompt(_profile_a())
    for marker in ("Core Competencies", "Career Summary", "Target Roles", "Target Companies"):
        assert marker in prompt


def test_build_prompt_is_field_agnostic() -> None:
    """The scaffolding (everything except the verbatim profile and the
    extracted target-role / competency bullets) must contain no enumerated
    industries, named companies, or role-title lists."""
    prompt = build_prompt(_profile_a())
    # Strip the verbatim profile block AND the extracted bullets; what's left is the scaffolding.
    scaffolding = prompt.replace(_profile_a().strip(), "")
    scaffolding = scaffolding.replace("Senior Person at Place.", "")
    scaffolding = scaffolding.replace("- Skill A", "").replace("- Skill B", "")
    forbidden = (
        "tech",
        "software",
        "engineer",
        "GPU",
        "NVIDIA",
        "Meta",
        "Google",
        "social work",
        "nursing",
        "teaching",
        "robotics",
        "data center",
    )
    for tok in forbidden:
        assert tok.lower() not in scaffolding.lower(), f"scaffolding contains field-locked token: {tok!r}"


def test_build_prompt_is_pure_and_deterministic() -> None:
    profile = _profile_a()
    assert build_prompt(profile) == build_prompt(profile)


def test_build_prompt_two_profiles_produce_different_outputs() -> None:
    a = build_prompt(_profile_a())
    b = build_prompt(_profile_b())
    assert a != b
    # Profile A markers
    assert "Skill A" in a and "Acme" in a
    # Profile B markers
    assert "Skill C" in b and "Delta" in b


def test_build_prompt_opening_sentence_inlines_first_role_anchor() -> None:
    """Perplexity's search component generates ONE search query from the
    user prompt's opening sentence (per docs.perplexity.ai/guides/prompt-guide,
    system prompt is ignored). Empirically, a "see below" structure produced
    searches for the generic scaffold phrases ("competency hiring" → HR
    methodology articles) instead of the candidate's actual field. The fix:
    inline the first target-role bullet's headline + descriptor into the
    opening sentence itself so the search query is field-grounded.
    """
    prompt = build_prompt(_profile_a())
    # First bullet's headline + descriptor appear in the FIRST sentence
    first_sentence = prompt.split(".", 1)[0]
    assert "Senior Person at Place" in first_sentence
    assert "making widgets and gizmos" in first_sentence
    assert "hiring" in first_sentence.lower()


def test_build_prompt_two_profiles_have_different_opening_anchors() -> None:
    """Same template, different candidates → different opening sentence
    anchors. This is the field-agnostic guarantee."""
    a = build_prompt(_profile_a()).split(".", 1)[0]
    b = build_prompt(_profile_b()).split(".", 1)[0]
    assert "Senior Person at Place" in a and "Senior Person at Place" not in b
    assert "Lead Other at Other Place" in b and "Lead Other at Other Place" not in a


def test_build_prompt_accepts_singular_target_role_alias() -> None:
    """Profiles using `## Target Role` (singular) or `## Core Competency`
    (singular) must still extract the role anchor."""
    prompt = build_prompt(_profile_singular_target_role())
    first_sentence = prompt.split(".", 1)[0]
    assert "The One Role" in first_sentence
    assert "for the unique thing" in first_sentence


def test_build_prompt_falls_back_when_target_roles_missing() -> None:
    """Profile without a parseable first bullet still produces a grammatical
    opener (no crash, no template-format error)."""
    minimal = "## Identity\nName: X\n\n## Career Summary\nNothing else.\n"
    prompt = build_prompt(minimal)
    # Doesn't crash; profile is still embedded
    assert minimal.strip() in prompt
    # Falls back to a generic-but-grammatical opener
    first_sentence = prompt.split(".", 1)[0]
    assert "hiring" in first_sentence.lower()
    # Section placeholders appear later in the prompt
    assert "(not specified in profile)" in prompt


def test_build_prompt_descriptor_clause_is_optional() -> None:
    """A bold-headline bullet with no `—` descriptor still produces a clean
    opener (no trailing 'for ' fragment)."""
    profile = "## Target Roles\n- **Just A Headline**\n\n## Core Competencies\n- X\n"
    prompt = build_prompt(profile)
    first_sentence = prompt.split(".", 1)[0]
    assert "Just A Headline" in first_sentence
    # No dangling " for " preposition with empty descriptor
    assert " for ." not in prompt
    assert (
        "hiring Just A Headline." in prompt
        or "hiring Just A Headline\n" in prompt
        or "hiring Just A Headline " in prompt
    )


def test_build_prompt_includes_novelty_mandate_in_opener() -> None:
    """The opener must signal that the discoverer's value is novelty —
    surfacing companies the candidate hasn't already named. Empirically
    caught when 4 of 5 returned companies were already on the candidate's
    Tier 1 list (OpenAI / Google / Apple / Oracle); the discoverer was
    re-discovering rather than discovering."""
    prompt = build_prompt(_profile_a())
    first_sentence = prompt.split(".", 1)[0].lower()
    assert "emerging" in first_sentence or "less-prominent" in first_sentence


def test_build_prompt_relaxes_citation_requirement() -> None:
    """The strict per-row citation requirement caused real-API smokes to
    refuse valid emerging-company recommendations when the model couldn't
    confirm a URL. The opener must now explicitly tell the model that
    citations are OPTIONAL — recommend the company by name + reasoning
    even when no URL is in the search results. Operator hand-verifies."""
    prompt = build_prompt(_profile_a())
    # Opener must tell the model citations are optional and that it should
    # NOT refuse to recommend due to missing URLs.
    assert "OMIT the citation" in prompt or "optional" in prompt.lower()
    assert "Do NOT refuse" in prompt or "do not refuse" in prompt.lower()


def test_build_prompt_passes_target_companies_as_exclusion_list() -> None:
    """The candidate's ## Target Companies / Organizations section is
    extracted and passed as an explicit EXCLUSION block (not an inclusion
    seed). This is the load-bearing instruction that turns the discoverer
    into a funnel-widener rather than a list-regurgitator."""
    prompt = build_prompt(_profile_a())
    # Exclusion-list block exists and contains the candidate's named companies
    assert "EXCLUSION LIST" in prompt
    assert "DO NOT recommend" in prompt
    assert "Acme" in prompt and "Beta Co" in prompt and "Gamma Inc" in prompt
    # The exclusion-list block appears BEFORE the profile block (so the model
    # processes the exclusion instruction before reading the rest)
    excl_pos = prompt.index("EXCLUSION LIST")
    profile_pos = prompt.index("=== BEGIN CANDIDATE PROFILE ===")
    assert excl_pos < profile_pos


def test_build_prompt_handles_missing_target_companies_section() -> None:
    """A profile without a ## Target Companies section still produces a
    valid prompt — the exclusion list shows '(none specified)' rather than
    crashing or leaving an empty placeholder."""
    no_targets = "## Identity\nName: Y\n\n## Target Roles\n- **A Role** — for X\n\n## Core Competencies\n- Skill\n"
    prompt = build_prompt(no_targets)
    assert "(none specified)" in prompt
    assert "EXCLUSION LIST" in prompt
