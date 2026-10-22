# Marked master-resume sections and a checked change report — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep master-resume sections marked "not for submitted resumes" out of every tailored resume by code, and make the resume change report list only changes the documents contain.

**Architecture:** A new module splits the master resume into kept and marked text. Phase B gives `resume_tailor` and `resume_change_reviewer` the kept text only, gives `cover_letter_writer` the marked text after the shared cache prefix, and checks the tailored resume for copied marked text (event, review checklist, and a warning on `/materials/{fingerprint}`). A second module computes a deterministic line diff for the change reviewer, which now returns JSON; code checks every quoted passage against the documents and renders the report, marking unfound quotes `[UNVERIFIED]`.

**Tech Stack:** Python 3.12 stdlib (`re`, `unicodedata`, `json`, `dataclasses`), FastAPI + Jinja2 for the warning, pytest.

**Spec:** issue bodies #1098 and #1099, plus the Session notes of 2026-09-28 on both. No separate spec file; the decisions settled at session start are recorded under **Decisions** below.

## Goal and scope

The tailored resume is what a candidate sends to an employer. Today `resume_tailor` gets the whole master resume, including blocks the candidate marked as not for submitted resumes, and only the model's reading of a prose marker keeps that text out. In one of two observed postings it did not (#1098). The change reviewer describes differences free-form and in both observed postings it reported edits the resume does not contain (#1099). Both defects are the class AGENTS.md § "Hard rejects are code" exists for: a constraint enforced only by prompt instruction. This plan moves both constraints into code.

**Not in scope.** The model-swap comparison against #1089's benchmark (#1099 approach step 3) — the prompt and output-shape change comes first. Detecting a *paraphrase* of marked text (only copied word runs are detected; the docs say so). Removing marked text from the Phase A briefing: the briefing is interview material and may use it. Moving `scripts/diag/validate_resume.py` into the package (#1167). Edits to `config/roles/resume_tailor.md` (#1153 owns lines 99, 102 and 127–128; this plan does not touch the file).

## Decisions (settled 2026-10-21)

