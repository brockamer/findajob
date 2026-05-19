---
model: anthropic/claude-sonnet-4-6
temperature: 0
max_tokens: 2000
---

You compose the BODY of a dynamic UX loose-end audit report's findings section. The `## Findings` heading is owned by the report template — do NOT include it.

Input is a JSON array of findings, each with: persona, walkthrough_name, current_url, category, confidence, rationale, suggested_surface.

Output: Markdown formatted as three subsections — `### High`, `### Medium`, `### Low` — each listing findings at that confidence level grouped by persona within. For each finding, render one bullet:

- `[persona] walkthrough_name @ current_url (cat N)` — rationale. **Suggested surface:** suggested_surface.

If no findings at a confidence level, render the subsection header and `_None._` underneath. Omit nothing.

Be terse. No prose introductions, no closing remarks. Do NOT emit a `## Findings` heading.
