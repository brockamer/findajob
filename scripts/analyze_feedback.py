#!/usr/bin/env python3
# ~/JobSearchPipeline/scripts/analyze_feedback.py
"""
Feedback loop analysis — no LLM, pure SQL + Python.

Reads feedback_log and jobs table to surface:
  1. Rejection breakdown by reason
  2. False positive analysis (score 8+ but rejected)
  3. Title keyword signals (applied vs rejected)
  4. Company repeat rejection patterns
  5. Source/query attribution of false positives
  6. Actionable recommendations

Usage:
    python3 scripts/analyze_feedback.py           # print report
    python3 scripts/analyze_feedback.py --notify  # also send via ntfy
    python3 scripts/analyze_feedback.py --json    # output JSON (for scripting)
"""

import json
import re
import sqlite3
import sys
from collections import Counter
from datetime import datetime

from findajob.config_loader import load_reject_reasons
from findajob.paths import BASE
from findajob.utils import load_env

DB_PATH = f"{BASE}/data/pipeline.db"

# Title keyword signals — tokens to track in applied vs rejected jobs
_TITLE_KEYWORDS = [
    "data center",
    "datacenter",
    "operations",
    "manager",
    "technician",
    "engineer",
    "senior",
    "director",
    "lead",
    "infrastructure",
    "systems",
    "hardware",
    "technical",
    "program",
    "manufacturing",
    "quality",
    "deployment",
    "site",
    "npi",
    "ai",
    "forward deployed",
]


def _contains(title, kw):
    return bool(re.search(r"\b" + re.escape(kw) + r"\b", title or "", re.IGNORECASE))


