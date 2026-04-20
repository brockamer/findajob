---
**Archived 2026-04-19. work done — see retroactive tracking issue filed 2026-04-19 summarizing what landed.**
---

# Search Expansion: Signal-to-Noise Improvement

**Date:** 2026-04-15
**Status:** Draft
**Motivation:** The pipeline's best signal comes from direct ATS feeds (Greenhouse/Ashby/Lever) — 78 of 159 jobs scored 7+ came from 15 ATS feeds. Broad Indeed/LinkedIn search produces 91% noise. Expanding ATS feeds and search categories is the highest-leverage improvement. Additionally, Brock's core competency — being the connective tissue between hardware engineering and field operations — transfers to any industry shipping complex physical products at scale.

---

## 1. The Problem

### Current state
- 4,278 jobs ingested total, 18 applied (0.4% yield)
- Best source: ATS feeds (78 of 159 7+ scored jobs from 15 feeds)
- Worst source: Indeed API (1,107 jobs/week, 91% hard-rejected)
- 89% Dashboard rejection rate — scorer lets through many "close but wrong niche" jobs
- Only searching one industry (data centers) despite transferable skills

### Root causes
1. **Coverage gap:** Only 15 ATS feeds, missing Tier 1 targets (Anthropic, OpenAI, Nebius)
2. **Category gap:** Not searching robotics, AV, virtual production — industries where the core competency directly transfers
3. **Scorer blind spot:** Profile and scorer prompt are 100% data-center-focused; a role that needs exactly Brock's skills but doesn't say "data center" would score low
4. **Search terms too narrow:** Only searching DC-specific job titles, missing the language these roles use in other industries

### The core competency (what the scorer should look for)

Brock is the person who **deeply understands the everyday world of the end user of a hardware product** — the operator, technician, or field engineer who has to live with what engineering designed. He can:

1. **Capture and characterize the operator's experience** in technical language that engineers respect
2. **Translate engineering decisions back to operators** — why the hardware is the way it is, how it gets made, what the realities of the NPI process mean for them
3. **Build trust and empathy between both sides** through programs, feedback loops, training, documentation, and cross-functional mechanisms
4. **Build the operational infrastructure** (processes, tools, training, runbooks) that scales this bridge as the product goes from 10 units to 10,000

This is NOT hardware validation engineering. It's NOT pure operations management. It's the connective tissue — and companies call it different things depending on their maturity:

| Company maturity | What they call this role |
|------------------|------------------------|
| Hyperscaler (Meta) | RTP Labs / NPI Operations |
| AI startup scaling DC | Head of Infrastructure Operations / Forward Deployed |
| Robotics company deploying units | Head of Field Operations / Deployment Operations |
| Hardware company with enterprise customers | Customer Engineering / Solutions Engineering / Field Enablement |
| AV company with fleet | Fleet Operations / Depot Operations / Hardware Ops |
| Any hardware company post-product-market-fit | Technical Enablement / Operational Readiness |

The scorer needs to recognize this role regardless of industry or title.

---

## 2. Solution: Three-Layer Expansion

### Layer 1: Add 17 verified ATS feeds (data center / AI infra)

These are verified via API probe. All have relevant open roles.

**Greenhouse (10 feeds):**
| Company | Slug | Category | Relevant Roles |
|---------|------|----------|----------------|
| Anthropic | `anthropic` | Tier 1 target | DC Design Lead, Sr DC Capacity Delivery Mgr |
| Nebius | `nebius` | Tier 1 target | DC Design Lead, Critical Infra Engineer |
| SpaceX | `spacex` | Tier 1-adjacent | Manager IT Infra (Data Centers) |
| Together AI | `togetherai` | AI infra | Director DC Ops, Director DC Strategy |
| RunPod | `runpod` | GPU cloud | Manager DC Network Engineering |
| Fireworks AI | `fireworksai` | AI infra | Monitoring |
| EdgeConneX | `edgeconnex` | DC provider | Lead DC Ops Engineer |
| Hut 8 | `hut8` | Crypto→AI | Manager DC Ops, VP AI Infrastructure |
| Core Scientific | `corescientific` | Crypto→AI | DC Design Engineer, VP Site Selection |
| Riot Games | `riotgames` | LA entertainment | Monitoring for infra roles |

**Ashby (5 feeds):**
| Company | Slug | Category |
|---------|------|----------|
| OpenAI | `openai` | Tier 1 target |
| Fluidstack | `fluidstack` | Tier 2 target |
| Modal | `modal` | AI infra |
| Baseten | `baseten` | AI infra |
| Aetherflux | `aetherflux` | Tier 2 target |

**Lever (2 feeds):**
| Company | Slug | Category |
|---------|------|----------|
| CleanSpark | `cleanspark` | Crypto→AI |
| T5 Data Centers | `t5datacenters` | DC provider |

### Layer 2: Add new-category ATS feeds (robotics priority, then adjacent)

Needs API probing (not yet verified). Target companies:

**Robotics (highest priority — strongest skill transfer):**
- Figure AI, Agility Robotics, Apptronik, Boston Dynamics, Tesla (Optimus), 1X Technologies, Sanctuary AI, Collaborative Robotics, Covariant, Locus Robotics, Symbotic, Serve Robotics (LA)

Why robotics is the best crossover: These companies are at the exact inflection point where they need someone who understands the field operator's reality. They're going from "we built 10 prototypes in a lab" to "we need to deploy, service, and support 1,000 units at customer sites." They need fleet readiness programs, technician training, field feedback loops, serviceability standards — the exact playbook Brock built at Meta, applied to robots instead of servers.

**Autonomous Vehicles:**
- Waymo, Zoox (Amazon), Aurora, Motional, Nuro, Gatik

Same pattern: hardware fleets in the field that need depot operations, hardware lifecycle management, and operational readiness.

**Virtual Production / Render (LA-specific):**
- Industrial Light & Magic (ILM), DNEG, Framestore, Luma AI

LED volume stages and render farms are specialized data centers. LA is the global center.

**Modular DC / Hardware Manufacturing:**
- Compass Datacenters, Vertiv, Schneider Electric

Companies that MANUFACTURE data centers as products. Brock understands both the product and the customer.

**Energy / Fusion (LA-adjacent):**
- TAE Technologies (Foothill Ranch CA), Commonwealth Fusion Systems, Helion

Test reactors need operational readiness programs and lab operations leadership.

### Layer 3: Expand jsearch queries

New search terms that use the *language* of the core competency, not just DC titles:

```
# Existing DC queries stay unchanged

# Robotics / hardware ops
robotics field operations manager
robotics deployment operations
hardware fleet operations manager

# Cross-industry bridge roles
forward deployed engineer operations
field enablement hardware
operational readiness hardware

# LA-specific
virtual production technology manager
```

Budget impact: 7 new queries × 2 sources = 14 additional API calls/day. Current: 22/day. New total: 36/day. Well within 666/day budget.

---

## 3. Scorer and Pre-filter Updates

### Scorer prompt changes

The scorer (`config/roles/job_scorer.md`) boost/reduce criteria need to shift from industry-specific to skill-specific. The scorer should recognize the core competency pattern regardless of what industry it appears in.

