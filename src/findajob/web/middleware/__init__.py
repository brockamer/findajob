"""ASGI middleware for the findajob web app."""

from findajob.web.middleware.cross_site import CrossSiteRequestMiddleware
from findajob.web.middleware.disconnect_state import (
    SCOPE_KEY,
    DisconnectStateMiddleware,
)
from findajob.web.middleware.security_headers import (
    CONTENT_SECURITY_POLICY,
    SecurityHeadersMiddleware,
)

__all__ = [
    "CONTENT_SECURITY_POLICY",
    "SCOPE_KEY",
    "CrossSiteRequestMiddleware",
    "DisconnectStateMiddleware",
    "SecurityHeadersMiddleware",
]