1. **Bundle.** #1099 ships in this PR with #1098: the change-report diff must compare against the master with marked sections removed, or every marked line reads as a removal.
2. **Marker convention.** A section is marked when its heading line, or the first non-blank line under its heading, contains `not for submitted resumes` (case-insensitive, after removing `*` and `_`). The section runs from its heading to the line before the next heading of the same or higher level, or to the end of the file. This matches the existing prose-marker shape (a heading followed by `*(For interview prep, cover letters, and outreach — not for submitted resumes)*`). No fenced or machine marker.
3. **Cache prefix.** `shared_candidate_jd` carries the kept master. `cover_letter_writer` gets the marked sections appended after the voice samples, so its prefix still extends the tailor's and both keep their cache hit. With no marked section, every Phase B prompt is byte-identical to today's.
4. **Briefing leak path.** Phase A is unchanged. The post-check on the tailored resume is required, not optional, because the briefing can carry marked text to the tailor.
5. **Real-posting criterion (#1099).** Run once before the PR opens, with the operator's approval, on a read-only scratch copy of a few real prep folders; record counts only. The model swap is out of scope.
6. **Change-report shape.** The reviewer returns JSON; code checks and renders the Markdown report, so the `Resume Changes` file and its materials grouping keep their name and place. The event is `change_report_checked` (fields `claims`, `unsupported`, `parse_failed`), logged on every run so the defect rate has a denominator; this replaces the `change_report_unsupported_claim` name the issue proposed.

## Global Constraints

- LLM calls go through `run_role` (→ `findajob.llm.openrouter.complete()`) only.
- Paths come from `findajob.paths` or `request.app.state.base_root`; no hardcoded `/app` or home paths.
- Tracked files, tests included, carry no candidate content and no field-locked examples: use synthetic, cross-field text (`Acme`, "led the night shift", "a colleague's quote").
- New modules are under `src/findajob/prep/` and pass `uv run mypy src/findajob/`.
- Do not edit `config/roles/resume_tailor.md` or `scripts/diag/validate_resume.py`.
- Phase A (`_run_prep_phase_a`) is unchanged.
- With no marked section in the master resume, Phase B's `resume_tailor`, `cover_letter_writer` and `briefing_writer` prompts and prefixes are byte-identical to today's.

## Review Focus

1. **Marker on a line that is neither a heading nor the first line under one** — not stripped. The operator must hear about it: Phase B logs `master_resume_marker_unrecognized` (test in Task 2).
2. **Master resume with CRLF line endings** — split and detection behave as with LF (test in Task 1).
3. **Reviewer returns valid JSON with all lists empty** — zero claims is a pass, not a parse failure (test in Task 4).
4. **Short attribution or common lines in a marked section** (`— A. Colleague, Director`) — never flagged, because detection needs six consecutive words that the kept master does not also contain (test in Task 1).
5. **Marked text containing HTML-like characters** shown in the warning — escaped by Jinja autoescape, never rendered (test in Task 3).

---

### Task 1: `resume_markers` — split marked sections and find copied text

**Files:**
- Create: `src/findajob/prep/resume_markers.py`
- Test: `tests/test_resume_markers.py`

**Interfaces:**
- Produces:
  - `MARKER_RE: re.Pattern[str]` = `re.compile(r"not\s+for\s+submitted\s+resumes", re.IGNORECASE)`
  - `MIN_RUN_WORDS: int = 6`
  - `normalize_for_match(text: str) -> str`
  - `split_marked_sections(master_text: str) -> tuple[str, str]` — `(kept, marked)`
  - `find_marked_text(resume_text: str, marked_text: str, kept_text: str) -> list[str]`

- [ ] **Step 1: Write the failing tests** in `tests/test_resume_markers.py`. Fixture master (synthetic):

```python
MASTER = (
    "# Jane Candidate\n\n"
    "## Experience\n\n"
    "### Acme — Shift Lead\n"
    "- Led the night shift through a site move without missing a delivery date.\n\n"
    "## Selected Quotes\n"
    "*(For interview prep, cover letters, and outreach — not for submitted resumes)*\n\n"
    '- "She turned a struggling night shift into the steadiest team in the building."\n'
    "- — A. Colleague, Director\n\n"
    "### Older quotes\n"
    '- "Calm under pressure and generous with credit for every success."\n\n'
    "## Skills\n"
    "- Scheduling, inventory control\n"
)
```

Tests and their assertions:
- `test_split_removes_marked_section_and_its_subsections` — `kept` contains `"## Experience"`, `"## Skills"`, `"Led the night shift"`; contains neither `"Selected Quotes"`, `"steadiest team"`, `"Older quotes"` nor `"generous with credit"`; `marked` contains `"## Selected Quotes"`, `"steadiest team"` and `"generous with credit"`, and not `"## Skills"`.
- `test_marker_in_heading_marks_section` — master with `## Quotes (not for submitted resumes)\n- "x y z"\n## Skills\n- a\n` → `"x y z"` in `marked`, not in `kept`.
- `test_marked_section_at_end_of_file` — marked section is the last section (no following heading) → whole tail in `marked`.
- `test_no_marker_returns_input_unchanged` — for `MASTER.replace("not for submitted resumes", "for reference")`: `kept == input` and `marked == ""`.
- `test_crlf_master_splits_like_lf` — `split_marked_sections(MASTER.replace("\n", "\r\n"))` → `"steadiest team"` in `marked`, not in `kept`; `"Led the night shift"` in `kept`.
- `test_heading_inside_code_fence_is_not_a_heading` — a ```` ``` ```` fenced block inside the marked section containing `## not a heading` does not end the section.
- `test_find_flags_copied_clause` — resume `"## Summary\nShift lead who turned a struggling night shift into the steadiest team.\n"` → `find_marked_text(resume, marked, kept) == ['- "She turned a struggling night shift into the steadiest team in the building."']`.
- `test_find_ignores_runs_shorter_than_six_words` — resume containing `"struggling night shift into the"` only (5 words) → `[]`.
- `test_find_ignores_text_also_in_kept_master` — marked line `- "Led the night shift through a site move without missing a delivery date."` (also in Experience) copied into the resume → `[]`.
- `test_find_ignores_attribution_line` — resume containing `"A. Colleague, Director"` → `[]`.
- `test_find_matches_across_case_quotes_and_emphasis` — resume has `**Turned a struggling Night Shift into the “steadiest” team**` → the quote line is flagged.
- `test_find_with_empty_marked_text` — `find_marked_text("anything", "", MASTER) == []`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_resume_markers.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'findajob.prep.resume_markers'`

- [ ] **Step 3: Implement the module**

`normalize_for_match`: `unicodedata.normalize("NFKC", text)`; map `‘ ’` → `'` and `“ ”` → `"`; map `–`, `—`, `−` → `-`; delete `*`, `_` and `` ` ``; `casefold()`; collapse every whitespace run (newlines included) to one space; `strip()`.

`split_marked_sections`: iterate `master_text.splitlines(keepends=True)`; a heading is a line whose `rstrip("\r\n")` matches `^(#{1,6})\s` while not inside a ```` ``` ```` fence (toggle on lines starting with ```` ``` ````); its level is the `#` count. Decide marking per section as Decision 2 states (test `MARKER_RE` against the heading line and against the first non-blank following line, each with `*` and `_` removed). Collect marked lines into `marked` and all other lines into `kept`, both joined with `"".join` so line endings survive. When nothing is marked, return `(master_text, "")`.

