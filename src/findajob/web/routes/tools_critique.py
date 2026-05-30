"""`/tools/critique-review/` — view the recruiter-critique aggregate (#933).

Computes the aggregate live from the corpus on each load (fast — no LLM, no
network) and renders it. Read-only; unlike the CLI it does not write the dated
report file. The report embeds real resume lines, but the whole app is
auth-gated, consistent with /materials/.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from findajob.critique_aggregator.pipeline import aggregate_corpus, default_source_files
from findajob.critique_aggregator.report import cluster_action, representative_sentence

router = APIRouter()

# Cap the noisy themes surface in the view (the CLI report keeps them all;
# tuning the floor itself is tracked in #932).
_MAX_THEMES = 15


@router.get("/tools/critique-review/", response_class=HTMLResponse)
def critique_review(request: Request) -> HTMLResponse:
    companies_root: Path = request.app.state.companies_root
    base_root: Path = request.app.state.base_root
    today = datetime.now().strftime("%Y-%m-%d")

    result, _ = aggregate_corpus(
        companies_root,
        default_source_files(base_root),
        generated_for=today,
    )

    clusters = [
        {
            "action": cluster_action(c),
            "location": f"{c.anchor.file}:{c.anchor.line_no}",
            "source_line": c.anchor.text,
            "recruiter_sentence": representative_sentence(c),
            "companies": c.companies,
            "company_count": c.company_count,
        }
        for c in result.source_clusters
    ]

    themes = result.recurring_themes[:_MAX_THEMES]
    hidden_themes = len(result.recurring_themes) - len(themes)

    templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "tools/critique_review.html",
        {
            "result": result,
            "clusters": clusters,
            "themes": themes,
            "hidden_themes": hidden_themes,
            "today": today,
        },
    )
