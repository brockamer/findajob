"""Pipeline funnel scoreboard — refreshes the operator-facing GitHub issue."""

import subprocess
from datetime import UTC, datetime

from findajob.analyze_feedback import analyze as feedback_analyze
from findajob.notifications.ntfy import db_connect, send

SCOREBOARD_ISSUE = 31
SCOREBOARD_REPO = "brockamer/findajob"


def cmd_scoreboard() -> None:
    """Regenerate the pipeline funnel scoreboard and update issue #31."""
    conn = db_connect()
    today = datetime.now(UTC).strftime("%Y-%m-%d")

    # ── Funnel counts ──
    total = conn.execute("SELECT COUNT(*) FROM jobs WHERE dupe_of = '' OR dupe_of IS NULL").fetchone()[0]
    scored = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE relevance_score IS NOT NULL AND (dupe_of = '' OR dupe_of IS NULL)"
    ).fetchone()[0]
    s7 = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE relevance_score >= 7 AND (dupe_of = '' OR dupe_of IS NULL)"
    ).fetchone()[0]
    prepped = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE prep_folder_path IS NOT NULL AND prep_folder_path != ''"
    ).fetchone()[0]
    applied = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE stage IN ('applied','interview','offer','not_selected')"
    ).fetchone()[0]
    interview = conn.execute("SELECT COUNT(*) FROM jobs WHERE stage IN ('interview','offer')").fetchone()[0]
    offer = conn.execute("SELECT COUNT(*) FROM jobs WHERE stage = 'offer'").fetchone()[0]

    # ── Conversion rates ──
    hit_rate = f"{s7 / scored * 100:.1f}" if scored else "0"
    prep_rate = f"{prepped / s7 * 100:.0f}" if s7 else "0"
    apply_rate = f"{applied / prepped * 100:.0f}" if prepped else "0"
    interview_rate = f"{interview / applied * 100:.0f}" if applied else "0"

    # ── Current queue ──
    ready = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE stage = 'materials_drafted' AND (dupe_of = '' OR dupe_of IS NULL)"
    ).fetchone()[0]
    waitlisted = conn.execute("SELECT COUNT(*) FROM jobs WHERE stage = 'waitlisted'").fetchone()[0]
    user_rejected = conn.execute("SELECT COUNT(*) FROM jobs WHERE stage = 'rejected'").fetchone()[0]
    feedback_entries = conn.execute("SELECT COUNT(*) FROM feedback_log").fetchone()[0]

    # ── Score distribution ──
    dist_rows = conn.execute("""
        SELECT relevance_score, COUNT(*) as cnt FROM jobs
        WHERE relevance_score IS NOT NULL AND (dupe_of = '' OR dupe_of IS NULL)
        GROUP BY relevance_score ORDER BY relevance_score
    """).fetchall()

    # ── Attrition ──
    score1 = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE relevance_score = 1 AND (dupe_of = '' OR dupe_of IS NULL)"
    ).fetchone()[0]
    score2_6 = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE relevance_score BETWEEN 2 AND 6 AND (dupe_of = '' OR dupe_of IS NULL)"
    ).fetchone()[0]
    rejected_after_prep = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE stage = 'rejected' AND prep_folder_path IS NOT NULL AND prep_folder_path != ''"
    ).fetchone()[0]
    waitlisted_after_prep = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE stage = 'waitlisted'"
        " AND prep_folder_path IS NOT NULL AND prep_folder_path != ''"
    ).fetchone()[0]
    not_selected = conn.execute("SELECT COUNT(*) FROM jobs WHERE stage = 'not_selected'").fetchone()[0]

    # ── Low-signal feeds (last 7d) ──
    # Companies producing ≥20 scored jobs in 7 days with 0 scoring 7+ are strong
    # candidates for removal from feed_urls.txt. The scorer is working correctly
    # for these — the feed is genuinely off-target for the user's profile.
    low_signal_rows = conn.execute("""
        SELECT
            company,
            COUNT(*) AS total,
            GROUP_CONCAT(DISTINCT source) AS sources
        FROM jobs
        WHERE julianday('now') - julianday(created_at) <= 7
          AND (dupe_of = '' OR dupe_of IS NULL)
          AND relevance_score IS NOT NULL
          AND company != ''
        GROUP BY company
        HAVING total >= 20 AND SUM(CASE WHEN relevance_score >= 7 THEN 1 ELSE 0 END) = 0
        ORDER BY total DESC
    """).fetchall()

    conn.close()

    # ── Build markdown ──
    dist_table = "| Score | Count | % |\n|-------|-------|---|\n"
    for r in dist_rows:
        pct = f"{r['cnt'] / scored * 100:.1f}" if scored else "0"
        dist_table += f"| {r['relevance_score']} | {r['cnt']:,} | {pct}% |\n"

    hit_health = "healthy" if 2 <= float(hit_rate) <= 5 else "review needed"

    if low_signal_rows:
        low_signal_section = (
            "Companies with ≥20 scored jobs in the last 7d and 0 scoring 7+. "
            "Strong candidates for removal from `feed_urls.txt` — the scorer is "
            "correctly rejecting these as off-profile; the feed just adds noise.\n\n"
            "| Company | Jobs (7d) | Source(s) |\n"
            "|---|---|---|\n"
        )
        for r in low_signal_rows:
            low_signal_section += f"| {r['company']} | {r['total']} | {r['sources']} |\n"
    else:
        low_signal_section = "_None — every active feed produced at least one 7+ job in the last 7 days._"

    # ── Prefilter expansion candidates ──
    # Title n-grams that recur in score-7+ rejections with title-related reject
    # reasons. Each one is a concrete candidate to add to scorer_prefilter.py.
    # Human-approved — this is a proposal list, nothing is auto-applied.
    candidates: list[dict] = []
    try:
        fb_conn = db_connect()
        fb = feedback_analyze(fb_conn)
        fb_conn.close()
        candidates = fb.get("prefilter_candidates", [])[:10]
    except Exception:  # noqa: BLE001 — scoreboard must not crash on feedback-analysis failure
        candidates = []
    # ── LLM spend (last 7d, populated cost_log rows only) ──
    spend_conn = db_connect()
    spend_rows = spend_conn.execute("""
        SELECT
            operation,
            COUNT(*) AS n_calls,
            SUM(cost_usd) AS total_cost,
            SUM(input_tokens) AS in_tok,
            SUM(output_tokens) AS out_tok
        FROM cost_log
        WHERE cost_usd IS NOT NULL
          AND julianday('now') - julianday(logged_at) <= 7
        GROUP BY operation
        ORDER BY total_cost DESC
    """).fetchall()

    # cost_log.cost_usd comes from OpenRouter's response.usage.cost — authoritative.
    total_7d = sum((r["total_cost"] or 0) for r in spend_rows)
    total_calls_7d = sum((r["n_calls"] or 0) for r in spend_rows)
    spend_conn.close()

    if spend_rows:
        monthly_proj = total_7d * (30 / 7)
        spend_section = (
            f"**Total: ${total_7d:.2f}** across {total_calls_7d:,} calls "
            f"(projected monthly: **${monthly_proj:.0f}**).\n\n"
            "Sourced from `cost_log.cost_usd` (OpenRouter native).\n\n"
            "| Operation | Calls | Input tok | Output tok | Cost (7d) |\n"
            "|---|---|---|---|---|\n"
        )
        for r in spend_rows:
            op_cost = r["total_cost"] or 0
            spend_section += (
                f"| {r['operation']} | {r['n_calls']:,} | "
                f"{(r['in_tok'] or 0):,} | {(r['out_tok'] or 0):,} | "
                f"${op_cost:.2f} |\n"
            )
    else:
        spend_section = "_No cost data in the last 7d._"

    if candidates:
        prefilter_candidates_section = (
            "Title n-grams recurring in score-7+ rejections (3+ times, title-related reasons only, "
            "not in applied-job titles). Each is a candidate to add to `scorer_prefilter.py` Stage 1. "
            "Review and add the patterns that consistently waste scoring budget.\n\n"
            "| Count | Reason | N-gram | Proposed regex | Example |\n"
            "|---|---|---|---|---|\n"
        )
        for c in candidates:
            ngram = " ".join(c["ngram"])
            example = (c["examples"][0] if c["examples"] else "")[:60]
            prefilter_candidates_section += (
                f"| {c['count']} | {c['dominant_reason']} | `{ngram}` | `{c['proposed_regex']}` | {example} |\n"
            )
    else:
        prefilter_candidates_section = (
            "_No recurring patterns (need ≥3 rejections at score 7+ with title-related reason)._"
        )

    body = f"""\
> **This is a living scoreboard, not a task.** Auto-updated weekly by `notify.py scoreboard`.

## The Funnel (as of {today})

Cumulative counts — how many jobs ever reached each stage, not just current state.

```
  Ingested    {total:,} jobs
      │
  Scored      {scored:,} ({scored / total * 100:.0f}%)
      │
  Score 7+      {s7:,} ({hit_rate}% of scored)     ← pipeline signal quality
      │
  Prepped        {prepped:,} ({prep_rate}% of 7+)          ← materials generated
      │
  Applied        {applied:,} ({apply_rate}% of prepped)     ← applications submitted
      │
  Interview       {interview} ({interview_rate}% of applied)      ← active interviews
      │
  Offer           {offer}                       ← pending
```

### Conversion Rates

| Step | Rate | Interpretation |
|------|------|---------------|
| Scored → 7+ | {hit_rate}% | Selectivity. Too low = queries too broad. Too high = scorer too generous. |
| 7+ → Prepped | {prep_rate}% | User triage. Rest rejected before prep (user filter working). |
| Prepped → Applied | {apply_rate}% | User action bottleneck. Materials exist but applications require human effort. |
| Applied → Interview | {interview_rate}% | Market signal. Low = resume/targeting needs work. |

### Current Queue

| Status | Count | Note |
|--------|-------|------|
| Ready to Apply (`materials_drafted`) | {ready} | |
| Waitlisted | {waitlisted} | Deferred, not rejected |
| User rejected | {user_rejected} | {feedback_entries} in feedback_log feeding back to scorer |

### Attrition Detail

| Exit Point | Count | Note |
|------------|-------|------|
| Hard reject (score 1) | {score1:,} | {score1 / scored * 100:.0f}% — prefilter working as intended |
| Score 2–6 | {score2_6:,} | Filtered by Dashboard threshold |
| User rejected after prep | {rejected_after_prep} | Prepped but user decided not to apply |
| Waitlisted after prep | {waitlisted_after_prep} | Good fit but timing/competing apps |
| Not selected (company) | {not_selected} | Company rejections |

## Score Distribution

{dist_table}

## Low-Signal Feeds (last 7d)

{low_signal_section}

## Prefilter Expansion Candidates

{prefilter_candidates_section}

## LLM Spend (last 7d)

{spend_section}

## What to Watch

- **Score 7+ hit rate** should be 2–5%. Currently {hit_rate}% — {hit_health}.
- **Prepped → Applied conversion ({apply_rate}%)** is the user bottleneck.
- **Applied → Interview rate ({interview_rate}%)** — needs 50+ applications before this metric is meaningful.

---

📌 Pinned — not a task to complete. Auto-updated weekly by `notify.py scoreboard`.

🤖 Generated with [Claude Code](https://claude.com/claude-code)"""

    # Update the issue
    rc = subprocess.run(
        ["gh", "issue", "edit", str(SCOREBOARD_ISSUE), "--repo", SCOREBOARD_REPO, "--body", body],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if rc.returncode == 0:
        msg = f"Pipeline funnel scoreboard (#31) updated for {today}."
        send("💼 findajob — scoreboard updated", msg, priority="low", tags="bar_chart", kind="scoreboard")
    else:
        send(
            "💼 findajob — scoreboard update failed",
            f"gh issue edit failed: {rc.stderr[:200]}",
            priority="high",
            tags="warning",
            kind="scoreboard",
        )
