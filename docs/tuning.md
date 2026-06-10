# Tuning Guide

How to read the stats pages, understand what the numbers mean, decide whether
to adjust your pipeline configuration, and verify the adjustment worked.

## The tuning cycle

```
1. Review stats pages → spot an issue
2. Edit config (scorer prompt, prefilter rules, queries, etc.)
3. Wait 7 days for the change to take effect
4. Check the config-change marker on the chart
5. Click the marker → read the before/after comparison
6. Decide: keep, revert, or refine
```

## Stats pages at a glance

> **New to the Stats tab?** [Reading the Stats pages](usage/stats.md) is a screenshot tour of what each page shows and how to read it. The notes below are the *tuning* lens — what to change when a number looks off.

### /stats/funnel

**What it shows:** Daily stage-transition counts over the last 30 days,
plus conversion rates between consecutive funnel stages.

**Key numbers:**
- **Conversion rates** — the percentage of jobs that advance from one stage
  to the next. Each rate shows a Wilson 95% confidence interval.
- **Rejection rate** — proportion of scored jobs that were rejected. A rising
  rejection rate may mean the scorer is letting through too many irrelevant
  jobs; a falling one may mean you're being too strict.

**When to act:** If the scored→manual_review conversion drops below your
comfort level, or if the rejection rate climbs above ~60%, review your
scorer prompt and prefilter rules.

### /stats/feedback

**What it shows:** Per-reject-reason breakdown over the last 28 days.

**Key numbers:**
- **Reason proportions** — each reason's share of total rejections, with
  Wilson 95% CI. High-proportion reasons are candidates for automated
  prefilter rules.
- **Daily trend** — look for spikes that correlate with config changes
  (red dashed vertical lines on the chart).

**When to act:** If a single reason dominates (>30% of rejections), consider
adding a prefilter rule to catch it automatically. If a reason disappears
after a config change, the change worked.

### /stats/scoring

**What it shows:** Score distribution histograms for relevance, interview
likelihood, fit, and probability scores. Per-source stratification for
relevance scores.

**Key numbers:**
- **Distribution shape** — a healthy pipeline has a left-skewed distribution
  (most jobs score low, a tail scores high). A flat distribution suggests
  the scorer isn't differentiating.
- **Per-source breakdown** — if one source produces systematically different
  score distributions, that's useful signal for query tuning.

**When to act:** If a source's distribution is flat or bimodal, its queries
may be too broad or too narrow.

### /stats/rejections

**What it shows:** All-time rejection breakdown by reason and by company.

**Key numbers:**
- **Per-reason proportions** with Wilson CIs — same data as /stats/feedback
  but all-time rather than 28-day windowed.
- **Top-5 companies** — where rejection volume concentrates.

### /stats/throughput

**What it shows:** Per-week counts of applied, interview, and offer
transitions. Stacked bar chart, all-time.

**Key numbers:**
- **Stage proportions** — what fraction of transitions are applied vs
  interview vs offer, with Wilson CIs.

### /stats/effectiveness

**What it shows:** Outcome tracking for submitted applications.

**Key numbers:**
- **Response rate** — proportion of applications that got any response
  (interview or explicit rejection). Wilson 95% CI.
- **Interview rate** — proportion that led to interviews.
- **Ghost rate** — applications with no response after 21 days.
- **Per-source breakdown** — which sources produce the best interview rates.
- **Response latency** — P25/median/P75 days from application to response.

**Expected state:** Most metrics will be min-N gated (showing "—") until
you have ≥20 applications. This is correct — the page is designed to
become useful over time, not to produce misleading numbers from small samples.

### /stats/recall-audit

**What it shows:** Weekly automated re-scoring results.

**Key numbers:**
- **Upgrade rate** — proportion of sampled jobs that scored higher on
  re-evaluation. Above 10% triggers an alert.
- **Alert threshold line** — the red dashed line at 10%.

**When to act:** A sustained upgrade rate above 10% suggests recall
degradation — the pipeline is rejecting jobs it shouldn't. Review recent
config changes and consider relaxing prefilter rules.

## Reading confidence intervals

Every proportion on these pages shows a **Wilson 95% confidence interval**
in brackets, e.g., `42.1% [38.2%, 46.0%]`.

- **Width tells you precision.** Narrow intervals (±2%) mean high confidence;
  wide intervals (±15%) mean the sample is small and the number could move
  significantly with a few more data points.
- **"—" means insufficient data.** When N < 20, the interval is too wide
  to be meaningful, so the page shows "—" instead. This is not a bug —
  it's protecting you from acting on noise.
- **Overlapping CIs ≠ no difference,** but if two intervals don't overlap
  at all, the difference is almost certainly real.

## Config-change markers

Red dashed vertical lines on trend charts mark dates when the pipeline
detected a configuration change. The label shows which lever changed
(e.g., `scorer_prompt`, `prefilter_rules`).

**Click a marker** to see a before/after comparison popover:
- **7d before vs 7d after** — key metrics computed for the week before and
  after the change.
- **Δ column** — the shift. Green means improvement, red means regression.
  Cost shifts are percentages; reject rate shifts are percentage points.

## Drift alerts

Seven days after any config change, the system automatically checks whether
key metrics shifted significantly:
- **>15 percentage points** in reject rate
- **>25% change** in cost per applied job

If either threshold is exceeded, you'll get an ntfy notification with the
specific numbers. Review the change and decide whether to keep or revert.

## Common tuning scenarios

### "Too many irrelevant jobs getting through"
1. Check `/stats/feedback` — which reject reasons dominate?
2. If a reason accounts for >30% of rejections, add a prefilter rule
3. Wait 7 days, check the marker on the chart
4. If rejection rate dropped and precision improved, keep the change

### "Good jobs are getting rejected"
1. Check `/stats/recall-audit` — is the upgrade rate above 10%?
2. Review recent prefilter rule additions or scorer prompt changes
3. Consider relaxing the rule or adjusting the prompt
4. Monitor the audit for 2-3 weeks after the change

### "Cost is too high"
1. Check the nav spend chip and `/stats/funnel` for prep volume
2. Look at which config lever was last changed
3. If cost spiked after a max_tokens increase or model upgrade, consider
   reverting or adding a spend ceiling
