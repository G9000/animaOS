from __future__ import annotations

import ipaddress
from typing import Any

import httpx
import pytest
from anima_server.config import settings
from anima_server.services.ingestion import web_fetch
from anima_server.services.ingestion.web_fetch import (
    UnsafeFetchUrlError,
    WebFetchDisabledError,
    WebFetchError,
    fetch_capture_html,
    require_public_http_url,
)

HTML_BODY = "<html><body><article><h1>T</h1><p>Body.</p></article></body></html>"


@pytest.fixture()
def fetch_enabled(monkeypatch: Any):
    monkeypatch.setattr(settings, "web_capture_url_fetch_enabled", True)
    monkeypatch.setattr(
        web_fetch,
        "_resolve_addresses",
        lambda host, port: [ipaddress.ip_address("93.184.216.34")],
    )


def _transport(handler) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


def test_fetch_is_disabled_by_default() -> None:
    assert settings.web_capture_url_fetch_enabled is False
    with pytest.raises(WebFetchDisabledError):
        fetch_capture_html("https://example.com/")


def test_fetch_returns_html_and_final_url(fetch_enabled: None) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, headers={"content-type": "text/html; charset=utf-8"}, text=HTML_BODY
        )

    final_url, html = fetch_capture_html(
        "https://example.com/article", transport=_transport(handler)
    )
    assert final_url == "https://example.com/article"
    assert html == HTML_BODY


def test_fetch_follows_redirects_and_revalidates_each_hop(
    fetch_enabled: None, monkeypatch: Any
) -> None:
    validated: list[str] = []
    original = web_fetch.require_public_http_url

    def spy(url: str) -> None:
        validated.append(url)
        original(url)

    monkeypatch.setattr(web_fetch, "require_public_http_url", spy)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/start":
            return httpx.Response(302, headers={"location": "/final"})
        return httpx.Response(200, headers={"content-type": "text/html"}, text=HTML_BODY)

    final_url, html = fetch_capture_html(
        "https://example.com/start", transport=_transport(handler)
    )
    assert final_url == "https://example.com/final"
    assert html == HTML_BODY
    assert validated == ["https://example.com/start", "https://example.com/final"]


def test_fetch_rejects_redirect_to_private_address(fetch_enabled: None) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "http://127.0.0.1/internal"})

    with pytest.raises(UnsafeFetchUrlError):
        fetch_capture_html("https://example.com/start", transport=_transport(handler))


def test_fetch_rejects_too_many_redirects(fetch_enabled: None) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "/again"})

    with pytest.raises(WebFetchError, match="redirects"):
        fetch_capture_html("https://example.com/start", transport=_transport(handler))


def test_fetch_rejects_non_html_content_type(fetch_enabled: None) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, headers={"content-type": "application/pdf"}, content=b"%PDF-"
        )

    with pytest.raises(WebFetchError, match="content type"):
        fetch_capture_html("https://example.com/doc.pdf", transport=_transport(handler))


def test_fetch_rejects_oversized_pages(fetch_enabled: None, monkeypatch: Any) -> None:
    monkeypatch.setattr(settings, "web_capture_url_fetch_max_bytes", 64)

    def declared_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/html", "content-length": "100000"},
            text="x",
        )

    with pytest.raises(WebFetchError, match="fetch limit"):
        fetch_capture_html(
            "https://example.com/big", transport=_transport(declared_handler)
        )

    def streamed_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, headers={"content-type": "text/html"}, text="y" * 1000
        )

    with pytest.raises(WebFetchError, match="fetch limit"):
        fetch_capture_html(
            "https://example.com/big", transport=_transport(streamed_handler)
        )


def test_fetch_wraps_transport_errors(fetch_enabled: None) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    with pytest.raises(WebFetchError, match="Fetch failed"):
        fetch_capture_html("https://example.com/", transport=_transport(handler))


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.com/file",
        "file:///etc/passwd",
        "https:///nohost",
        "http://127.0.0.1/",
        "http://[::1]/",
        "http://10.1.2.3/admin",
        "http://192.168.0.10/",
        "http://169.254.169.254/latest/meta-data/",
        "http://100.64.0.1/",
        "http://192.0.2.10/",
        "http://224.0.0.1/",
        "http://[ff02::1]/",
    ],
)
def test_require_public_http_url_rejects_unsafe_targets(url: str) -> None:
    with pytest.raises(UnsafeFetchUrlError):
        require_public_http_url(url)


def test_require_public_http_url_rejects_hosts_resolving_to_private_ranges(
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr(
        web_fetch,
        "_resolve_addresses",
        lambda host, port: [
            ipaddress.ip_address("93.184.216.34"),
            ipaddress.ip_address("10.0.0.5"),
        ],
    )
    with pytest.raises(UnsafeFetchUrlError, match="non-public"):
        require_public_http_url("https://rebind.example.com/")


def test_require_public_http_url_accepts_public_hosts(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        web_fetch,
        "_resolve_addresses",
        lambda host, port: [ipaddress.ip_address("93.184.216.34")],
    )
    require_public_http_url("https://example.com/article")
