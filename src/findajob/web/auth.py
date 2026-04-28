"""HTTP Basic Auth middleware for internet-exposed findajob instances (#327).

Installed iff `FINDAJOB_AUTH_USER` and `FINDAJOB_AUTH_PASS` env vars are both
set and non-empty. When unset, the middleware is not added at all and every
request passes through. When set, every request to a non-allowlisted path
must present matching HTTP Basic Auth credentials or gets 401.

Threat model: drive-by scanning of internet-exposed per-tester instances
(`findajob-{tester}.example.com`). Defense layers upstream are TLS
termination + geo-IP restriction. This is shared-secret auth, not identity.
"""

from __future__ import annotations

import base64
import hmac
import logging
import os
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

logger = logging.getLogger(__name__)

_ALLOWLIST_PREFIXES: tuple[str, ...] = ("/static/",)
_ALLOWLIST_EXACT: frozenset[str] = frozenset({"/healthz", "/favicon.ico"})


class BasicAuthMiddleware(BaseHTTPMiddleware):
    def __init__(
        self,
        app: ASGIApp,
        *,
        username: str,
        password: str,
        realm: str = "findajob",
    ) -> None:
        super().__init__(app)
        self._username = username.encode("utf-8")
        self._password = password.encode("utf-8")
        self._challenge = f'Basic realm="{realm}"'

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if _is_allowlisted(request.url.path):
            return await call_next(request)
        if not self._authorized(request):
            return Response(
                content="Unauthorized",
                status_code=401,
                headers={"WWW-Authenticate": self._challenge},
                media_type="text/plain",
            )
        return await call_next(request)

    def _authorized(self, request: Request) -> bool:
        header = request.headers.get("authorization", "")
        if not header.lower().startswith("basic "):
            return False
        try:
            decoded = base64.b64decode(header[6:].strip(), validate=True)
        except ValueError:
            return False
        if b":" not in decoded:
            return False
        user, _, pw = decoded.partition(b":")
        return hmac.compare_digest(user, self._username) and hmac.compare_digest(pw, self._password)


def _is_allowlisted(path: str) -> bool:
    if path in _ALLOWLIST_EXACT:
        return True
    return any(path.startswith(p) for p in _ALLOWLIST_PREFIXES)


def install_basic_auth(
    app: ASGIApp,
    *,
    username: str | None = None,
    password: str | None = None,
) -> bool:
    """Add `BasicAuthMiddleware` to `app` iff credentials are present.

    With no kwargs, reads `FINDAJOB_AUTH_USER` / `FINDAJOB_AUTH_PASS` from the
    environment. Returns True when middleware was installed, False otherwise.

    Always emits one log line so the operator can grep startup logs to confirm
    the auth state. Partial misconfiguration (only one var set) emits a
    WARNING — silent fail-open on a typo'd compose.yaml is a real foot-gun for
    internet-exposed instances and observability is the cheap defense.
    """
    user = username if username is not None else os.environ.get("FINDAJOB_AUTH_USER", "")
    pw = password if password is not None else os.environ.get("FINDAJOB_AUTH_PASS", "")
    if user and pw:
        app.add_middleware(BasicAuthMiddleware, username=user, password=pw)  # type: ignore[attr-defined]
        logger.info("basic auth: ENABLED (FINDAJOB_AUTH_USER + FINDAJOB_AUTH_PASS both set)")
        return True
    if user or pw:
        which_set = "FINDAJOB_AUTH_USER" if user else "FINDAJOB_AUTH_PASS"
        which_missing = "FINDAJOB_AUTH_PASS" if user else "FINDAJOB_AUTH_USER"
        logger.warning(
            "basic auth: DISABLED — %s is set but %s is empty. Set both to enable, "
            "or unset both to silence this warning.",
            which_set,
            which_missing,
        )
        return False
    logger.info("basic auth: DISABLED (no env vars set)")
    return False