def analyze(conn):
    """Run full analysis. Returns a dict of findings."""
    out = {}

    # ── 1. Rejection breakdown ────────────────────────────────────────────────
    rows = conn.execute("""
        SELECT reject_reason, COUNT(*) as n
        FROM feedback_log
        GROUP BY reject_reason
        ORDER BY n DESC
    """).fetchall()
    total = sum(r["n"] for r in rows)
    out["total_feedback"] = total
    out["by_reason"] = [(r["reject_reason"] or "(blank)", r["n"]) for r in rows]

    if total == 0:
        out["error"] = "No feedback entries yet"
        return out

    # ── 2. False positive analysis (score 8+, rejected) ───────────────────────
    fp_rows = conn.execute("""
        SELECT f.title, f.company, f.relevance_score, f.reject_reason,
               j.source, j.url
        FROM feedback_log f
        LEFT JOIN jobs j ON j.id = f.job_id
        WHERE f.relevance_score >= 8
        ORDER BY f.relevance_score DESC, f.reject_reason
    """).fetchall()
    out["false_positives"] = len(fp_rows)
    out["fp_pct"] = round(len(fp_rows) / total * 100, 1) if total else 0

    fp_by_reason = Counter(r["reject_reason"] for r in fp_rows)
    out["fp_by_reason"] = fp_by_reason.most_common()

    # Score distribution in feedback_log
    score_dist = conn.execute("""
        SELECT relevance_score, COUNT(*) as n
        FROM feedback_log
        WHERE relevance_score IS NOT NULL
        GROUP BY relevance_score
        ORDER BY relevance_score DESC
    """).fetchall()
    out["score_distribution"] = [(r["relevance_score"], r["n"]) for r in score_dist]

    # ── 3. Title keyword signals ──────────────────────────────────────────────
    # Compare keyword frequency in applied/drafted vs rejected jobs
    applied_rows = conn.execute("""
        SELECT title FROM jobs
        WHERE stage IN ('applied', 'materials_drafted')
          AND (dupe_of = '' OR dupe_of IS NULL)
    """).fetchall()
    rejected_rows = conn.execute("""
        SELECT title FROM jobs
        WHERE stage = 'rejected'
          AND (dupe_of = '' OR dupe_of IS NULL)
          AND relevance_score >= 7
    """).fetchall()

    n_applied = len(applied_rows)
    n_rejected = len(rejected_rows)
    keyword_signals = []
    for kw in _TITLE_KEYWORDS:
        a_count = sum(1 for r in applied_rows if _contains(r["title"], kw))
        r_count = sum(1 for r in rejected_rows if _contains(r["title"], kw))
        a_pct = round(a_count / n_applied * 100, 1) if n_applied else 0
        r_pct = round(r_count / n_rejected * 100, 1) if n_rejected else 0
        if a_pct == 0 and r_pct == 0:
            continue
        ratio = round(a_pct / r_pct, 2) if r_pct > 0 else float("inf")
        keyword_signals.append(
            {
                "keyword": kw,
                "applied_pct": a_pct,
                "rejected_pct": r_pct,
                "ratio": ratio,
                "applied_n": a_count,
                "rejected_n": r_count,
            }
        )
    # Sort: biggest divergence first (most applied-vs-rejected skew)
    keyword_signals.sort(key=lambda x: abs(x["ratio"] - 1.0) if x["ratio"] != float("inf") else 999, reverse=True)
    out["keyword_signals"] = keyword_signals

    # ── 4. Company repeat rejection patterns ──────────────────────────────────
    company_counts = Counter(r["company"] for r in fp_rows if r["company"])
    out["company_fp_counts"] = company_counts.most_common(10)

    # ── 5. Source attribution of false positives ──────────────────────────────
    source_counts = Counter(r["source"] for r in fp_rows if r["source"])
    out["fp_by_source"] = source_counts.most_common()

    # Overall rejection rate by source (false positives / total jobs from source)
    source_totals = conn.execute("""
        SELECT source, COUNT(*) as n
        FROM jobs
        WHERE (dupe_of = '' OR dupe_of IS NULL) AND relevance_score >= 7
        GROUP BY source
    """).fetchall()
    source_total_map = {r["source"]: r["n"] for r in source_totals}
    source_fp_rates = []
    for src, fp_n in source_counts.most_common():
        total_n = source_total_map.get(src, 0)
        rate = round(fp_n / total_n * 100, 1) if total_n else 0
        source_fp_rates.append({"source": src, "fp_count": fp_n, "total_scored_7plus": total_n, "fp_rate_pct": rate})
    out["source_fp_rates"] = source_fp_rates

    # ── 6. Title patterns in false positives — prefilter candidates ───────────
    fp_titles = [r["title"] for r in fp_rows if r["title"]]
    title_word_freq = Counter()
    for t in fp_titles:
        words = re.findall(r"\b[a-zA-Z]{3,}\b", t.lower())
        for w in words:
            if w not in (
                "and",
                "the",
                "for",
                "with",
                "data",
                "center",
                "senior",
                "lead",
                "manager",
                "director",
                "engineer",
                "operations",
            ):
                title_word_freq[w] += 1
    out["fp_title_word_freq"] = title_word_freq.most_common(20)

    # ── 7. Applied jobs info for context ─────────────────────────────────────
    out["n_applied_jobs"] = n_applied
    out["n_rejected_high_score"] = n_rejected

    # ── 8. Prefilter expansion candidates (n-gram recurrences) ───────────────
    # Surface 2–3 word title sequences that recur in score-7+ rejections AND
    # never appear in applied titles. These are concrete candidates to add to
    # scorer_prefilter.py Stage 1 (title regex hard-reject).
    out["prefilter_candidates"] = _prefilter_candidates(fp_rows, applied_rows)

    return out


# Short words that dominate low-value n-grams — filter out n-grams that are
# entirely made of these (e.g., "manager and" is useless).
_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "the",
        "of",
        "and",
        "for",
        "with",
        "to",
        "in",
        "on",
        "at",
        "by",
        "or",
        "from",
    }
)


def _tokenize(title):
    return re.findall(r"\b[a-zA-Z][a-zA-Z0-9-]*\b", (title or "").lower())


def _ngrams(tokens, n):
    return [tuple(tokens[i : i + n]) for i in range(len(tokens) - n + 1)]


# Reject reasons that indicate the TITLE was the problem (not user already
# applied, not the posting going stale, etc). Only these feed the prefilter
# candidate analysis — we're looking for patterns the scorer keeps missing.
# Single source of truth: `config/reject_reasons.yaml` (`title_signal_reasons:`
# subset of `reasons:`).
_, _TITLE_SIGNAL_REASONS = load_reject_reasons()


