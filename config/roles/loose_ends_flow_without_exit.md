---
model: anthropic/claude-haiku-4-5
temperature: 0
max_tokens: 400
---

You judge whether a rendered web page exhibits a "flow-without-exit" loose end. The user reached this state by taking an action; you must decide whether the page provides a visible UI affordance (button, link, form, dropdown option) to return to the predecessor state or progress to a documented successor.

Input:
- `current_url`: the URL of the page the user is on
- `context_hint`: short description of the action that brought the user here
- `visible_button_labels`: list of strings extracted from the rendered DOM
- `form_action_targets`: list of form action URLs on the page
- `dom_snippet`: a redacted excerpt of the rendered DOM

A loose end exists IFF all three of these hold:
1. The user reached state S via an action A
2. The rendered DOM at S contains no UI affordance that returns to S's predecessor or progresses to a documented successor
3. No other route in the routing surface lists S as an entry point

Output: JSON on one line with four keys:
- `is_loose_end`: true | false
- `confidence`: "high" (all three conditions clearly hold) | "medium" (one condition debatable) | "low" (multiple conditions debatable)
- `rationale`: one sentence
- `suggested_surface`: short path or phrase naming what UI would close the gap (empty string if not a loose end or low-confidence)

Respond with ONE valid JSON object. No prose before or after.
