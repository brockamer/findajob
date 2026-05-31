# Generalization Tracking — Making findajob Domain-Agnostic

This document is for contributors evaluating whether findajob works for their
field and developers preventing new domain-locked content.

---

## Principles

1. **Logic is generic; lists are config.** Pipeline code must not embed domain knowledge — target companies, job titles, search queries, reject patterns are all config-driven.
2. **Prompts reference the profile, not the domain.** Role prompts instruct the LLM to read the candidate's profile for domain context, never hardcode category language.
3. **Profile is the source of truth.** `candidate_context/profile.md` carries all domain-specific content — target role, target companies, hard-reject categories, in-domain signals.

---

## Shipped (resolved items)

- **Prefilter: `TIER1` frozenset** — dropped; `config/target_companies.md` is the canonical target-company signal for non-prefilter consumers (#10, 2026-04-17).
- **Prefilter: `_HARD_REJECT_PATTERNS`** — externalized to `config/prefilter_rules.yaml` via `config_loader` (#10, 2026-04-17).
- **Prefilter: `_IN_DOMAIN_PATTERNS` / `_IN_DOMAIN_POISON`** — externalized to `config/in_domain_patterns.yaml` (#10, 2026-04-17).
- **`job_scorer.md`: hard-reject enumerations** — removed; prompt reads candidate profile sections (`## Excluded Categories`, `## Deal-Breakers`, etc.) instead (#65, 2026-04-25).
- **`job_scorer.md`: TIER 1 COMPANY EXCEPTION** — in-domain title list removed; prompt derives in-domain from profile target-role and core-competency sections (#65, 2026-04-25).
- **`job_scorer.md`: ENGINEER TITLE CALIBRATION** — moved to operator's `profile.md` under `## Title Calibration Notes`; `profile.md.example` shows tech and non-tech examples (#65, 2026-04-25).
- **`job_scorer.md`: CROSS-INDUSTRY RECOGNITION** — hardware-specific industry list removed; prompt reads profile cross-industry framing sections instead (#65, 2026-04-25).
- **`company_researcher.md`** — "senior infrastructure job candidate" and data-center-specific section headings replaced with field-neutral language (#156, 2026-04-22).
- **`cover_letter_writer.md`** — operator-specific role title in peer quote example and data center metric example (`MW, rack counts`) replaced with field-neutral alternatives (#156, 2026-04-22).
- **`resume_tailor.md`** — operator-specific section labels (`**Data Center Builds**`, `**Infrastructure NPI Operations**`) replaced with generic placeholders (#156, 2026-04-22).
- **`company_discoverer.md`** — field-agnostic by design; enumerates no industries, companies, or titles; runs weekly to produce a competency-fit signal separate from the static strategic-preference list in `profile.md` (#284).

---

## Open Items

### Example / template files

- [ ] **`candidate_context/profile.md.example`** — target role is tech-specific; should show 2-3 examples from different fields (healthcare, education, social services) or a field-neutral template.
- [ ] **`config/target_companies.md.example`** — lists AI-company examples; replace with multi-field examples or generic placeholders.
- [ ] **`config/jsearch_queries.txt.example`** — tech queries only; add examples for non-tech fields (social work, education, nonprofit, healthcare).
- [ ] **`config/feed_urls.txt.example`** — Greenhouse slugs for tech companies; note that non-tech employers often use Workday, Taleo, or iCIMS.

### Role prompts

- [ ] **`briefing_writer.md`** — may include tech interview framing; audit pending.
- [ ] **`fit_analyst.md`** — scoring dimensions may be tech-biased; audit pending.
- [ ] **`outreach_drafter.md`** — tone is tech-industry informal; formal-register fields (education, healthcare) may need an alternative.

### Ingestion / search

- [ ] **`scripts/triage.py`**: LinkedIn `experienceLevels: 'midSenior;director'` — should be configurable per candidate.
- [ ] **`scripts/triage.py`**: default `location: 'United States'` — should be configurable.
- [ ] **`scripts/find_contacts.py`** — assumes LinkedIn `connections.csv`; fields where job-relevant contacts are not on LinkedIn may need alternative contact sources.

### Suggested follow-on phases

- **Phase 3:** Rewrite example files (`profile.md.example`, `target_companies.md.example`, `jsearch_queries.txt.example`) to show multiple fields.
- **Phase 4:** Build a guided setup flow (`scripts/setup_profile.py`) and document a "I'm a ___" starter flow.
- **Phase 5:** Add Workday / Taleo / iCIMS feed support for non-tech ATS integrations.

---

## Self-Check for Future Sessions

Before committing code or prompt changes, ask:

1. Does this add any hardcoded company name, job title, industry term, or category that only makes sense for one field?
2. Does this make the pipeline easier or harder to use for a social worker, teacher, or accountant?
3. Is the domain-specific content in `config/*` (gitignored) or in `scripts/*` (tracked)?

If the answer to #3 is "scripts/", stop and reconsider. Domain content belongs in gitignored config.
