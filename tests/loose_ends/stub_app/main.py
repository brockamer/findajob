"""Synthetic web app for #572 Phase 2 integration test.

Exposes:
  GET /empty-no-cta              — cat 3 positive (empty table, no CTA)
  GET /empty-with-cta            — cat 3 negative (empty table, has CTA)
  GET /flow-without-exit         — cat 2 positive (status page, no back/undo)
  GET /flow-with-exit            — cat 2 negative (status page, has back button)
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

app = FastAPI()


@app.get("/empty-no-cta", response_class=HTMLResponse)
def empty_no_cta() -> str:
    return """<!doctype html><html><body>
        <h1>Applied Jobs</h1>
        <table id="applied-jobs"><tbody></tbody></table>
    </body></html>"""


@app.get("/empty-with-cta", response_class=HTMLResponse)
def empty_with_cta() -> str:
    return """<!doctype html><html><body>
        <h1>Applied Jobs</h1>
        <p>No applications yet. Mark a job applied from the dashboard.</p>
        <table id="applied-jobs"><tbody></tbody></table>
        <a href="/board/dashboard">Go to dashboard</a>
    </body></html>"""


@app.get("/flow-without-exit", response_class=HTMLResponse)
def flow_without_exit() -> str:
    return """<!doctype html><html><body>
        <h1>Status: Interviewing</h1>
        <p>Your interview status has been recorded.</p>
    </body></html>"""


@app.get("/flow-with-exit", response_class=HTMLResponse)
def flow_with_exit() -> str:
    return """<!doctype html><html><body>
        <h1>Status: Interviewing</h1>
        <p>Your interview status has been recorded.</p>
        <button>Back to Applied</button>
        <button>Undo</button>
    </body></html>"""
