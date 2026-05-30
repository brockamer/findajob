"""Render an :class:`AggregateResult` to a markdown report (#265).

Anti-slop by construction: every line is a verbatim quote/sentence + a count +
a ``file:line`` + a fixed-vocabulary action verb. No model writes prose here —
the recruiter's own words carry the voice, the aggregator supplies only the
index (where it lives, how often it recurs).
"""

from __future__ import annotations

from findajob.critique_aggregator.analyze import AggregateResult
from findajob.critique_aggregator.cluster import Cluster

# Controlled action vocabulary, keyed by the critic's section.
_ACTION = {
    "weak": "WEAKEN / CUT",
    "generic": "REWRITE",
    "missing": "ADD",
}


def _representative_sentence(cluster: Cluster) -> str:
    """The most informative verbatim recruiter sentence in the cluster."""
    return max((it.recruiter_sentence for it in cluster.items), key=len)


def _dominant_section(cluster: Cluster) -> str:
    counts: dict[str, int] = {}
    for it in cluster.items:
        counts[it.section] = counts.get(it.section, 0) + 1
    return max(counts, key=counts.get)


def _render_cluster(cluster: Cluster) -> str:
    section = _dominant_section(cluster)
    action = _ACTION.get(section, "REVIEW")
    loc = f"{cluster.anchor.file}:{cluster.anchor.line_no}"
    companies = ", ".join(cluster.companies)
    return "\n".join(
        [
            f"### {action}  `{loc}`",
            f"> {cluster.anchor.text}",
            "",
            f"Recruiter: {_representative_sentence(cluster)}",
            "",
            f"⤷ flagged by {cluster.company_count} companies: {companies}",
            "",
        ]
    )


def render_report(result: AggregateResult, *, generated_for: str) -> str:
    lines: list[str] = [
        f"# Recruiter-Critique Aggregate — {generated_for}",
        "",
        f"{result.total_critiques} critiques · {result.total_companies} companies · {result.total_flags} flagged lines",
        "",
    ]

    lines.append("## Source-level fixes — recurring across companies, act now")
    lines.append("")
    if result.source_clusters:
        for cluster in result.source_clusters:
            lines.append(_render_cluster(cluster))
    else:
        lines.append("No recurring source-level defects above the floor.")
        lines.append("")

    lines.append("## Recurring themes — no single source line (prompt / profile level)")
    lines.append("")
    if result.recurring_themes:
        for theme in result.recurring_themes:
            companies = ", ".join(theme.companies)
            lines.append(f"- **{theme.term}** — {theme.company_count} companies: {companies}")
    else:
        lines.append("No recurring unanchored themes above the floor.")
    lines.append("")

    lines.append(
        f"## One-offs — {result.oneoff_lines} content lines flagged below the floor "
        "(per-prep nitpicks, not yet systemic)"
    )
    lines.append("")

    return "\n".join(lines)