`find_marked_text`: tokens are `re.findall(r"[a-z0-9]+(?:'[a-z0-9]+)*", normalize_for_match(s))`. Build the set of `MIN_RUN_WORDS`-token tuples for the resume and for `kept_text`. Candidate lines are the non-blank lines of `marked_text` that are not headings and do not match `MARKER_RE`. A line is flagged when one of its runs is in the resume set and not in the kept set. Return flagged lines `.strip()`-ed, in document order, without duplicates.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_resume_markers.py -v && uv run mypy src/findajob/prep/resume_markers.py`
Expected: all PASS; mypy `Success`.

- [ ] **Step 5: Commit**

```bash
git add src/findajob/prep/resume_markers.py tests/test_resume_markers.py
git commit -m "feat(prep): split master-resume sections marked not for submitted resumes (#1098)"
```

Commit body: what the convention is (Decision 2), that detection needs six consecutive words absent from the kept master, and that paraphrase is not detected.

---

### Task 2: Phase B uses the split; the tailored resume is checked

**Files:**
- Modify: `src/findajob/prep/orchestrator.py` (`_run_prep_phase_b`: master read `:635-652`, tailor stage `:671-711`, checklist `:773-794`)
- Test: `tests/test_prep_phase_split.py` (new section `# #1098 — marked master-resume sections`)

**Interfaces:**
- Consumes: Task 1's `split_marked_sections`, `find_marked_text`, `MARKER_RE`.
- Produces: local `kept_master: str` in `_run_prep_phase_b`, used by Task 5. Events `resume_marked_content` (`severity="HIGH"`, `count`, `company`, `title`, `job_id`) and `master_resume_marker_unrecognized` (`company`, `title`, `job_id`).

- [ ] **Step 1: Write the failing tests.** Add a recording fake: `calls: list[tuple[str, str, dict]]` filled by a `run_role` stand-in that delegates to `_fake_run_role`; seed the isolated master with a marked section (`"## Selected Quotes\n*(not for submitted resumes)*\n- \"Turned a struggling night shift into the steadiest team in the building.\"\n"` after `FAKE_MASTER_RESUME`). Run Phase A then Phase B as `test_phase_b_transitions_to_materials_drafted` does.
  - `test_tailor_prefix_excludes_marked_section` — the `resume_tailor` call's `cached_prefix` contains `"History here."` and not `"steadiest team"`.
  - `test_cover_letter_prefix_extends_tailor_prefix_and_has_marked_text` — `cover_prefix.startswith(tailor_prefix)` and `"steadiest team" in cover_prefix`.
  - `test_briefing_writer_still_gets_marked_section` — Phase A's `briefing_writer` `cached_prefix` contains `"steadiest team"`.
  - `test_no_marker_keeps_phase_b_prefixes_unchanged` — with the default `FAKE_MASTER_RESUME`, the tailor `cached_prefix` equals `f"CANDIDATE PROFILE:\n{FAKE_PROFILE}\n\nMASTER RESUME:\n{FAKE_MASTER_RESUME}\n\nCompany: {COMPANY}\nTitle: {TITLE}\n\nJD:\n{FAKE_JD}\n\n---\n\n"` and the cover letter's equals that plus `"---\n\n"`.
  - `test_copied_marked_text_is_logged_high_and_in_checklist` — fake `resume_tailor` returns `"# Resume\n\n## Summary\nTurned a struggling night shift into the steadiest team.\n" + "x" * 600`; `_events(isolate_event_log)` has one `resume_marked_content` event with `severity == "HIGH"` and `count == 1`; the `Review Checklist - *.md` file contains `"Fix before sending"` and `"steadiest team"`.
  - `test_clean_resume_logs_no_marked_content_event` — default fake tailor output → no `resume_marked_content` event and no `"Fix before sending"` in the checklist.
  - `test_unrecognized_marker_is_logged` — master whose `## Experience` section has the marker phrase in its second bullet (not the first line under the heading) → one `master_resume_marker_unrecognized` event.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_prep_phase_split.py -v -k "marked or marker or prefix"`
Expected: FAIL (prefix still holds the marked text; no events).

- [ ] **Step 3: Implement in `_run_prep_phase_b`**
  - After reading `master_text`: `kept_master, marked_master = split_marked_sections(master_text)`; if `MARKER_RE.search(kept_master)`, log `master_resume_marker_unrecognized`.
  - `shared_candidate_jd` uses `kept_master`. Build `marked_section = "MASTER RESUME SECTIONS NOT FOR SUBMITTED RESUMES (the cover letter may use these; the resume must not):\n" + marked_master + "\n\n"` when `marked_master`, else `""`; `shared_with_voice = f"{shared_candidate_jd}{voice_section}{marked_section}---\n\n"`.
  - After `_linkify_contact_info`, before the file write: `marked_hits = find_marked_text(resume_md, marked_master, kept_master) if marked_master else []`; when non-empty log `resume_marked_content`.
  - In the checklist, when `marked_hits`, insert before `## Before sending, complete these steps:` (same 4-space indent as its neighbours) a block: `## Fix before sending`, then `- [ ] The resume contains text from a master-resume section marked "not for submitted resumes". Remove it:`, then one `  - <hit[:120]>` line per hit.
  - Leave the `validate_resume.py` subprocess check as it is.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_prep_phase_split.py tests/test_prep_pipeline.py tests/test_prep_phase_b_progress.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/findajob/prep/orchestrator.py tests/test_prep_phase_split.py
git commit -m "fix(prep): resume_tailor never receives master-resume sections marked not for submitted resumes (#1098)"
```

Commit body: tailor and reviewer get the kept master; the cover letter gets marked sections after the voice samples, so the shared prefix and its cache hit stay; a copied marked passage logs a HIGH `resume_marked_content` event and leads the review checklist; Phase A unchanged.

---

### Task 3: Warning on the materials folder page

**Files:**
- Modify: `src/findajob/web/routes/materials.py` (new helper; `folder_view` context)
- Modify: `src/findajob/web/templates/materials/folder.html` (after `</header>`, before the `{% if is_briefing_gate %}` block)
- Test: `tests/test_materials_marked_content_banner.py`

**Interfaces:**
- Consumes: Task 1's `split_marked_sections`, `find_marked_text`.
- Produces: `_marked_content_warnings(folder: Path, base_root: Path) -> list[dict[str, Any]]` — one `{"file": str, "lines": list[str]}` per Resume `.md` that holds marked text; template variable `marked_content_warnings`.

- [ ] **Step 1: Write the failing route tests.** Build the client as the `folder_client` fixture in `tests/test_materials_folder_banner.py` does (`create_app(companies_root=..., db_path=..., base_root=tmp_path)`, `mark_complete(tmp_path)`), and write `tmp_path / "candidate_context" / "master_resume.md"` with Task 1's `MASTER`. Resume file name: `Candidate Resume - Acme - Shift Lead - 20261021-120000.md`.
  - `test_warning_lists_copied_marked_text` — resume holds `"turned a struggling night shift into the steadiest team"` → status 200; body contains `"Remove marked text before you send this resume"`, the resume file name, and `"steadiest team in the building"`.
  - `test_no_warning_for_clean_resume` — body lacks `"Remove marked text"`.
  - `test_no_warning_when_master_resume_missing` — no master file → 200, no warning.
  - `test_resume_changes_file_is_not_checked` — copied text only in `Candidate Resume Changes - Acme - Shift Lead - 20261021-120000.md` → no warning.
  - `test_applied_snapshot_is_not_checked` — copied text only in `... .applied-2026-10-21.md` → no warning.
  - `test_warning_escapes_marked_text` — marked quote `"Keeps <b>calm</b> during every single overnight cutover window."` copied into the resume → body contains `&lt;b&gt;calm&lt;/b&gt;` and not `<b>calm</b>`.

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_materials_marked_content_banner.py -v`
Expected: FAIL on the positive and escape tests (no warning rendered).

