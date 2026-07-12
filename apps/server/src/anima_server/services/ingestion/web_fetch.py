"""Opt-in server-side URL fetching for web captures.

Disabled by default: the local-first threat model expects the desktop client
to supply captured HTML. When enabled (ANIMA_WEB_CAPTURE_URL_FETCH_ENABLED),
every hop is validated against SSRF guards — http(s) only, no private,
loopback, link-local, or otherwise non-public addresses — with size and time
limits. Hosts are re-validated per redirect; DNS rebinding between the guard
check and the request is accepted as out of scope for this threat model.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urljoin, urlparse

import httpx

from anima_server.config import settings

_MAX_REDIRECTS = 3
_ALLOWED_CONTENT_TYPES = frozenset({"text/html", "application/xhtml+xml"})
_USER_AGENT = "anima-web-capture/1.0"


class WebFetchDisabledError(RuntimeError):
    """Server-side URL fetching is disabled by configuration."""


class UnsafeFetchUrlError(ValueError):
    """The URL failed the SSRF guards."""


class WebFetchError(ValueError):
    """The target could not be fetched or is not an HTML page."""


def fetch_capture_html(
    url: str,
    *,
    transport: httpx.BaseTransport | None = None,
) -> tuple[str, str]:
    """Fetch *url* and return ``(final_url, html)``.

    The *transport* parameter exists for tests; production callers use the
    default HTTP transport.
    """
    if not settings.web_capture_url_fetch_enabled:
        raise WebFetchDisabledError(
            "Server-side URL fetching is disabled "
            "(set ANIMA_WEB_CAPTURE_URL_FETCH_ENABLED=true to allow it)."
        )

    max_bytes = settings.web_capture_url_fetch_max_bytes
    current = url
    try:
        with httpx.Client(
            timeout=settings.web_capture_url_fetch_timeout,
            follow_redirects=False,
            transport=transport,
        ) as client:
            for _hop in range(_MAX_REDIRECTS + 1):
                require_public_http_url(current)
                with client.stream(
                    "GET", current, headers={"User-Agent": _USER_AGENT}
                ) as response:
                    if response.is_redirect:
                        location = response.headers.get("location")
                        if not location:
                            raise WebFetchError(
                                "Redirect response without a Location header."
                            )
                        current = urljoin(current, location)
                        continue
                    if response.status_code != 200:
                        raise WebFetchError(
                            f"Fetch failed with HTTP {response.status_code}."
                        )
                    return current, _read_html_body(response, max_bytes=max_bytes)
            raise WebFetchError("Too many redirects.")
    except httpx.HTTPError as exc:
        raise WebFetchError(f"Fetch failed: {exc}") from exc


def _read_html_body(response: httpx.Response, *, max_bytes: int) -> str:
    content_type = (
        response.headers.get("content-type", "").split(";")[0].strip().lower()
    )
    if content_type not in _ALLOWED_CONTENT_TYPES:
        raise WebFetchError(
            f"Unsupported content type {content_type or 'unknown'!r}; "
            "expected an HTML page."
        )
    declared = response.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > max_bytes:
        raise WebFetchError(f"Page exceeds the {max_bytes}-byte fetch limit.")

    received = bytearray()
    for chunk in response.iter_bytes():
        received.extend(chunk)
        if len(received) > max_bytes:
            raise WebFetchError(f"Page exceeds the {max_bytes}-byte fetch limit.")
    encoding = response.charset_encoding or "utf-8"
    return bytes(received).decode(encoding, errors="replace")


def require_public_http_url(url: str) -> None:
    """Reject URLs that are not absolute http(s) or resolve to non-public hosts."""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise UnsafeFetchUrlError("url must be an absolute http(s) URL")
    host = parsed.hostname
    try:
        addresses: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = [
            ipaddress.ip_address(host)
        ]
    except ValueError:
        addresses = _resolve_addresses(
            host, parsed.port or (443 if parsed.scheme == "https" else 80)
        )
    for address in addresses:
        if not _is_public_address(address):
            raise UnsafeFetchUrlError(
                f"Refusing to fetch non-public address for host {host!r}."
            )


def _resolve_addresses(
    host: str, port: int
) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except OSError as exc:
        raise UnsafeFetchUrlError(f"Could not resolve host {host!r}.") from exc
    addresses = [ipaddress.ip_address(info[4][0]) for info in infos]
    if not addresses:
        raise UnsafeFetchUrlError(f"Could not resolve host {host!r}.")
    return addresses


def _is_public_address(
    address: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> bool:
    # is_global covers ranges a flag blacklist misses (e.g. the shared
    # address space 100.64.0.0/10, which is neither private nor reserved),
    # but ipaddress reports some multicast ranges as global — only public
    # unicast destinations are acceptable fetch targets.
    return address.is_global and not address.is_multicast


__all__ = [
    "UnsafeFetchUrlError",
    "WebFetchDisabledError",
    "WebFetchError",
    "fetch_capture_html",
    "require_public_http_url",
]
