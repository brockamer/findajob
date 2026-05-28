"""HTTP Basic Auth middleware for internet-exposed findajob instances (#327).

Installed iff `FINDAJOB_AUTH_USER` and `FINDAJOB_AUTH_PASS` env vars are both
set and non-empty. When unset, the middleware is not added at all and every
request passes through. When set, every request to a non-allowlisted path
must present matching HTTP Basic Auth credentials or gets 401.

Threat model: drive-by scanning of internet-exposed instances. Defense
layers upstream are TLS termination + geo-IP restriction. This is
shared-secret auth, not identity.
"""

from __future__ import annotations

import base64
import hmac
import logging
import os
import secrets
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

logger = logging.getLogger(__name__)

_ALLOWLIST_PREFIXES: tuple[str, ...] = ("/static/",)
_ALLOWLIST_EXACT: frozenset[str] = frozenset({"/healthz", "/favicon.ico"})


class BasicAuthMiddleware(BaseHTTPMiddleware):
    """Always-installed middleware that reads credentials from ``app.state``.

    When ``app.state.auth_user`` and ``app.state.auth_pass`` are both
    non-empty, every non-allowlisted request must present matching HTTP
    Basic Auth credentials.  When either is falsy the middleware passes
    through — identical to not having auth installed at all.

    This dynamic approach lets the onboarding flow set credentials at
    runtime (writing to ``app.state`` + ``data/.env``) without a
    container restart (#895).
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        realm: str = "findajob",
    ) -> None:
        super().__init__(app)
        self._challenge = f'Basic realm="{realm}"'

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if _is_allowlisted(request.url.path):
            return await call_next(request)
        user = getattr(request.app.state, "auth_user", "")
        pw = getattr(request.app.state, "auth_pass", "")
        if not user or not pw:
            return await call_next(request)
        if not self._authorized(request, user.encode("utf-8"), pw.encode("utf-8")):
            return Response(
                content="Unauthorized",
                status_code=401,
                headers={"WWW-Authenticate": self._challenge},
                media_type="text/plain",
            )
        return await call_next(request)

    def _authorized(self, request: Request, expected_user: bytes, expected_pw: bytes) -> bool:
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
        return hmac.compare_digest(user, expected_user) and hmac.compare_digest(pw, expected_pw)


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
    """Always add ``BasicAuthMiddleware`` and seed ``app.state`` credentials.

    With no kwargs, reads ``FINDAJOB_AUTH_USER`` / ``FINDAJOB_AUTH_PASS``
    from the environment.  Returns True when credentials were found at
    startup, False otherwise.

    The middleware is installed unconditionally — when both
    ``app.state.auth_user`` and ``app.state.auth_pass`` are falsy it
    passes through (no auth enforced).  This lets the onboarding flow
    set credentials at runtime without a container restart (#895).
    """
    user = username if username is not None else os.environ.get("FINDAJOB_AUTH_USER", "").strip()
    pw = password if password is not None else os.environ.get("FINDAJOB_AUTH_PASS", "").strip()

    app.state.auth_user = user  # type: ignore[attr-defined]
    app.state.auth_pass = pw  # type: ignore[attr-defined]
    app.state.setup_token = ""  # type: ignore[attr-defined]
    app.add_middleware(BasicAuthMiddleware)  # type: ignore[attr-defined]

    if user and pw:
        logger.info("basic auth: ENABLED (FINDAJOB_AUTH_USER + FINDAJOB_AUTH_PASS both set)")
        return True

    # Middleware is fail-open below this point — generate a one-time setup
    # token (#895 advisor finding) so /onboarding/auth requires log-level
    # access to complete.  Covers both no-creds and partial-creds branches
    # — a typo'd compose.yaml leaving only USER or PASS set was previously
    # a drive-by squat window, since the middleware passed through but no
    # token gate was active.
    token = secrets.token_urlsafe(24)
    app.state.setup_token = token  # type: ignore[attr-defined]

    if user or pw:
        which_set = "FINDAJOB_AUTH_USER" if user else "FINDAJOB_AUTH_PASS"
        which_missing = "FINDAJOB_AUTH_PASS" if user else "FINDAJOB_AUTH_USER"
        logger.warning(
            "basic auth: DISABLED — %s is set but %s is empty. Set both to enable, "
            "or unset both to silence this warning.",
            which_set,
            which_missing,
        )
        logger.info(
            "FINDAJOB_SETUP_TOKEN=%s — paste this into the onboarding auth form to set your password",
            token,
        )
        return False

    logger.info("basic auth: DISABLED (no env vars set — set via onboarding or Fly secrets)")
    logger.info(
        "FINDAJOB_SETUP_TOKEN=%s — paste this into the onboarding auth form to set your password",
        token,
    )
    return False
