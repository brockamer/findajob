"""ASGI middleware that rejects cross-site state-changing requests.

Browsers replay HTTP Basic Auth (and any ambient credential) on cross-site
form posts and ``fetch`` calls. Without a request-origin check, a page on
another site can drive every POST route in the UI as the logged-in
operator. This middleware is the check. It is always installed, whether or
not ``BasicAuthMiddleware`` has credentials, because a LAN-only instance
with no auth is just as reachable from a hostile page in the operator's
browser.

Decision order for POST, PUT, PATCH and DELETE:

1. ``Sec-Fetch-Site`` present — the browser's own verdict is authoritative.
   ``cross-site`` and ``same-site`` are rejected; ``same-origin`` and
   ``none`` (a typed URL or bookmark) pass. Trusting the browser here also
   keeps a reverse proxy that rewrites ``Host`` from locking the operator
   out on a modern browser.
2. Otherwise ``Origin`` present — its hostname must equal the request's
   ``Host`` hostname, or the first ``X-Forwarded-Host`` hostname when a
   proxy forwards one. A literal ``null`` origin (cross-origin redirect
   chains, sandboxed frames) is rejected. Hostnames only: Basic Auth is not
   replayed across ports, and proxies disagree on default-port elision.
3. Neither header — the request passes. Scripts, ``curl`` and the test
   client send neither, and they are not a browser replaying credentials.

Trusting ``X-Forwarded-Host`` is safe for this check: a browser cannot set
it on a cross-site request without a CORS preflight that this app never
approves, and a non-browser client is not a CSRF vector.

Safe methods (GET, HEAD, OPTIONS, TRACE) are never touched. The check runs
before routing, so it covers unsafe methods no route uses today.
"""

from __future__ import annotations

import logging
from urllib.parse import urlsplit

from starlette.types import ASGIApp, Receive, Scope, Send

logger = logging.getLogger(__name__)

_SAFE_METHODS: frozenset[str] = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})
_REJECTED_FETCH_SITES: frozenset[str] = frozenset({"cross-site", "same-site"})
_REJECT_BODY = b"Cross-site request rejected."


class CrossSiteRequestMiddleware:
    """Reject unsafe-method requests that a browser marks as cross-site."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] in _SAFE_METHODS:
            await self.app(scope, receive, send)
            return

        headers = _Headers(scope)
        reason = _rejection_reason(headers)
        if reason is None:
            await self.app(scope, receive, send)
            return

        logger.warning(
            "cross-site request rejected: %s %s (%s; sec-fetch-site=%r origin=%r host=%r)",
            scope["method"],
            scope.get("path", ""),
            reason,
            headers.get("sec-fetch-site"),
            headers.get("origin"),
            headers.get("host"),
        )
        await send(
            {
                "type": "http.response.start",
                "status": 403,
                "headers": [
                    (b"content-type", b"text/plain; charset=utf-8"),
                    (b"content-length", str(len(_REJECT_BODY)).encode("ascii")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": _REJECT_BODY})


class _Headers:
    """Minimal case-insensitive view over raw ASGI headers (first value wins)."""

    def __init__(self, scope: Scope) -> None:
        self._raw: dict[str, str] = {}
        for key, value in scope.get("headers", []):
            name = key.decode("latin-1").lower()
            self._raw.setdefault(name, value.decode("latin-1"))

    def get(self, name: str) -> str | None:
        return self._raw.get(name)


def _rejection_reason(headers: _Headers) -> str | None:
    """Return why the request is cross-site, or None when it may proceed."""
    fetch_site = headers.get("sec-fetch-site")
    if fetch_site is not None:
        site = fetch_site.strip().lower()
        if site in _REJECTED_FETCH_SITES:
            return f"sec-fetch-site is {site}"
        return None

    origin = headers.get("origin")
    if origin is None:
        return None
    origin_host = _hostname(origin)
    if origin_host is None:
        return "origin has no host"
    expected = _expected_hosts(headers)
    if origin_host not in expected:
        return "origin host does not match request host"
    return None


def _expected_hosts(headers: _Headers) -> set[str]:
    hosts: set[str] = set()
    host = headers.get("host")
    if host:
        hostname = _hostname("//" + host)
        if hostname:
            hosts.add(hostname)
    forwarded = headers.get("x-forwarded-host")
    if forwarded:
        first = forwarded.split(",", 1)[0].strip()
        hostname = _hostname("//" + first)
        if hostname:
            hosts.add(hostname)
    return hosts


def _hostname(value: str) -> str | None:
    """Lower-cased hostname of a URL or ``//host[:port]`` string, or None.

    ``urlsplit`` handles bracketed IPv6 literals and strips the port. A bare
    ``Origin: null`` has no host and yields None, which callers reject.
    """
    try:
        return urlsplit(value.strip()).hostname or None
    except ValueError:
        return None