- [ ] **Step 3: Implement**
  - `_marked_content_warnings`: read `base_root / "candidate_context" / "master_resume.md"` (missing or `OSError` → `[]`); split; no marked text → `[]`. For each `p` in `sorted(folder.iterdir())` that is a file with suffix `.md`, `_classify_file(p.name)[0] == "Resume"` and no `_HIDE_FROM_VIEW_RE` match, read it (`encoding="utf-8", errors="replace"`; `OSError` → skip) and run `find_marked_text`; keep hits as `h[:160]`.
  - `folder_view`: add `"marked_content_warnings": _marked_content_warnings(folder, request.app.state.base_root)`.
  - Template: `<aside role="alert" class="mb-5 border-2 border-rose-300 bg-rose-50 rounded-md px-4 py-3">` with `<h2 class="text-base font-semibold text-rose-900">Remove marked text before you send this resume</h2>`, the sentence `Your master resume marks some sections “not for submitted resumes”. This resume contains text from them:`, then per warning the file name (`font-mono text-xs`) and a `<ul class="list-disc ml-5 text-sm">` of its lines.

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/test_materials_marked_content_banner.py tests/test_materials_folder_banner.py tests/test_materials_grouping.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/findajob/web/routes/materials.py src/findajob/web/templates/materials/folder.html tests/test_materials_marked_content_banner.py
git commit -m "feat(materials): warn when a resume holds text marked not for submitted resumes (#1098)"
```

Commit body: computed at view time from the current master and resume files, so an edit that removes the passage clears the warning; fails open.

---

### Task 4: `change_report` — line diff, claim check, rendering

**Files:**
- Create: `src/findajob/prep/change_report.py`
- Test: `tests/test_change_report.py`

**Interfaces:**
- Consumes: Task 1's `normalize_for_match`; `findajob.llm_parsing.extract_json_payload`.
- Produces:
  - `resume_diff_summary(master_text: str, tailored_text: str) -> str`
  - `@dataclass(frozen=True) class CheckedReport: markdown: str; claims: int; unsupported: int; parse_failed: bool`
  - `check_change_report(raw_output: str, master_text: str, tailored_text: str) -> CheckedReport`
  - `PARSE_FAILED_NOTE: str = "The change reviewer's output could not be parsed, so none of it was checked. Treat every entry below as unverified."`

- [ ] **Step 1: Write the failing tests.** Synthetic master `"# J\n## Summary\nSteady shift lead.\n## Experience\n- Ran inventory counts weekly.\n- Trained new staff.\n"` and tailored `"# J\n## Experience\n- ran inventory counts weekly.\n## Summary\nShift lead focused on safety.\n"`.
  - `test_diff_summary_sections_and_only_in_lines` — output contains `"SECTION ORDER IN MASTER: Summary | Experience"`, `"SECTION ORDER IN TAILORED: Experience | Summary"`, `"+ Shift lead focused on safety."`, `"- Steady shift lead."`, `"- - Trained new staff."`; does not contain `"inventory counts"` (same line after normalising case and bullet).
  - `test_diff_summary_empty_lists_say_none` — identical inputs → both line lists render `(none)`.
  - `test_added_claim_absent_from_tailored_is_unverified` — fabricated report (the #1079 shape) `{"added": [{"text": "certified forklift trainer", "section": "Summary", "reason": "JD term"}]}` → `claims == 1`, `unsupported == 1`, markdown contains `'- [UNVERIFIED] "certified forklift trainer"'`.
  - `test_added_claim_present_is_verified` — `"text": "Shift lead focused on **safety**"` with a curly apostrophe/emphasis variant still matches → `unsupported == 0`, markdown contains `'- "Shift lead focused on **safety**"'`.
  - `test_removed_claim_still_in_tailored_is_unverified` — `removed.text = "Ran inventory counts weekly."` → unsupported.
  - `test_removed_claim_not_in_master_is_unverified` — `removed.text = "Managed a fleet of trucks."` → unsupported.
  - `test_removed_claim_valid` — `removed.text = "Trained new staff."` → verified.
  - `test_reworded_needs_before_in_master_and_after_in_tailored` — valid pair verified; pair whose `after` is absent → unsupported.
  - `test_missing_info_placeholder_must_be_in_tailored` — `"[MISSING: start date]"` absent → unsupported.
  - `test_empty_lists_are_a_pass` — `{"reordered_sections": [], "added": [], "reworded": [], "removed": [], "missing_info": []}` → `claims == 0`, `unsupported == 0`, `parse_failed is False`, markdown contains `"## Added\n- None"`.
  - `test_fenced_json_is_accepted` — the same object inside a ```` ```json ```` fence → `parse_failed is False`.
  - `test_unparseable_output_falls_back` — `"REORDERED_SECTIONS: none"` → `parse_failed is True`, markdown starts with `"# Resume Changes\n\n" + PARSE_FAILED_NOTE` and contains the raw text.
  - `test_non_dict_item_is_unverified` — `{"added": ["just a string"]}` → `unsupported == 1`.

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_change_report.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement**

