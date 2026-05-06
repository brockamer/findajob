---
model: openrouter:anthropic/claude-opus-4.7
max_tokens: 4096
---

You are processing voice samples (the candidate's own personal long-form prose)
to be used as STYLE calibration for cover letters and outreach. The text below
has already been stripped of markdown structure. Your job is to generalize
personal identifiers that the candidate may not have thought to scrub, while
preserving their natural voice and prose flow.

REDACT (replace with generic equivalents):
- Specific dates that anchor the candidate in time → "around that time", "a few years ago", "in those days"
- Named third parties (friends, partners, doctors, teachers, colleagues by name) → "a friend", "a teacher", "a colleague"
- Exact geographic specifiers (cities, states, regions, named neighborhoods, named freeways, named landmarks) → "the city", "a small town", "another state", "the freeway"
- Named institutions (treatment facilities, hospitals, universities, specific employers, named programs) → "the program", "the hospital", "a university", "an employer"
- Exact dollar amounts → "a lot", "a meaningful amount"
- Exact durations that combined with other context would identify the candidate (e.g., "the six year relationship I had until 2019") → "a long relationship"
- Phone numbers, email addresses, URLs → strip entirely

PRESERVE EXACTLY:
- Every word of the actual prose that is not a specific identifier
- Sentence structure, rhythm, parenthetical asides, em-dashes, contractions
- Typos, idioms, idiosyncratic word choices, deliberate emphasis (CAPS, italics)
- Paragraph breaks (double newlines)
- The candidate's own name if it appears (it is their voice)
- Generic vocabulary: industry terms, common nouns, public figures, well-known concepts, named recovery programs (AA, NA, SMART Recovery), books, methodologies

RULES:
- Conservative bias: when in doubt, keep the prose as-is. False positives on stripping are worse than false positives on keeping.
- Do NOT rephrase, summarize, condense, or "improve" any sentence. Voice signal lives in unaided writing.
- Do NOT correct typos, grammar, or punctuation.
- Output only the redacted text. No preamble, no commentary, no markdown code fences, no closing notes about what you changed.

TEXT TO REDACT:
