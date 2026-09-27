"""``require_htmx`` — refuse unsafe requests that HTMX did not send.

Second line of defence behind ``CrossSiteRequestMiddleware`` for routes
that only HTMX ever calls. HTMX sets ``HX-Request: true`` on every request
it makes. A cross-site HTML form cannot set a custom header at all, and
cross-site JavaScript can only send one after a CORS preflight this app
never approves. So a state-changing route that demands the header cannot
be driven from another site, even by a browser too old to send
``Sec-Fetch-Site`` or ``Origin``.

Apply it as a FastAPI dependency — at ``include_router`` time for a router
whose unsafe routes are all HTMX-driven, or per route on a mixed module.
Safe methods pass, so a router's GET pages are unaffected. Do NOT put it on
a handler reached by a plain ``<form method="post">``, a ``fetch`` call, or
an HTMX-aware handler with a deliberate non-HTMX fallback:
``tests/test_web_htmx_guard.py`` keeps the inventory and fails on either
kind of misclassification.
"""

from __future__ import annotations

from fastapi import HTTPException, Request

_SAFE_METHODS: frozenset[str] = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})


def require_htmx(request: Request) -> None:
    """Raise 403 on POST/PUT/PATCH/DELETE without an ``HX-Request`` header."""
    if request.method in _SAFE_METHODS:
        return
    if request.headers.get("HX-Request"):
        return
    raise HTTPException(status_code=403, detail="This action is only available from the findajob UI.")