`resume_diff_summary`: section order = text of `## ` lines in order, joined `" | "`. Line identity = `normalize_for_match` of the line after stripping leading `#`, `-`, `*`, `+` and `\d+\.` markers; skip lines whose identity is empty. "Only in tailored" = tailored lines whose identity is not among master identities (and the reverse); keep document order; render each as `+ <line.strip()>` / `- <line.strip()>`. Exact layout:

```
SECTION ORDER IN MASTER: <...>
SECTION ORDER IN TAILORED: <...>

LINES ONLY IN TAILORED (new or reworded):
<+ lines or (none)>

LINES ONLY IN MASTER (removed or reworded):
<- lines or (none)>
```

`check_change_report`: `json.loads(extract_json_payload(raw_output))`; anything but a `dict` → parse failure. A missing or non-list field counts as `[]`. Rules (`found(x, doc)` = normalized `x` non-empty and a substring of normalized `doc`):
- `added`: `found(text, tailored)`
- `reworded`: `found(before, master)` and `found(after, tailored)`
- `removed`: `found(text, master)` and not `found(text, tailored)`
- `missing_info`: `found(placeholder, tailored)`
- `reordered_sections`: rendered, not counted as claims.

Render `# Resume Changes`, a one-paragraph note (`Every quoted passage below was checked against the master and tailored resumes. A line marked [UNVERIFIED] quotes text the check could not find in the right document: do not rely on it.`), then `## Reordered sections`, `## Added`, `## Reworded`, `## Removed`, `## Missing info` in that order. Line formats: `- {section}: {note}`; `- "{text}" ({section}): {reason}`; `- Before: "{before}" / After: "{after}" ({section})`; `- "{text}": {reason}`; `- {placeholder}: {needs}`. Omit an empty `({section})` or `: {reason}` part. An unsupported claim's line starts `- [UNVERIFIED] `. An empty field renders `- None`. Each heading is followed directly by its lines; one blank line separates sections.

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/test_change_report.py -v && uv run mypy src/findajob/prep/change_report.py`
Expected: all PASS; mypy `Success`.

- [ ] **Step 5: Commit**

```bash
git add src/findajob/prep/change_report.py tests/test_change_report.py
git commit -m "feat(prep): check every passage a resume change report quotes (#1099)"
```

Commit body: the diff gives the reviewer ground truth (section order, lines only in one document); each quoted passage is checked against the document its field names; unfound quotes are marked, not dropped, so the defect stays visible.

---

### Task 5: The reviewer gets the diff and returns JSON

**Files:**
- Modify: `config/roles/resume_change_reviewer.md` (body only; keep the frontmatter)
- Modify: `src/findajob/prep/orchestrator.py` (stage 5 `:713-719`; checklist line about the changes file)
- Test: `tests/test_resume_change_reviewer_role_prompt_invariants.py` (new), `tests/test_prep_phase_split.py`

**Interfaces:**
- Consumes: Task 2's `kept_master`; Task 4's `resume_diff_summary`, `check_change_report`.
- Produces: event `change_report_checked` (`company`, `title`, `job_id`, `claims`, `unsupported`, `parse_failed`).

- [ ] **Step 1: Write the failing tests**
  - Role-prompt invariants: frontmatter still `model: openrouter:google/gemini-3-flash-preview` and `temperature: 0.1`; body contains `"LINE DIFF"`, `"character for character"`, `"Return only one JSON object"`, and each key `"reordered_sections"`, `"added"`, `"reworded"`, `"removed"`, `"missing_info"`; body no longer contains `"ADDED_KEYWORDS"`.
  - `test_reviewer_gets_kept_master_and_line_diff` — with the marked master from Task 2, the `resume_change_reviewer` prompt contains `"LINE DIFF:\n"` and `"SECTION ORDER IN MASTER:"`, and not `"steadiest team"`.
  - `test_fabricated_claim_is_marked_and_counted` — fake reviewer returns `{"added": [{"text": "certified forklift trainer", "section": "Summary", "reason": "JD"}]}` → the `Resume Changes - *.md` file contains `"[UNVERIFIED]"`; one `change_report_checked` event with `claims == 1`, `unsupported == 1`, `parse_failed is False`.

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_resume_change_reviewer_role_prompt_invariants.py tests/test_prep_phase_split.py -v -k "reviewer or fabricated"`
Expected: FAIL.

