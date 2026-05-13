I'm a user of `findajob`, a private LLM-driven job-matching pipeline.
The cover-letter writer generates letters in my voice by reading
`candidate_context/voice_samples/*.md`. Help me extract voice patterns
from a sample cover letter I'm about to paste, and emit a new entry
suitable for that directory.

## What I want from you

Read the sample I paste. Then, before emitting anything, summarize back
in 3–5 bullets what you noticed about my voice — sentence rhythm, word
choice, structural habits. Wait for me to confirm or correct that
read. Voice is easy to misread on a single sample, and the cover-letter
writer will faithfully reproduce whatever you put down.

Once I confirm, emit a single markdown file body using this exact shape:

```
# Voice sample: <one-line label I give you>

## What this sample is
<one paragraph: who I wrote it for, what stage of the search, why I
think it represents my voice well>

## Sentence-rhythm patterns
- <pattern 1>
- <pattern 2>
- ...

## Words and phrases I reach for
- <word/phrase>: <when I use it / what register>
- ...

## Words and phrases I never use
- <word/phrase>: <why — usually a register mismatch or a cliché I've
  worn out>
- ...

## Opening pattern
<2–3 sentences describing how I open a cover letter — formal vs
informal, do I name the role, do I lead with a hook>

## Closing pattern
<2–3 sentences describing how I close — call to action vs soft
sign-off, what I say about next steps>

## When NOT to imitate this sample
<one paragraph: contexts where this voice would land badly, e.g.
"this was for a startup; for a federal contractor reduce the warmth
by half">
```

## Hard constraints on your output

- **Don't invent quirks I don't actually have.** If the sample is short
  and you can't pin down a pattern, say so explicitly in the relevant
  section — "no strong pattern from this sample" beats a hallucinated
  one.
- **Don't redact or rephrase the sample itself.** I'm extracting voice,
  not approving content. Quote my exact phrasing in the bullets when
  illustrating a pattern.
- **Don't merge with other samples.** Each sample stands alone; the
  cover-letter writer reads the whole directory and synthesizes.

## After we have the file

I'll paste the result into `/config/` and save it as
`candidate_context/voice_samples/<descriptor>.md`. I may also rerun this
tool with a second sample later — a second perspective on my voice is
often more useful than refining the first.

## Ready?

Ask me for the sample. After I paste it, give me the 3–5 bullet summary
before emitting the file.
