I'm a user of `findajob`, a private LLM-driven job-matching pipeline.
The scorer is matching me to jobs I'd reject on sight. Help me articulate
the pattern precisely enough to add it to my config.

## The locus question — always first

Before we add anything, decide WHERE the signal lives. There are exactly
two loci, and the right destination depends on which it is. Ask me 3–5
specific example jobs I'd reject. For each one, ask:

> "If you had only seen the title — not the JD — would you have rejected
> it? Yes or no?"

- **YES → title-only signal.** The TITLE alone tells me "no" regardless
  of JD content. Examples (illustrative — substitute mine): every
  "Junior X", every title in a function I don't do. These belong in
  `config/prefilter_rules.yaml` as a regex hard-reject; matched jobs
  score 1 with no LLM call.
- **NO → JD-content signal.** The title looks plausible but specific JD
  content tells me "no". Example shape: a title family I generally CAN
  do (say "Program Manager"), but specific sub-flavors of that title
  family that fall outside my actual work. These belong in
  `candidate_context/profile.md` under `## Excluded Categories` or
  `## Title Calibration Notes` so the scorer LLM reads them as part of
  my profile.

**Never write the same signal into both files.** Pick one. If I'm
unsure, treat it as JD-content (profile.md) — the scorer can still
catch it, and a profile note is easier to reverse than a regex rule.

## Title-only signals — the prefilter output

When the signal is title-only, emit a regex pattern suitable for
`prefilter_rules.yaml`. Format:

```yaml
hard_rejects:
  <category-name>:
    - '(?i)\bpattern1\b'
    - '(?i)\bpattern2\b'
```

Use word boundaries (`\b`) and case-insensitive flag (`(?i)`). Avoid
substring matching — `Apple` would match `GreenApple`. Each pattern
must include a comment-style explanation in a separate prose section
so I remember WHY when I read this file in 6 months.

## JD-content signals — the profile output

When the signal is JD-content, emit a markdown block for
`candidate_context/profile.md`. Use one of:

```
## Excluded Categories
- <category>: <one-paragraph description, including which titles the
  category typically wears and what JD content I'd reject on>
```

or, for ambiguous title families where some shapes are fine and some
aren't:

```
## Title Calibration Notes
- <title family>: I can do <these shapes>. I cannot do <those shapes>.
  Distinguishing JD signals: <bullets>.
```

## Hard constraints on your output

- **Do not enumerate specific company names** in your output. The
  prefilter rules ship across multiple operators; my company-specific
  preferences belong elsewhere (`config/target_companies.md`, gitignored).
- **Do not invent industry vocabulary** I didn't use. If I gave you a
  specific phrase, use it verbatim; don't generalize it into a broader
  industry term unless I confirm.
- **Do not write into role files** (`config/roles/*.md`). Those are
  tracked code shared across operators; my exclusions are personal.

## Ready?

Ask me for the first 3–5 example jobs I'd reject. Then walk each one
through the locus question.
