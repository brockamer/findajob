I'm a user of `findajob`, a private LLM-driven job-matching pipeline. The
pipeline scores incoming jobs against `candidate_context/profile.md`. My
search focus has shifted since I last set up findajob, and my profile is
now out of date in places. Help me refresh it.

## What I want from you

A conversational walkthrough. Ask me questions one section at a time, in
this order:

1. **Target role.** What role(s) am I currently targeting? Has the primary
   role changed, or only the adjacent roles I'd consider? (1–2 sentences
   from me is enough — push back if my answer is vague.)
2. **Target companies / organizations.** Which companies or organization
   types do I want to prioritize? Which ones have I crossed off since last
   time? Why?
3. **What to emphasize.** Which competencies / experiences should the
   scorer weight higher than my résumé alone suggests? (Often: leadership
   on a project the title doesn't capture, or domain expertise that lives
   in projects rather than employer names.)
4. **Things to avoid mentioning.** What should the cover-letter writer
   never bring up about my background? (Health, prior employers I left
   poorly, etc.)
5. **Excluded categories.** What roles or industries should always score
   1, regardless of how close the title looks? (Be specific — vague
   exclusions cause silent over-rejection.)
6. **Title calibration notes.** Are there ambiguous titles in my field
   where the same words can mean different jobs? Tell me which sub-shapes
   I can do vs which I can't.

For each section, ask follow-ups if my answer is generic. Then summarize
back what you heard before moving on.

## How to format your final output

When all six sections are covered, emit a single markdown block that
I can paste into `/config/` to edit `candidate_context/profile.md`. Use
this shape:

```
## Target Role
<my new target-role paragraph>

## Target Companies / Organizations
<my new company list>

## What to Emphasize
<my new emphasis paragraph>

## Things to Avoid Mentioning
<my new avoid list>

## Excluded Categories
<my new exclusion list, one bullet per category>

## Title Calibration Notes
<my new calibration notes, one entry per ambiguous title family>
```

**Do not** propose changes to these sections — they're stable identity data:
- `## Identity`
- `## Career Summary`
- `## Employer History`
- `## Core Competencies`

If something I say sounds like it belongs in one of those, flag it but
don't write into it; tell me to edit those manually via `/config/`.

## Ready?

Start with section 1 (Target role).
