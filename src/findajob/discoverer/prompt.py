"""Pure prompt builder for the company_discoverer role.

The role file's system prompt does the reasoning; this module produces the
user-prompt string that wraps the candidate's profile. Field-agnostic: the
scaffolding contains no enumerated industries, named companies, or role
titles. Profile content passes through verbatim — the LLM is responsible
for reading and reasoning about it.
"""

from __future__ import annotations

_TEMPLATE = """\
The candidate profile is below, between the delimiters. Read it carefully.
Pay particular attention to the sections named: Core Competencies, Career
Summary, Target Roles (or Target Role), Target Companies / Organizations.

The Target Companies / Organizations section is a seed, not the universe.
Augment it with companies the candidate has not named, grouped into the
three clusters described in your role.

=== BEGIN CANDIDATE PROFILE ===
{profile}
=== END CANDIDATE PROFILE ===

Now produce the discovered-companies markdown per your role's output
format. Cite every company. If the profile lacks the required sections,
respond with the literal text INSUFFICIENT_PROFILE and nothing else.
"""


def build_prompt(profile_text: str) -> str:
    """Return the user-prompt string for the company_discoverer role.

    Pure function: same input, same output. The profile is embedded
    verbatim — no paraphrasing, no field-specific scaffolding.
    """
    return _TEMPLATE.format(profile=profile_text.strip())
