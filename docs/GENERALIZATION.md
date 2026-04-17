# Generalization Tracking — Making findajob Domain-Agnostic

The pipeline was built for a specific use case (data center operations / hardware NPI job
search) but the goal is to make it useful for any job seeker — social workers, teachers,
accountants, designers, operations managers in any field.

This document tracks every piece of the codebase that currently assumes the original
domain. Each item is a future task to make the pipeline domain-neutral.

**Status key:**
- `[ ]` — domain-locked, needs work
- `[~]` — partially generalized (some config-driven, some hardcoded)
- `[x]` — fully generalized

---

## Principles

1. **Logic is generic; lists are config.** Pipeline code should not embed any domain knowledge. Target companies, job titles, search queries, reject patterns — all config-driven.
2. **Prompts reference the profile, not the domain.** Role prompts should instruct the LLM to read the candidate's profile for domain context, not bake in category language.
3. **Examples are generic or plural.** When an example is needed, use a hypothetical candidate ("Jane Smith, senior social worker at a city health department") rather than a real one. Better: show 2-3 examples from different fields.
4. **Profile is the source of truth.** The candidate's `candidate_context/profile.md` carries all domain-specific content — target role, target companies, hard-reject categories, in-domain signals.

---

## Hardcoded Domain Content (needs work)

### Scorer prefilter — `src/findajob/scorer_prefilter.py`

- [x] **`TIER1` frozenset** — dropped entirely in favor of `config/companies_of_interest.txt` (see changelog below). The prefilter no longer consults any company-level list.

- [x] **`_HARD_REJECT_PATTERNS`** — externalized to `config/prefilter_rules.yaml` under `hard_rejects`, grouped by category. Loaded via `src/findajob/config_loader.py`.

- [x] **`_IN_DOMAIN_PATTERNS`** — externalized to `config/in_domain_patterns.yaml` under `positive`.

- [x] **`_IN_DOMAIN_POISON`** — externalized to `config/in_domain_patterns.yaml` under `poison`.