def _prefilter_candidates(rejected_rows, applied_rows, min_recurrences=3):
    """Return list of n-gram patterns recurring in rejections, absent from applied.

    Only counts rejections where reject_reason signals a title problem
    (not 'Already Applied', 'Stale/Closed', 'Other' etc.).

    Each candidate dict has:
      - ngram: tuple of tokens
      - count: times seen in rejections at score 7+
      - dominant_reason: most common reject_reason for this n-gram
      - proposed_regex: regex suitable for scorer_prefilter.py Stage 1
      - examples: up to 3 example titles that matched
    """
    # Build applied n-gram set for negative filtering — anything the user
    # actively applied to should never be hard-rejected.
    applied_ngrams: set[tuple[str, ...]] = set()
    for r in applied_rows:
        toks = _tokenize(r["title"])
        for n in (2, 3):
            applied_ngrams.update(_ngrams(toks, n))

    # Count n-grams across rejections where reason is title-related
    rejected_counter: Counter[tuple[str, ...]] = Counter()
    reasons_by_ngram: dict[tuple[str, ...], Counter[str]] = {}
    examples: dict[tuple[str, ...], list[str]] = {}
    for r in rejected_rows:
        reason = r["reject_reason"]
        if reason not in _TITLE_SIGNAL_REASONS:
            continue
        title = r["title"] or ""
        toks = _tokenize(title)
        seen_here: set[tuple[str, ...]] = set()
        for n in (2, 3):
            for g in _ngrams(toks, n):
                if g in seen_here:
                    continue
                seen_here.add(g)
                if all(t in _STOPWORDS for t in g):
                    continue
                rejected_counter[g] += 1
                reasons_by_ngram.setdefault(g, Counter())[reason] += 1
                examples.setdefault(g, []).append(title)

    candidates = []
    for g, count in rejected_counter.most_common():
        if count < min_recurrences:
            continue
        if g in applied_ngrams:
            continue  # the user liked something with this pattern
        if not any(len(t) >= 4 for t in g):
            continue
        dominant_reason, _ = reasons_by_ngram[g].most_common(1)[0]
        parts = [re.escape(t) for t in g]
        proposed_regex = r"\b" + r"\s+".join(parts) + r"\b"
        candidates.append(
            {
                "ngram": g,
                "count": count,
                "dominant_reason": dominant_reason,
                "proposed_regex": proposed_regex,
                "examples": examples[g][:3],
            }
        )
    return candidates


