# voice_samples/

Writing samples used by the `cover_letter_writer` and `outreach_drafter` roles
to calibrate the candidate's voice — sentence rhythm, word choice, register,
parenthetical asides, paragraph cadence.

## What goes here

`.md` or `.txt` files — long-form, first-person, nonfiction prose in roughly the
register you'd use in a thoughtful cover letter or professional outreach. The
unaided writing matters more than the topic.

Good sources:
- Personal blog posts, Substack archives, long essays you've written
- Long-form emails to colleagues explaining something
- Application essays you're proud of
- Cover letters you've written and sent that you'd reference for tone

Avoid:
- LLM-generated content (drift loop — model trains on its own output)
- Lists, bullets, code, tweets, text messages (voice lives in flow between sentences)
- Resume bullets (compressed prose isn't voice signal)

The more samples, the better calibration. ~5,000–8,000 words across one or more
files is a strong baseline.

## Loader behavior

`findajob.utils.load_voice_samples()` reads every `.md` and `.txt` file in this
directory (except files starting with `README`), concatenates them with double
newlines, and caps the result at 32,000 characters (~8,000 tokens). The combined
text is injected into the cover letter and outreach prompts as a `VOICE SAMPLES:`
section under explicit "use for style only, not topic" guard rails.

## Naming

No required convention. Descriptive names help. Files starting with `README` are
ignored by the loader.

    voice-samples.md
    blog_export_2024.md
    personal_essay_2023.txt

## Gitignore

This directory is gitignored. **Do not commit writing samples.** They contain
personal voice, employer names, and context that should not be public.

Add your files here manually after cloning the repo.