- [ ] **Step 3: Implement**
  - Replace the prompt body with this text:

```
You compare an original master resume with a tailored version made for one job application.

You get four inputs: ORIGINAL MASTER RESUME, TAILORED RESUME, LINE DIFF and TARGET JD.
The LINE DIFF is computed by code and is correct. It gives the section order of both
documents and every line that is in only one of them. Report only changes that the
LINE DIFF shows.

Copy every quoted passage character for character from the document named for its
field. Code checks each quote, and a quote it cannot find is marked UNVERIFIED in the
report the candidate reads.

Return only one JSON object, with no text before or after it:

{
  "reordered_sections": [{"section": "...", "note": "why it moved"}],
  "added": [{"text": "exact text from TAILORED RESUME", "section": "...", "reason": "JD term or purpose"}],
  "reworded": [{"before": "exact text from ORIGINAL MASTER RESUME", "after": "exact text from TAILORED RESUME", "section": "..."}],
  "removed": [{"text": "exact text from ORIGINAL MASTER RESUME", "reason": "..."}],
  "missing_info": [{"placeholder": "exact [MISSING: ...] text from TAILORED RESUME", "needs": "..."}]
}

Use an empty list for a field with nothing to report. Quote the shortest passage that
shows each change. When a whole role or section was dropped, report it once, quoting
its heading.
```

  - Stage 5: `diff_summary = resume_diff_summary(kept_master, resume_md)`; prompt `f"ORIGINAL MASTER RESUME:\n{kept_master}\n\nTAILORED RESUME:\n{resume_md}\n\nLINE DIFF:\n{diff_summary}\n\nTARGET JD:\n{jd_text}"`; `checked = check_change_report(raw, kept_master, resume_md)`; log `change_report_checked`; write `checked.markdown`.
  - Checklist line for the changes file: `- [ ] Open \`{fn["changes_md"]}\` — review every change; a line marked [UNVERIFIED] was not found in the documents`.

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/test_resume_change_reviewer_role_prompt_invariants.py tests/test_prep_phase_split.py tests/test_change_report.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add config/roles/resume_change_reviewer.md src/findajob/prep/orchestrator.py tests/test_resume_change_reviewer_role_prompt_invariants.py tests/test_prep_phase_split.py
git commit -m "fix(prep): resume change report explains a computed diff and marks unfound quotes (#1099)"
```

Commit body: the reviewer gets the master without marked sections plus the line diff and returns JSON; `change_report_checked` logs claims and unsupported counts on every run; the `Resume Changes` file keeps its name and place.

---

### Task 6: Documentation and CHANGELOG

**Files:**
- Modify: `docs/operations/config-reference.md` (§ `candidate_context/master_resume.md`)
- Modify: `docs/architecture.md` (`:149` row; `:175` bullet if it still calls the output a plain diff)
- Modify: `docs/architecture/scripts.md` (`:32-39` output list)
- Modify: `docs/architecture/file-map.md` (after the `prep/briefing.py` line)
- Modify: `docs/usage.md` (`:209` file table — add or correct the change-report row)
- Modify: `CHANGELOG.md` (`## [Unreleased]`)

- [ ] **Step 1: Write the doc changes** (content under **Documentation Impact**).
- [ ] **Step 2: Verify**

Run: `grep -n "not for submitted resumes" docs/operations/config-reference.md && grep -n "UNVERIFIED" docs/architecture.md CHANGELOG.md && grep -n "resume_markers\|change_report" docs/architecture/file-map.md`
Expected: each grep prints at least one line.

- [ ] **Step 3: Commit**

```bash
git add docs/ CHANGELOG.md
git commit -m "docs: marked master-resume sections and the checked change report (#1098, #1099)"
```

Commit body: the master-resume marker convention and its limits; what the change report can be trusted for; file map and output-name corrections.

---

## Documentation Impact