**Resolved 2026-04-17 (#10):**
- `TIER1` was *dropped*, not externalized. The Tier-1 prefilter bonus (+1 score at the in-domain/no-JD floor) was removed entirely — the LLM already sees the target-companies list via prompt context, so the prefilter kludge is unnecessary.
- The "companies I care about" concept lives on via `config/companies_of_interest.txt`, consumed by `scripts/sync_sheet.py` (archival exception for low-score-old jobs at these companies) and `scripts/notify.py` (mis-score health check). The prefilter does NOT consult this file.
- Hard-reject + in-domain rules now load from `config/prefilter_rules.yaml` and `config/in_domain_patterns.yaml` through `src/findajob/config_loader.py`. Both files are gitignored; see `.example` siblings for templates.
- Items 4 and 5 of #10 (prompt-level neutralization: `job_scorer.md` hard-reject enumerations + ENGINEER TITLE CALIBRATION move to `profile.md`) are **deferred** — they change LLM behavior and need their own validation loop. Tracked as a follow-up issue.

### Scorer role prompt — `config/roles/job_scorer.md`

- [ ] **`HARD REJECT RULES`** section — enumerates tech job categories explicitly (software engineering, security, IT, supply chain, networking, hardware design, biotech, finance, legal, HR, marketing, facilities)
  - Should become: "Hard reject any role that matches a category listed in the candidate's profile under `## Excluded Categories`. The profile determines what's excluded."

- [ ] **`TIER 1 COMPANY EXCEPTION`** — defines in-domain titles as "Data center technician, DC operations, NPI program manager, operational readiness, forward deployed engineer"
  - Should reference profile's `## Core Competencies` and `## Target Role` sections
  - The exception logic (Tier 1 + in-domain → score 6 minimum) is generic and can stay

- [ ] **`ENGINEER TITLE CALIBRATION`** section — assumes candidate has mixed IC/ops/program background in hardware
  - This entire section is personal calibration based on past false positives
  - Should move to `candidate_context/profile.md` as candidate-specific scoring guidance, or become optional

### Role prompts with tech vocabulary — `config/roles/*.md`

- [ ] **`briefing_writer.md`** — mostly generic but may include tech interview framing
- [ ] **`fit_analyst.md`** — scoring dimensions may be tech-biased
- [ ] **`company_researcher.md`** — assumes company has "products", "funding rounds" — may not fit nonprofit/public sector
- [ ] **`outreach_drafter.md`** — tone is tech-industry informal; social work/education may need more formal
- [ ] **`cover_letter_writer.md`** — generic-ish, verify

All of these need a pass with a non-tech candidate profile to see what breaks.

### Example files — `config/*.example`, `candidate_context/*`

- [ ] **`candidate_context/profile.md.example`** — target role is "hardware engineer / technical program manager at AI infrastructure companies"
  - Should show 2-3 examples from different fields (healthcare, education, social services, tech)
  - Or a more abstract template that's field-neutral

- [ ] **`config/target_companies.md.example`** — lists OpenAI, Anthropic, Google DeepMind
  - Should show examples from multiple fields or use generic "Company A / Company B / Company C"

- [ ] **`config/jsearch_queries.txt.example`** — tech queries only
  - Add examples for: social work case manager, elementary school teacher, nonprofit development director, hospital patient advocate

- [ ] **`config/feed_urls.txt.example`** — Greenhouse slugs for tech companies
  - Greenhouse itself is tech-biased; many non-tech employers use Workday, Taleo, iCIMS
  - Longer-term: add alternative ATS integrations

### Scripts with domain hints

- [ ] **`scripts/find_contacts.py`** — assumes LinkedIn connections.csv format
  - LinkedIn is still the dominant professional network across fields, probably OK
  - But for some fields (education, healthcare) job-relevant contacts may not be on LinkedIn
  - Could support other contact sources via config

- [ ] **`scripts/ingest_form.py`** — Google Form ingestion, fields assume job search
  - Generic enough but worth reviewing the field names

### Google Sheets column names — `scripts/sync_sheet.py`, `scripts/setup_sheets.py`

- [ ] Column headers include `comp_estimate`, `known_contacts`, `remote_status` — generic
- [ ] `REJECT_REASON` dropdown options include "Too TPM-Heavy" and "Skills Mismatch" — need to verify these are configurable
- [ ] No known domain leakage here

### Documentation — `docs/*.md`

- [x] **`docs/architecture.md`** — generic, OK
- [ ] **`docs/operations.md`** — may reference tech workflows; needs review
- [ ] **`docs/setup/configure.md`** — may mention AI company examples
- [ ] **`docs/google-sheets.md`** — verify neutral

### Search / ingestion logic — `scripts/triage.py`

- [ ] LinkedIn/Indeed query parameters: `experienceLevels: 'midSenior;director'` — hardcoded to senior/director tier
  - Should be configurable per candidate (junior, mid, senior, exec)
- [ ] Default `location: 'United States'` — hardcoded, reasonable default but should be configurable

---

## Already Generic (no work needed)

- [x] `scripts/utils.py` — pure utilities, no domain
- [x] `scripts/poll_flags.py` — generic stage management
- [x] `scripts/sync_sheet.py` — generic DB-to-Sheets sync
- [x] `scripts/notify.py` — ntfy wrapper, generic
- [x] `scripts/analyze_feedback.py` — reads feedback_log and jobs, no domain content
- [x] `scripts/backfill_jd.py` — generic JD re-fetch
- [x] `scripts/init_db.py` — generic schema
- [x] `scripts/prep_application.py` — generic prep orchestration; domain comes from injected profile
- [x] `config/scoring_schema.json` — generic 1-10 score + string fields
- [x] `config/reference.docx` — neutral Word template
- [x] `CLAUDE.md` — project guidance, no candidate details
- [x] `CLAUDE.local.md.example` — placeholder template

---

## Order of Work (suggested)

**Phase 1: Config externalization (high value, mechanical)** — ✅ shipped 2026-04-17 (#10)
1. ~~Move `TIER1` out~~ → dropped; replaced by `config/companies_of_interest.txt` for non-prefilter consumers.
2. ~~Move `_HARD_REJECT_PATTERNS`~~ → loaded from `config/prefilter_rules.yaml` via `config_loader`.
3. ~~Move `_IN_DOMAIN_PATTERNS` and `_IN_DOMAIN_POISON`~~ → loaded from `config/in_domain_patterns.yaml`.

**Phase 2: Prompt neutralization**
4. Rewrite `job_scorer.md` hard reject section to reference profile categories rather than enumerate tech
5. Move engineer-calibration logic from prompt to profile.md.example (as a generic example of "per-candidate scoring calibration")
6. Audit other role prompts for domain vocabulary

**Phase 3: Example diversification**
7. Rewrite `profile.md.example` and `target_companies.md.example` to show 3 fields
8. Add non-tech example queries to `jsearch_queries.txt.example`
9. Document that Greenhouse integration is tech-heavy; note alternatives for Phase 5

**Phase 4: Setup flow**
10. Build a guided `scripts/setup_profile.py` that walks new users through creating their own config from scratch based on their field
11. Document the "I'm a \_\_\_\_" starter flow in README

**Phase 5: Alternative ingestion**
12. Add Workday / Taleo / iCIMS feed support for non-tech fields
13. Evaluate per-field best ATS integrations

---

## Self-Check for Future Sessions

Before committing code or prompt changes, ask:
1. Does this add any hardcoded company name, job title, industry term, or category that only makes sense for one field?
2. Does this make the pipeline easier or harder to use for a social worker, teacher, or accountant?
3. Is the domain-specific content in `config/*` (gitignored) or in `scripts/*` (tracked)?

If the answer to #3 is "scripts/", stop and reconsider. Domain content belongs in gitignored config.
