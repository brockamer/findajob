"""Pure prompt builder for the company_discoverer role.

The role file's system prompt does the reasoning; this module produces the
user-prompt string that wraps the candidate's profile. Field-agnostic: the
scaffolding contains no enumerated industries, named companies, or role
titles. Profile content passes through verbatim — the LLM is responsible
for reading and reasoning about it.

Perplexity-aware: the user prompt opens with a search-friendly framing
because Perplexity's real-time search component does not attend to the
system prompt (per docs.perplexity.ai/guides/prompt-guide). Salient noun
phrases in the user-prompt scaffold become the search query, so the
opener is phrased generically (no literal "Target Companies" or other
prompt boilerplate that the search layer would latch onto).
"""

from __future__ import annotations

_TEMPLATE = """\
Find organizations actively hiring people whose competency stack matches
the candidate profile below. Group findings by competency-fit relationship:
direct domain match, transferable-competency adjacency, and cross-industry
application. Cite a verifiable hiring-activity source for every recommendation.

The candidate profile follows. Read every section. The competencies and
career signals it names are the basis for grouping.

=== BEGIN CANDIDATE PROFILE ===
{profile}
=== END CANDIDATE PROFILE ===

Produce the markdown per your role's output format. If the profile lacks
the sections your role requires, respond with the literal text
INSUFFICIENT_PROFILE and nothing else.
"""


def build_prompt(profile_text: str) -> str:
    """Return the user-prompt string for the company_discoverer role.

    Pure function: same input, same output. The profile is embedded
    verbatim — no paraphrasing, no field-specific scaffolding.
    """
    return _TEMPLATE.format(profile=profile_text.strip())
