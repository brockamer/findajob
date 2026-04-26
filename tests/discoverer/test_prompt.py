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
Senior Person at Place.

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
Lead Other at Other Place.

## Target Companies / Organizations
Delta, Epsilon Org, Zeta LLC.
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
    """The scaffolding (everything except the verbatim profile) must contain
    no enumerated industries, named companies, or role-title lists."""
    prompt = build_prompt(_profile_a())
    # Strip the verbatim profile block; what's left is the scaffolding.
    scaffolding = prompt.replace(_profile_a().strip(), "")
    forbidden = (
        "tech", "software", "engineer", "GPU", "NVIDIA", "Meta", "Google",
        "social work", "nursing", "teaching", "robotics", "data center",
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
