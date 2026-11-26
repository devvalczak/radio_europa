"""Outbound HTTP for modules, with an SSRF guard.

Modules fetch URLs that a user typed in (RSS feeds, ...). Without a guard, a stolen session could
point the server at localhost services, the Docker network or cloud metadata. We therefore refuse
targets that resolve to private/loopback/link-local addresses and re-check every redirect hop.
(DNS rebinding between check and connect is not covered; acceptable for a single-user server.)
"""

import ipaddress
import socket
from urllib.parse import urljoin, urlsplit

import httpx

MAX_REDIRECTS = 3
USER_AGENT = "Jarvis/0.1 (+personal dashboard)"


class FetchError(Exception):
    pass


def check_host(host: str, port: int) -> None:
    """Raise FetchError unless `host` resolves only to publicly routable addresses."""
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise FetchError("host not found") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if not ip.is_global:
            raise FetchError("address is not publicly routable")


def _check_target(url: str, allow_private: bool = False) -> None:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise FetchError("only http(s) URLs are allowed")
    if not allow_private:
        check_host(parts.hostname, parts.port or (443 if parts.scheme == "https" else 80))


# tests swap this for an httpx.MockTransport (a fake server); None = the real network
_transport: httpx.BaseTransport | None = None


def get(url: str, *, params: dict | None = None, timeout: float = 10.0, max_bytes: int = 2_000_000) -> httpx.Response:
    """GET with size cap, timeout and SSRF checks. Raises FetchError on any failure (incl. HTTP >= 400)."""
    try:
        with httpx.Client(follow_redirects=False, timeout=timeout, headers={"User-Agent": USER_AGENT}) as client:
            for _ in range(MAX_REDIRECTS + 1):
                _check_target(url)
                with client.stream("GET", url, params=params) as resp:
                    if resp.is_redirect:
                        url, params = urljoin(url, resp.headers.get("location", "")), None
                        continue
                    if resp.status_code >= 400:
                        raise FetchError(f"HTTP {resp.status_code}")
                    body = bytearray()
                    for chunk in resp.iter_bytes():
                        body.extend(chunk)
                        if len(body) > max_bytes:
                            raise FetchError("response too large")
                    return httpx.Response(resp.status_code, headers=resp.headers, content=bytes(body), request=resp.request)
            raise FetchError("too many redirects")
    except httpx.HTTPError as exc:
        raise FetchError(f"request failed: {type(exc).__name__}") from exc


def request(method: str, url: str, *, headers: dict | None = None, content: bytes | str | None = None,
            auth: tuple[str, str] | None = None, timeout: float = 20.0, max_bytes: int = 5_000_000,
            allow_private: bool = False) -> httpx.Response:
    """Any method (PROPFIND, REPORT, PUT, DELETE, ...) with the same guards as `get`. HTTP errors are returned, not
    raised (WebDAV callers need 207/404/412); network failures raise FetchError. Redirects are followed for
    GET/PROPFIND/REPORT only; credentials are never sent to another host after a redirect.
    `allow_private` (servers in the user's LAN) only when the user configured such a server on purpose."""
    host = urlsplit(url).hostname
    try:
        with httpx.Client(follow_redirects=False, timeout=timeout, transport=_transport,
                          headers={"User-Agent": USER_AGENT}) as client:
            for _ in range(MAX_REDIRECTS + 1):
                _check_target(url, allow_private)
                same_host = urlsplit(url).hostname == host
                with client.stream(method, url, headers=headers, content=content,
                                   auth=auth if same_host else None) as resp:
                    if resp.is_redirect and method in ("GET", "PROPFIND", "REPORT"):
                        url = urljoin(url, resp.headers.get("location", ""))
                        continue
                    body = bytearray()
                    for chunk in resp.iter_bytes():
                        body.extend(chunk)
                        if len(body) > max_bytes:
                            raise FetchError("response too large")
                    out = httpx.Response(resp.status_code, headers=resp.headers, content=bytes(body), request=resp.request)
                    out.final_url = url  # type: ignore[attr-defined]
                    return out
            raise FetchError("too many redirects")
    except httpx.HTTPError as exc:
        raise FetchError(f"request failed: {type(exc).__name__}") from exc
