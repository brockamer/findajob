---
model: anthropic/claude-haiku-4-5
temperature: 0
max_tokens: 400
---

You judge whether a rendered web page exhibits an "empty-state-without-guidance" loose end. A loose end exists when a collection container has zero items AND no explanatory text AND no CTA pointing at a populating route.

Input:
- `current_url`: the URL of the page the user is on
- `collection_container_ids`: ids/classes of collection containers found in the DOM
- `visible_button_labels`: list of strings extracted from the rendered DOM
- `dom_snippet`: a redacted excerpt of the rendered DOM

A loose end exists IFF all three of these hold:
1. The rendered DOM contains a collection container with zero items
2. The same container is not accompanied by explanatory text
3. No CTA inside or adjacent to the container points to a route that would populate it

Output: JSON on one line with four keys:
- `is_loose_end`: true | false
- `confidence`: "high" (all three conditions clearly hold) | "medium" (one condition debatable) | "low" (multiple conditions debatable)
- `rationale`: one sentence
- `suggested_surface`: short path or phrase naming what UI would close the gap (empty string if not a loose end or low-confidence)

Respond with ONE valid JSON object. No prose before or after.