- `docs/operations/config-reference.md` § `master_resume.md` — new paragraph: how to mark a section (heading or the first line under it contains "not for submitted resumes"; the whole section and its sub-sections are covered); the resume tailor never sees it; the cover letter, outreach and interview prep still use it; a tailored resume that copies six or more consecutive words from it gets a warning on its materials page and in the review checklist; a reworded version is not detected; a marker elsewhere is not honoured and logs `master_resume_marker_unrecognized`.
- `docs/architecture.md:149` — inputs become "master resume without marked sections + tailored resume + computed line diff + JD"; output "`Resume Changes` — explanation of the computed diff; every quoted passage checked, unfound ones marked `[UNVERIFIED]`". This answers #1099's 2026-09-24 comment (audit row L168): the report can be trusted for what its verified quotes show, not for completeness.
- `docs/architecture/scripts.md:32-39` — the output list uses names that `prep_naming.build_prep_filenames` no longer produces; replace them with the current patterns (`{Prefix} Resume - …`, `{Prefix} Resume Changes - …`, `Review Checklist - …`).
- `docs/architecture/file-map.md` — two lines: `src/findajob/prep/resume_markers.py` (split marked master-resume sections; detect copied text, #1098) and `src/findajob/prep/change_report.py` (line diff for the change reviewer; check and render its report, #1099).
- `docs/usage.md:209` — the file table's change-report row says the report marks unfound quotes `[UNVERIFIED]`.
- `CHANGELOG.md` `## [Unreleased]` → `### Fixed`: one entry for #1098 (marked sections never reach the tailor; warning on the materials page and checklist) and one for #1099 (report built from a computed diff; quotes checked; `change_report_checked` event). No migration, no config change, no new env var.
- `docs/architecture/models-and-paths.md` — none: no model or path changes.
- `AGENTS.md` / `CLAUDE.local.md` / `README.md` / `docs/getting-started/` — none.

## Verification gate

1. The four CI commands pass: `uv run ruff check src/ scripts/ tests/`, `uv run ruff format --check src/ scripts/ tests/`, `uv run mypy src/findajob/`, `uv run pytest tests/ -v`.
2. **Real data, with the operator's approval** (Decision 5). Copy read-only from the operator's stack into the session scratchpad, never into the repo: `master_resume.md` and, from two recent prep folders, the newest `Resume - *.md` and the `JD - *.txt`. Then, with `JSP_BASE` pointing at a scratch BASE that holds a copy of the branch's `config/roles/`:
   - a. `split_marked_sections` on the real master: report the kept and marked line counts and the marked headings. Expected: exactly the section(s) the operator marked.
   - b. Known positive: copy one sentence from a marked section into a copy of a real tailored resume → `find_marked_text` returns that line. Known negatives: each real tailored resume as it is → report the hit count; any hit is read and explained before going on.
   - c. Run `resume_change_reviewer` (via `run_role`, `conn=None`) and `check_change_report` on both postings. Report `claims`, `unsupported` and `parse_failed` per posting. Expected: `unsupported == 0`, `parse_failed is False`. Hand-check three verified claims per posting with `grep -F` against the files. Cost: two Gemini Flash calls, under $0.05.
   - d. Delete the scratch copies. The PR records counts only, no candidate text.
3. If 2c gives `unsupported > 0`, read the unverified lines. A normalisation gap gets a fix and a test. A model that keeps inventing quotes stays visible as `[UNVERIFIED]` lines (the defect is now visible, which the issue accepts); record the count in the PR and the model-swap follow-up on #1099. If the real run cannot happen, open the PR with this item unticked and the reason (see Decision 5); #1098 does not depend on it.

## Self-review checklist

**Spec coverage**

| Requirement | Task |
|---|---|
| #1098 documented marker convention covering the prose marker | 1, 6 |
| #1098 tailor never receives marked blocks (unit test) | 2 (`test_tailor_prefix_excludes_marked_section`) |
| #1098 other roles still receive marked content (test) | 2 (cover letter, briefing writer) |
| #1098 copied marked text is a HIGH violation, visible to the operator (test) | 2 (event, checklist), 3 (route test) |
| #1098 CHANGELOG and docs | 6 |
| #1099 reviewer prompt includes a deterministic diff | 4, 5 |
| #1099 unsupported added / removed claims marked (fabricated-report tests) | 4 |
| #1099 unsupported claims counted in a logged event | 5 (`change_report_checked`) |
| #1099 real-posting re-run with zero unsupported claims | Verification gate 2c |
| #1099 CHANGELOG | 6 |

**Placeholder scan:** no `TBD` / `TODO`; every function a task uses is defined in an Interfaces block.

**Type consistency:** `split_marked_sections -> tuple[str, str]` (Tasks 1, 2, 3); `find_marked_text(resume_text, marked_text, kept_text) -> list[str]` (Tasks 1, 2, 3); `check_change_report(raw_output, master_text, tailored_text) -> CheckedReport` with the master argument always the kept master (Tasks 4, 5).
