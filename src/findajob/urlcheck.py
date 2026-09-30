"""URL checks for job links: scheme allowlist, host matching, fetch targets.

A job URL reaches the pipeline from email anchors, feed responses and the
manual-ingest form. It is later shown to the operator and, for a few sources,
fetched server-side. These helpers keep the accepted set small: ``http(s)``
links with a real host, and, for a fetch, ``https`` links to a public host.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit

_HTTP_SCHEMES = frozenset({"http", "https"})


def _parts(url: object) -> tuple[str, str, str] | None:
    """Return ``(scheme, hostname, path)`` for a URL string, or ``None``."""
    if not isinstance(url, str):
        return None
    try:
        split = urlsplit(url.strip())
        host = split.hostname
    except ValueError:
        return None
    if not host:
        return None
    return split.scheme.lower(), host.lower(), split.path


def is_http_url(url: object) -> bool:
    """True for an ``http://`` or ``https://`` URL that names a host."""
    parts = _parts(url)
    return parts is not None and parts[0] in _HTTP_SCHEMES


def _is_public_host(host: str) -> bool:
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        return False
    try:
        return ipaddress.ip_address(host).is_global
    except ValueError:
        return True  # a DNS name; resolution is not checked here


def is_fetchable_url(url: object) -> bool:
    """True when the pipeline may fetch ``url`` server-side.

    ``https`` only, and never a loopback, private or link-local IP literal
    or a local-only hostname.
    """
    parts = _parts(url)
    return parts is not None and parts[0] == "https" and _is_public_host(parts[1])


def resolves_to_public(url: object) -> bool:
    """True when every address the URL's host resolves to is a public one.

    Fails closed: an unresolvable host is rejected. This narrows, but does not
    close, DNS rebinding (the fetch resolves again); the caller re-checks each
    redirect hop.
    """
    parts = _parts(url)
    if parts is None:
        return False
    host = parts[1]
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except (OSError, UnicodeError):
        return False
    addrs = {str(info[4][0]).split("%", 1)[0] for info in infos}
    if not addrs:
        return False
    try:
        return all(ipaddress.ip_address(a).is_global for a in addrs)
    except ValueError:
        return False


def url_matches(url: object, domain: str, path_prefix: str = "/") -> bool:
    """True when ``url`` is http(s), its host is ``domain`` or a subdomain of
    it, and its path starts with ``path_prefix``.

    Compares the parsed host, so ``https://evil.example/?u=indeed.com/viewjob``
    and ``https://indeed.com.evil.example/viewjob`` do not match ``indeed.com``.
    """
    parts = _parts(url)
    if parts is None or parts[0] not in _HTTP_SCHEMES:
        return False
    _, host, path = parts
    return (host == domain or host.endswith("." + domain)) and path.startswith(path_prefix)