def format_report(data):
    """Format analysis dict into a human-readable report string."""
    lines = []
    lines.append("=" * 60)
    lines.append("JSP FEEDBACK LOOP ANALYSIS")
    lines.append(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append("=" * 60)

    if data.get("error"):
        lines.append(f"\n{data['error']}")
        return "\n".join(lines)

    total = data["total_feedback"]

    # ── 1. Rejection breakdown ────────────────────────────────────────────────
    lines.append(f"\n{'─' * 40}")
    lines.append(f"REJECTION BREAKDOWN ({total} total)")
    lines.append(f"{'─' * 40}")
    for reason, n in data["by_reason"]:
        pct = round(n / total * 100, 1)
        lines.append(f"  {reason:<30} {n:>4}  ({pct}%)")

    # ── 2. False positives ────────────────────────────────────────────────────
    lines.append(f"\n{'─' * 40}")
    fp = data["false_positives"]
    fp_pct = data["fp_pct"]
    lines.append(f"FALSE POSITIVES (score 8+, rejected): {fp} of {total} ({fp_pct}%)")
    lines.append(f"{'─' * 40}")
    if data["score_distribution"]:
        lines.append("  Score distribution of rejected jobs:")
        for score, n in data["score_distribution"]:
            pct = round(n / total * 100, 1)
            lines.append(f"    {score:>3}: {n:>3} ({pct}%)")
    lines.append("  By reject reason (score 8+ only):")
    for reason, n in data["fp_by_reason"]:
        pct = round(n / fp * 100, 1) if fp else 0
        lines.append(f"    {reason:<30} {n:>4}  ({pct}%)")

    # ── 3. Keyword signals ────────────────────────────────────────────────────
    lines.append(f"\n{'─' * 40}")
    lines.append("TITLE KEYWORD SIGNALS (applied vs high-score rejected)")
    lines.append(f"  Applied jobs: {data['n_applied_jobs']}  |  Rejected (score 7+): {data['n_rejected_high_score']}")
    lines.append(f"{'─' * 40}")
    lines.append(f"  {'Keyword':<22} {'Applied%':>8}  {'Rejected%':>9}  {'Ratio':>7}")
    lines.append(f"  {'-' * 22}  {'-' * 8}  {'-' * 9}  {'-' * 7}")
    for kw in data["keyword_signals"]:
        ratio = kw["ratio"]
        ratio_str = f"{ratio:.2f}x" if ratio != float("inf") else "inf"
        trend = "↑ good" if ratio > 1.5 else ("↓ bad" if ratio < 0.5 else "")
        lines.append(
            f"  {kw['keyword']:<22}  {kw['applied_pct']:>7}%  {kw['rejected_pct']:>8}%  {ratio_str:>7}  {trend}"
        )

    # ── 4. Company patterns ───────────────────────────────────────────────────
    lines.append(f"\n{'─' * 40}")
    lines.append("COMPANIES WITH REPEAT FALSE POSITIVES")
    lines.append(f"{'─' * 40}")
    for company, n in data["company_fp_counts"]:
        lines.append(f"  {company:<35} {n:>3} rejections")

    # ── 5. Source attribution ─────────────────────────────────────────────────
    lines.append(f"\n{'─' * 40}")
    lines.append("SOURCE ATTRIBUTION (false positives / total score-7+ from source)")
    lines.append(f"{'─' * 40}")
    for s in data["source_fp_rates"]:
        lines.append(
            f"  {s['source']:<25} {s['fp_count']:>3} FP / {s['total_scored_7plus']:>4} total  ({s['fp_rate_pct']}% FP rate)"
        )

    # ── 6. Recommendations ───────────────────────────────────────────────────
    lines.append(f"\n{'─' * 40}")
    lines.append("ACTIONABLE SIGNALS")
    lines.append(f"{'─' * 40}")
    bad_kws = [kw for kw in data["keyword_signals"] if kw["ratio"] < 0.4 and kw["rejected_n"] >= 3]
    good_kws = [kw for kw in data["keyword_signals"] if kw["ratio"] > 2.0 and kw["applied_n"] >= 2]
    if good_kws:
        lines.append("  Keywords that predict GOOD fit (consider adding to search queries):")
        for kw in good_kws[:5]:
            lines.append(f'    + "{kw["keyword"]}"  ({kw["applied_n"]} applied, {kw["applied_pct"]}% rate)')
    if bad_kws:
        lines.append("  Keywords that predict BAD fit (consider adding to prefilter):")
        for kw in bad_kws[:8]:
            lines.append(
                f'    - "{kw["keyword"]}"  ({kw["rejected_n"]} rejected, {kw["rejected_pct"]}% rate, ratio={kw["ratio"]})'
            )
    top_company, top_n = data["company_fp_counts"][0] if data["company_fp_counts"] else (None, 0)
    if top_n >= 5:
        lines.append(f"  Company pattern: {top_company} has {top_n} false positives — review posting patterns")

    lines.append("\n" + "=" * 60)
    return "\n".join(lines)


def main():
    notify_flag = "--notify" in sys.argv
    json_flag = "--json" in sys.argv

    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row

    data = analyze(conn)
    conn.close()

    if json_flag:
        print(json.dumps(data, indent=2, default=str))
        return

    report = format_report(data)
    print(report)

    if notify_flag:
        load_env()
        import os
        import subprocess

        env = load_env()
        topic = env.get("NTFY_TOPIC") or os.environ.get("NTFY_TOPIC", "jobsearch-pipeline")
        # Send abbreviated summary via ntfy
        total = data.get("total_feedback", 0)
        fp = data.get("false_positives", 0)
        fp_pct = data.get("fp_pct", 0)
        top_reason = data["by_reason"][0] if data.get("by_reason") else ("?", 0)
        body = (
            f"Feedback log: {total} rejections\n"
            f"False positives (score 8+): {fp} ({fp_pct}%)\n"
            f"Top reason: {top_reason[0]} ({top_reason[1]})\n"
            f"Run: python3 scripts/analyze_feedback.py"
        )
        subprocess.run(
            [
                "curl",
                "-s",
                "-X",
                "POST",
                f"https://ntfy.sh/{topic}",
                "-H",
                "Title: JSP Feedback Analysis",
                "-H",
                "Tags: magnifying_glass",
                "-H",
                "Content-Type: text/plain; charset=utf-8",
                "-d",
                body,
            ],
            check=False,
            capture_output=True,
        )


if __name__ == "__main__":
    main()
