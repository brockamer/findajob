# Walkthrough Replay Corpus — Avery Chen (fictional persona)

Generated: synthetic — not from any real walkthrough run.

This corpus drives the harness's replay loop (`scripts/walkthrough_harness.py
--replay-from <this file>`). Every USER turn is a fictional answer from a
fictional person; no real candidate has ever submitted any of this content
to the pipeline. The persona is intentionally **non-tech** (a clinical
pharmacist transitioning to clinical informatics) so the corpus exercises
the prompt's field-agnostic claim under stress — if a prompt revision
narrows the question shape toward tech-only assumptions, replay against
this corpus will surface mismatches loudly.

## Re-baseline workflow

When the onboarding prompt revisions noticeably shift question shape:

1. Run the harness against the new prompt with this corpus and produce a
   transcript. The transcript will have `Skip — using prior context` user
   messages on questions where the corpus drifted.
2. Hand-edit those `Skip — using prior context` lines in the freshly
   produced transcript to be substantive answers in Avery Chen's voice.
3. Save the edited transcript back over this file. The next harness run
   matches positionally again and the drift is reset.

Do NOT promote a real operator's walkthrough transcript over this file —
those contain operator PII and should never enter the repo.

---

## Turn 1 — ASSISTANT

Hi — I'm going to help you set up findajob, a tool that triages job postings for you every day so you only look at the ones worth looking at. To get there I need to learn who you are, what you're looking for, and how you talk about your own work. We'll go through five phases. Plan for about 60–90 minutes end to end.

A few ground rules before we start: this is a conversation — push back on any question you find confusing. You can ask "why are you asking this?" at any time. Toward the end I'll review what I've captured in four groups, one at a time.

Ready to move on? Let's get your resume into the picture.

## Turn 1 — USER

Ready when you are.

## Turn 2 — ASSISTANT

Great — let's move to Phase 2. Paste your resume into the chat — copy it from the source document (Word, Google Docs, PDF, LinkedIn export, plain text — whatever you've got) and drop it in here. If you have performance reviews, 360s, or self-assessments where someone has described your work in their own words, paste those too in follow-up messages.

## Turn 2 — USER

Avery Chen, Pharm.D., BCPS — Minneapolis, MN
avery.chen.pharmd@example.com  •  555-0142  •  linkedin.com/in/avery-chen-pharmd-example

Summary
Hospital clinical pharmacist with eight years of acute-care practice and a residency-trained background in medication safety. Recent focus on the seam between pharmacy practice and the EHR — built order-set governance and CPOE rule logic for a 600-bed academic medical center, partnered with informatics on alert-fatigue reduction projects. Looking to move full-time into clinical informatics where pharmacy expertise translates to system design rather than dispensing throughput.

Experience

Senior Clinical Pharmacist — Lakefront Medical Center, Minneapolis MN — 2022–present
- Lead pharmacist for the inpatient antimicrobial stewardship program; reduced broad-spectrum DOT (days of therapy) by 14% across two service lines through provider education and order-set redesign.
- Co-chair of the Pharmacy & Therapeutics order-set subcommittee; rewrote 40+ legacy order sets to incorporate weight-based dosing and renal-function logic in CPOE.
- Project lead on the 2024 alert-fatigue reduction initiative — partnered with informatics analysts to retire 1,200 low-yield interruptive alerts; clinician override rate fell from 91% to 67% on the remaining set.
- Precept PGY-1 residents on a clinical informatics elective rotation (2 residents/year).

Clinical Pharmacist — Lakefront Medical Center, Minneapolis MN — 2018–2022
- Front-line acute-care coverage across general medicine, ICU, and oncology service lines.
- Served on the medication-error review committee; co-authored a workflow change that eliminated a class of pediatric heparin dosing errors traced to a unit-conversion ambiguity.

PGY-1 Pharmacy Practice Resident — Saint Aldhelm Hospital, Madison WI — 2017–2018
- Standard ASHP-accredited residency with required and elective rotations across acute care, ambulatory care, and informatics.

Education
Doctor of Pharmacy (Pharm.D.) — University of Minnesota — 2017
B.S. Biochemistry — Carleton College — 2013

Credentials
Board Certified Pharmacotherapy Specialist (BCPS) — 2020
Active pharmacist license in Minnesota and Wisconsin
Epic Willow Inpatient — proficiency self-attested; no formal Epic certification yet

## Turn 3 — ASSISTANT

Thanks for that. Do you have any performance reviews, 360s, or self-assessments where someone has described your work in their own words? Paste them as follow-ups if so — they're invaluable for capturing your voice. If not, no problem — we can move on.

## Turn 3 — USER

Two paste-backs, both from my 2024 annual review.

From my director: "Avery is the person other pharmacists go to when something is unclear in CPOE. She bridges the gap between what bedside clinicians need and what the EHR enforces, and she does it without making either side feel talked down to. Her work on the alert-fatigue project this year was the most important non-clinical contribution from the pharmacy department. She has earned the trust of informatics and physician colleagues to a degree that's unusual for a staff pharmacist."

From a peer (an informatics analyst): "Avery doesn't speak to me like I'm an outsider — she explains the clinical reasoning behind every change request, which makes my own work better. She's the rare clinician who actually wants to understand why the system behaves the way it does, and she's patient about it. I would hire her into my team if she had the title, no hesitation."

## Turn 4 — ASSISTANT

This is great context. Let me read it back to make sure I have the picture right:

You're a senior clinical pharmacist with eight years of hospital-based practice, including a residency, board certification, and a recent move toward the clinical-informatics seam — order-set governance, CPOE rule design, and a successful alert-fatigue reduction effort that visibly shifted clinician behavior. The reviews call out that you bridge the pharmacy / informatics divide without condescension and that informatics colleagues already see you as functionally on their team.

You're now looking to make that pivot formal — informatics title, informatics scope, less direct dispensing.

Does that summary feel accurate? Tell me what I got right, what I got wrong, and anything important I missed.

## Turn 4 — USER

That's accurate, with one addition: please make sure my Epic Willow note stays in. I haven't done formal Epic certification yet because my employer wouldn't pay for it without an informatics title, but I'm self-taught proficient. If you frame me as Epic-naive, that misrepresents me — informatics roles often require Epic certification within 12 months and I want recruiters to know I'd take that on day one.

## Turn 5 — ASSISTANT

Got it — I'll preserve the Epic Willow self-attestation language and add framing that you're certification-ready on hire. Now let's move to Phase 3 — gap-fill interview. Starting with the basics: what's your name and what should I put on your resume?

## Turn 5 — USER

Name as on resume: Avery Chen, Pharm.D., BCPS. Email is avery.chen.pharmd@example.com — please use that one for outreach, not the personal one. Phone 555-0142. LinkedIn URL is linkedin.com/in/avery-chen-pharmd-example. Based in Minneapolis, MN — open to remote, hybrid in the Twin Cities, or willing to relocate within the Upper Midwest for the right informatics role.

## Turn 6 — ASSISTANT

Now your target role — what shape of informatics role are you looking for? Pharmacy informatics specifically (Pharmacist Informaticist titles), broader clinical informatics, application analyst roles, or something else? And what level — staff, lead, manager?

## Turn 6 — USER

Primary target: Pharmacy Informaticist or Clinical Informatics Pharmacist (those titles vary by health system). Open to broader Clinical Informatics Specialist titles when the role is pharmacy-adjacent and would still let me use my clinical license. Level: senior individual contributor or first-line lead — I'm not chasing director titles yet. Not interested in pure application-analyst roles where the clinical license is decorative; I want my Pharm.D. to be load-bearing.

## Turn 7 — ASSISTANT

Helpful. Target employers — any specific health systems, vendors, or types of organizations you're focused on? And the inverse — anywhere you definitely don't want to apply?

## Turn 7 — USER

Target organizations:
- Academic medical centers in the Upper Midwest (Mayo, M Health Fairview, Allina, Hennepin, Essentia, Marshfield, UW Health)
- Large regional integrated systems (Sanford, Avera, Children's Minnesota)
- EHR vendor health-system partnerships, especially Epic-shop systems
- Open to: VA hospitals, IHS facilities, health-system-owned ACOs

NOT interested in:
- Retail pharmacy chains
- PBMs (pharmacy benefit managers) — different work entirely
- Pharma manufacturers — wrong direction from my pivot
- Roles framed as "clinical pharmacist who also does informatics on the side" — that's my current job

## Turn 8 — ASSISTANT

What about gaps in your work history, breaks, or anything else a recruiter might ask about that's not on the resume? Anything you'd want me to frame proactively rather than have come up as a question?

## Turn 8 — USER

No employment gaps. The thing recruiters sometimes pattern-match on with a pharmacy-to-informatics pivot is "are you really sure you don't want to stay clinical?" — and I am sure. I've been doing the informatics-adjacent work without the title for two years; this is not a pivot I'm trying on, it's a pivot I've already half-completed and need to formalize.

## Turn 9 — ASSISTANT

Last few formatting questions before we move on. Resume length preference — single page, two pages, or whatever fits? Cover letter style — formal, conversational, or a third option? Anything specific about your voice you want preserved?

## Turn 9 — USER

Resume: two pages is fine — I'd rather have one slightly long resume than one that buries the alert-fatigue project in a single bullet. Cover letter: conversational but substantive — I read a lot of formal cover letters that say nothing and I write better than that. Voice: avoid the word "passionate." Avoid "leverage" as a verb. Don't pile up action verbs at the start of every bullet — vary the sentence structure. Use specific numbers when I gave you specific numbers (don't round 14% to "around 15%"). When in doubt, sound like a thoughtful clinician, not like an MBA application.

## Turn 10 — ASSISTANT

Excellent. Now let's move on to Phase 4 — translation and review. I'll walk through some categories of jobs that often turn up in the daily triage that I want to get your read on, one at a time. First: hospital staff-pharmacist openings — straight bedside clinical pharmacy work, no informatics scope. These will keyword-match your background since you've been doing the work for years. Should I keep them, relax to only "Senior" or "Lead" titles, or drop them entirely?

## Turn 10 — USER

Drop them entirely. Even Senior Staff Pharmacist roles — those are lateral or backward. Anything titled "Pharmacist" without "Informatics", "Clinical Informatics", "CMIO support", "Order set", or "EHR" in it should be excluded.

## Turn 11 — ASSISTANT

Got it. Next: PBM and managed-care-pharmacist roles — UnitedHealthcare, CVS Health, Caremark, Express Scripts, Optum. These will surface because they hit "pharmacist" + "remote" + "Minneapolis" filters. Keep, relax, or drop?

## Turn 11 — USER

Drop. PBM work is a different career and I have zero interest. Add explicit exclusion patterns for the company names so they don't keep appearing.

## Turn 12 — ASSISTANT

Pharmaceutical industry roles — Medical Science Liaison, Medical Affairs, Pharmacovigilance. These match Pharm.D. + BCPS but go in the opposite direction from informatics. Keep, relax, or drop?

## Turn 12 — USER

Drop. Same reasoning — wrong direction.

## Turn 13 — ASSISTANT

Now the inverse — patterns I should keep that the title-only filter might miss. What positive patterns should I treat as in-domain even when the title isn't an exact match?

## Turn 13 — USER

In-domain title patterns I want to KEEP even when "pharmacist" is missing from the title:
- Clinical Informaticist, Clinical Informatics Specialist, Clinical Informatics Lead
- CPOE Analyst (when it's a clinical role, not pure IT)
- Medication Safety Officer
- Order Set Lead, Order Set Governance Lead
- Epic Willow Application Analyst (with clinical-license-preferred language)
- CMIO support roles, Physician Informatics support roles where pharmacy is in scope
- Antimicrobial stewardship coordinator with EHR-system component

Plus any title with "informatics" + a clinical specialty word.

## Turn 14 — ASSISTANT

Excellent — that's exactly the shape I needed. Anything about ntfy notifications I should set up? You'll get a daily summary push notification when triage finishes; what topic name do you want?

## Turn 14 — USER

Topic: avery-pharmd-informatics-2026

## Turn 15 — ASSISTANT

And confirm timezone — I'll use America/Chicago for Minneapolis unless you tell me otherwise.

## Turn 15 — USER

Confirmed — America/Chicago.

## Turn 16 — ASSISTANT

Now Phase 5 — self-check and emission. I'll review what I've captured in four groups (identity, targeting, filters, writing voice), one at a time. Reply **next** to continue, or **redo {a|b|c|d}** to fix something in that group.

Group **a — Identity**:
- Name: Avery Chen, Pharm.D., BCPS
- Email: avery.chen.pharmd@example.com
- Phone: 555-0142
- LinkedIn: linkedin.com/in/avery-chen-pharmd-example
- Location: Minneapolis, MN
- Timezone: America/Chicago
- ntfy topic: avery-pharmd-informatics-2026

Sound good?

## Turn 16 — USER

next

## Turn 17 — ASSISTANT

Group **b — Targeting**:
- Target roles: Pharmacy Informaticist / Clinical Informatics Pharmacist; broader Clinical Informatics Specialist titles when pharmacy license is load-bearing; senior IC or first-line lead level
- Target organizations: Mayo Clinic, M Health Fairview, Allina Health, Hennepin Healthcare, Essentia Health, Marshfield Clinic, UW Health, Sanford Health, Avera Health, Children's Minnesota; VA hospitals, IHS facilities, health-system-owned ACOs
- Target geographies: Remote (US), hybrid in Twin Cities, willing to relocate within Upper Midwest
- Excluded: retail pharmacy chains, PBMs (UnitedHealthcare/CVS/Caremark/Express Scripts/Optum), pharma manufacturers, "clinical pharmacist who also does informatics" reframings of the current role

Sound good?

## Turn 17 — USER

next

## Turn 18 — ASSISTANT

Group **c — Filters**:
- Hard reject titles matching: Staff Pharmacist (without informatics qualifier), Senior Staff Pharmacist, Retail Pharmacist, Pharmacy Manager (retail), Medical Science Liaison, Medical Affairs Director, Pharmacovigilance Specialist
- Hard reject employers matching: CVS, Walgreens, Rite Aid, Optum Rx, Express Scripts, Caremark
- In-domain positive patterns: any title containing "informatics" plus clinical specialty word; CPOE Analyst; Medication Safety Officer; Order Set Lead; Epic Willow Application Analyst; CMIO/Physician Informatics support; Antimicrobial Stewardship Coordinator (with EHR/system scope)

Sound good?

## Turn 18 — USER

next

## Turn 19 — ASSISTANT

Group **d — Writing voice**:
- Avoid the word "passionate"
- Avoid "leverage" as a verb
- Vary sentence structure — don't pile up action-verb starts on every bullet
- Use specific numbers when given (don't round 14% to "around 15%")
- Sound like a thoughtful clinician, not an MBA application
- Resume: two pages is fine; cover letters: conversational but substantive

Sound good?

## Turn 19 — USER

next

## Turn 20 — ASSISTANT

Great — I'm ready to emit the configuration files. I'll do them in four groups, pausing after each. Reply **next** to continue or **redo {a|b|c|d}** to fix a group.

Group a — Identity files coming up.

[Emits profile.md, master_resume.md, display_name.txt, timezone.txt, ntfy_topic.txt with FILE block delimiters]

That's group **a — Identity**. Reply **next** to continue.

## Turn 20 — USER

next

## Turn 21 — ASSISTANT

Group b — Targeting files.

[Emits target_companies.md, business_sector_employers_reference.md, jsearch_queries.txt]

That's group **b — Targeting**. Reply **next** to continue.

## Turn 21 — USER

next

## Turn 22 — ASSISTANT

Group c — Filters.

[Emits prefilter_rules.yaml, in_domain_patterns.yaml]

That's group **c — Filters**. Reply **next** to continue.

## Turn 22 — USER

next

## Turn 23 — ASSISTANT

Group d — Writing voice. Did you have any voice samples — published writing, blog posts, reviews you've authored, or anything else where I can see your unedited voice — to paste in? Optional but helpful for cover letter calibration.

## Turn 23 — USER

Skip — I'd rather have my cover letters drafted from the resume + the framing notes than risk over-fitting to a small sample. We can revisit later if the cover letters don't sound right.

## Turn 24 — ASSISTANT

Understood — I'll skip the voice samples block. That's all four groups complete. The "Finalize" button at the top of the page should now be active. Click it when you're ready and findajob will write all the files into your stack and start running the pipeline.

## Turn 24 — USER

Done — clicking Finalize now.
