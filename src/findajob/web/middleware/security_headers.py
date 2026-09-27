"""ASGI middleware that adds a Content-Security-Policy to every response.

The policy is a second layer behind the Markdown sanitizer
(``findajob.web.markdown``), not a replacement for it. The templates today
rely on inline ``<script>`` blocks, inline ``on*=`` handlers, ``hx-on``
attributes and Alpine.js expressions, and Tailwind's Play CDN compiles CSS in
the browser and injects it as an inline ``<style>``. So ``script-src`` keeps
``'unsafe-inline'`` and ``'unsafe-eval'`` and ``style-src`` keeps
``'unsafe-inline'``; tightening those needs the templates moved off inline
script first.

What the policy does enforce today:

- Scripts load only from this origin and the three CDN hosts the templates
  name. An injected ``<script src>`` pointing anywhere else is refused.
- ``connect-src`` falls back to ``'self'``: ``fetch``/XHR/EventSource cannot
  send page data to another origin.
- ``object-src 'none'``, ``base-uri 'self'``, ``form-action 'self'``: no
  plugins, no ``<base>`` hijack of relative URLs, no form that posts the
  operator's input to another site.
- ``frame-ancestors 'none'``: no other site can frame the UI (clickjacking).

Images may load from any http or https origin because scraped job
descriptions and the docs carry remote images, and plain-HTTP LAN instances
exist (an https page already blocks http images as mixed content).

``frame-ancestors 'none'`` means the UI cannot be shown inside another site's
iframe, including a homelab dashboard that embeds it.

A route that sets its own ``Content-Security-Policy`` keeps it: the
middleware only adds the header when the response has none, so a page can
opt into a stricter policy.
"""

from __future__ import annotations

from collections.abc import MutableMapping
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send

CONTENT_SECURITY_POLICY = "; ".join(
    (
        "default-src 'self'",
        "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://cdn.tailwindcss.com https://unpkg.com https://cdn.jsdelivr.net",
        "style-src 'self' 'unsafe-inline'",
        "img-src 'self' https: http:",
        "object-src 'none'",
        "base-uri 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    )
)

_CSP_HEADER = b"content-security-policy"
_CSP_VALUE = CONTENT_SECURITY_POLICY.encode("latin-1")


class SecurityHeadersMiddleware:
    """Add ``Content-Security-Policy`` to each HTTP response that lacks one.

    Pure ASGI: it rewrites only the ``http.response.start`` message and
    passes body messages and ``receive`` through untouched, so streaming
    responses (the onboarding interview's SSE stream) and disconnect handling
    behave exactly as without it.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                _add_csp(message)
            await send(message)

        await self.app(scope, receive, send_with_headers)


def _add_csp(message: MutableMapping[str, Any]) -> None:
    headers = list(message.get("headers", []))
    if any(name.lower() == _CSP_HEADER for name, _ in headers):
        return
    headers.append((_CSP_HEADER, _CSP_VALUE))
    message["headers"] = headers
