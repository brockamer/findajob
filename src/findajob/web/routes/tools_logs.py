"""`/tools/logs/pipeline/` — bounded tail viewer for pipeline.jsonl (#650).

Reuses `findajob.admin.jsonl_tail.tail_events` for the read; same source
the cron_registry concurrency gate uses, so the log viewer is a fully
faithful window onto whatever the gate sees. Filtering happens in
Python after the read, not via shell pipes.

v1 cut (spec §2.4): tail-200 + event-name single-select filter. No
severity filter, time-range filter, auto-refresh, or rotated-file
traversal. Each is a documented follow-up.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from findajob.admin.jsonl_tail import tail_events

router = APIRouter()

_TAIL_LIMIT = 200


def _parse_event_filter(raw: str) -> set[str]:
    return {p.strip() for p in raw.split(",") if p.strip()}


@router.get("/tools/logs/pipeline/", response_class=HTMLResponse)
def logs_pipeline(
    request: Request,
    event: str = "",
) -> HTMLResponse:
    base_root: Path = request.app.state.base_root
    log_path = base_root / "logs" / "pipeline.jsonl"
    selected_events = _parse_event_filter(event)

    # tail_events yields newest-first.
    # Pass 1 — collect all observed event names from the full tail for the
    # dropdown (when no filter is active).  Pass 2 — collect the display
    # rows, honouring the filter cap.
    all_events: list[dict] = []
    observed_names: set[str] = set()
    raw_events: list[dict] = list(tail_events(log_path))

    for ev in raw_events:
        name = ev.get("event", "")
        if name:
            observed_names.add(name)

    for ev in raw_events:
        name = ev.get("event", "")
        if selected_events and name not in selected_events:
            continue
        all_events.append(ev)
        if len(all_events) >= _TAIL_LIMIT:
            break

    # When a filter is active, restrict the dropdown to what's visible so
    # non-matching names don't leak into the rendered HTML.
    if selected_events:
        observed_names = {n for n in observed_names if n in selected_events}

    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="tools/logs.html",
        context={
            "events": all_events,
            "observed_names": sorted(observed_names),
            "selected_events": sorted(selected_events),
        },
    )
