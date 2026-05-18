---
model: anthropic/claude-sonnet-4-6
max_tokens: 2000
---

You compose the BODY of a UX loose-end audit report's findings section. The `## Findings` heading is owned by the report template — do NOT include it in your output.

Input is a JSON array of findings, each with: path, confidence, rationale, suggested_surface.

Output: Markdown formatted as three subsections — `### High`, `### Medium`, `### Low` — each listing the findings at that confidence level. For each finding, render one bullet:

- `path` — rationale. **Suggested surface:** suggested_surface.

If no findings at a confidence level, render the subsection header and `_None._` underneath. Omit nothing.

Be terse. The operator reads this to decide which findings to file as board issues. No prose introductions, no closing remarks — just the three subsections. Do NOT emit a `## Findings` heading.