**Updated boost criteria (add to existing, don't replace):**

```
Boost score for roles involving:
- Being the bridge between engineering/R&D and field operations / end users
- Bringing operator or field reality into product development processes
- Building operational readiness programs, technician training, or field enablement
- Forward deployed engineering or customer engineering with a hardware focus
- Fleet operations, depot operations, or hardware lifecycle management for physical products
- Building the systems that scale a hardware product from prototype to mass deployment
- NPI operations in ANY hardware-intensive industry (servers, robots, vehicles, accelerators, satellites)
- Lab operations leadership (managing physical lab space, equipment, capacity for hardware bring-up)
```

**Updated reduce criteria (add to existing, don't replace):**

```
Reduce score for roles involving:
- Pure hardware DESIGN (mechanical design, electrical design, firmware, board layout, PCB, FPGA, schematic)
- Pure hardware VALIDATION engineering (DVT/PVT test plan authoring, characterization, compliance testing)
- Pure manufacturing engineering (process engineering, yield, line optimization)
- Software-only roles even if at a hardware company
- Roles where the entire job is managing a project schedule (pure TPM/PM)
```

The key distinction: **Brock is not the person who designs the hardware or validates it in the lab. He is the person who makes sure the people who OPERATE it in the field have what they need, and that what they learn in the field gets back to engineering.** The scorer should look for this pattern.

### Pre-filter: leave hard-reject patterns unchanged

Don't relax "mechanical engineer" or "electrical engineer" hard-rejects. Pure design roles should still be rejected at the regex level. The new-category roles that are good fits will have different titles — "Field Operations Manager," "Head of Deployment," "Fleet Readiness Lead" — none of which match the hard-reject patterns.

If a robotics company titles an ops role "Mechanical Engineer" (unlikely), the ATS feed will ingest it, the pre-filter will reject it, and that's an acceptable false negative. The alternative — relaxing the pattern — would let through thousands of pure ME/EE roles from Indeed, which is worse.

### TIER1 updates

Add to `scorer_prefilter.py` TIER1 set (after API probing confirms which robotics/AV companies have feeds):
```python
# Robotics
"figure", "agility", "apptronik", "boston dynamics",
# AV
"waymo", "zoox", "aurora",
# Energy
"tae technologies", "commonwealth fusion",
```

---

## 4. Pipeline Capacity Impact

### Ingestion volume
- 17 new DC/AI feeds: estimated +100-200 jobs/day (mostly deduped)
- ~15 new robotics/AV feeds: estimated +50-100 jobs/day
- 7 new jsearch queries: estimated +50-100 jobs/day
- **Total increase: ~200-400 jobs/day (currently ~350/day)**
- After dedup: probably +50-100 net new/day

### Scoring cost
DeepSeek v3.2 at ~$0.001/job. Adding 100 jobs/day = $0.10/day = $3/month. Negligible.

### Triage timing
Currently ~30-60 min for ~350 scored jobs. Adding 50-100 more: ~40-75 min. Runs at 07:00 UTC with no deadline. Acceptable.

### Dashboard volume
If scorer is well-tuned, Dashboard might grow from ~10 to ~15-20 jobs/day. This is more signal, not more noise — the new jobs would be genuinely interesting cross-industry roles. Rejection rate should decrease because the scorer is matching on skills rather than industry keywords.

---

## 5. Implementation Phases

### Phase 1: Add verified DC/AI ATS feeds (quick win, no prompt changes)
- Add 17 feeds to `feed_urls.txt`
- Update `TIER1` in `scorer_prefilter.py` for new DC/AI companies
- Run triage, validate new jobs appear and score correctly
- These are DC/AI roles so existing scorer handles them fine

### Phase 2: Update scorer for cross-industry recognition
- Update `profile.md` target roles to include the core competency description
- Update `job_scorer.md` boost/reduce criteria (skill-based, not industry-based)
- Keep pre-filter hard-reject patterns unchanged
- Test by manually scoring a few robotics/AV job descriptions through the pipeline

### Phase 3: Research and add robotics/AV/adjacent feeds
- Probe Greenhouse/Ashby/Lever APIs for robotics, AV, virtual production companies
- Add verified feeds to `feed_urls.txt`
- Add new jsearch queries
- Update `TIER1` for new target companies

### Phase 4: Validate and tune
- Run triage with expanded feeds + updated scorer
- Review first week of Dashboard results from new categories
- Tune scorer based on which new jobs are good fits vs. noise
- Adjust jsearch queries if needed (remove low-signal terms, add better ones)

---

## 6. What NOT to Change

- **Scoring model:** DeepSeek v3.2 via OpenRouter. Cheap, fast, good enough.
- **Triage schedule:** Daily at 07:00 UTC.
- **RapidAPI plan:** 20,000 req/month is sufficient.
- **Sheet architecture:** No new tabs. New-category jobs flow through same Dashboard.
- **Prep workflow:** Resume tailor, cover letter, briefing all work regardless of industry.
- **Pre-filter hard-reject patterns:** Leave unchanged. Let the LLM scorer handle the nuance.

---

## 7. Risks

**False positive spike from scorer changes:** If the skill-based boost criteria are too broad, Dashboard could fill with vaguely operational roles at hardware companies. Mitigation: the reduce criteria are specific enough ("pure hardware design," "pure validation," "pure manufacturing") to keep the boundary clear. Tune after first week.

**Robotics roles may not exist yet:** Many robotics companies are pre-deployment. They might not have field ops roles posted because they haven't hit that inflection point. Mitigation: monitor feeds for 2-4 weeks before concluding there's no signal. Companies at this stage often post roles under unusual titles.

**Category sprawl:** Adding 6+ new industries dilutes the search. Mitigation: phase the rollout. Start with robotics (Phase 3), evaluate for 2 weeks, then add AV/virtual production/energy only if robotics produces results.

**Scorer prompt complexity:** More criteria = harder for the LLM to calibrate. Mitigation: frame everything around ONE core competency pattern (bridge between engineering and operations) rather than listing industries. The LLM should be asking "does this role need someone who connects builders and operators?" not "is this a data center job?"

---

## 8. Success Criteria

- **Coverage:** At least 5 new companies producing 7+ scored jobs within first week of Phase 1
- **Cross-industry signal:** At least 3 robotics/AV/adjacent jobs scoring 7+ within first month of Phase 3
- **Precision:** Dashboard rejection rate stays below 90% (currently 89%)
- **Apply rate:** At least 2 new applications from expanded sources within first month
- **No regressions:** Existing DC/AI job scoring quality unchanged
- **Core competency test:** The scorer should score a Figure AI "Head of Field Operations" at 8+ and a Figure AI "Senior Mechanical Design Engineer" at 1-2
