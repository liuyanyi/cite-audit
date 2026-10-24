"""Small, strict boundary for all outbound HTTP requests.

DNS is checked *and pinned* for the connection, not merely checked before a
second, untrusted resolver lookup. Redirect destinations pass the same gate.
"""
from __future__ import annotations

import ipaddress
import re
import socket
import threading
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx

MAX_RESPONSE_BYTES = 2 * 1024 * 1024


class SecurityError(ValueError):
    """An outbound URL or response did not meet the network safety policy."""


def _public_ip(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value.split("%", 1)[0])
        if getattr(address, "ipv4_mapped", None):
            address = address.ipv4_mapped
        return address.is_global and not any((address.is_multicast, address.is_unspecified,
                                              address.is_loopback, address.is_link_local,
                                              address.is_reserved, address.is_private))
    except ValueError:
        return False


@dataclass(frozen=True)
class CheckedURL:
    url: str
    hostname: str
    port: int
    addresses: tuple[str, ...]


def validate_url(url: str, *, https_only: bool = False, resolve: bool = True) -> CheckedURL:
    """Allow only public HTTP(S), with no credentials or ambiguous host syntax."""
    if not isinstance(url, str) or len(url) > 8192 or any(ord(c) < 33 for c in url):
        raise SecurityError("URL contains unsupported characters")
    try:
        parsed = urlsplit(url)
        host, port = parsed.hostname, parsed.port
    except (ValueError, UnicodeError):
        raise SecurityError("URL is malformed") from None
    if parsed.scheme not in ({"https"} if https_only else {"http", "https"}):
        raise SecurityError("Only public HTTPS URLs are allowed" if https_only else "Only HTTP(S) URLs are allowed")
    if not host or parsed.username is not None or parsed.password is not None or "@" in parsed.netloc:
        raise SecurityError("URL credentials and empty hosts are not allowed")
    if "\\" in url or "%" in host or parsed.fragment:
        raise SecurityError("Ambiguous URLs and fragments are not allowed")
    host = host.rstrip(".").lower()
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError:
        raise SecurityError("URL hostname is invalid") from None
    if host == "localhost" or "." not in host and ":" not in host or host.endswith((".localhost", ".local", ".internal", ".lan", ".home", ".test", ".invalid", ".example")):
        raise SecurityError("Local and non-public hostnames are not allowed")
    port = port or (443 if parsed.scheme == "https" else 80)
    if port not in (80, 443):
        raise SecurityError("Only standard HTTP(S) ports are allowed")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address:
        if not _public_ip(host):
            raise SecurityError("Non-public network addresses are not allowed")
        addresses = (host,)
    elif resolve:
        try:
            answers = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
            addresses = tuple(dict.fromkeys(answer[4][0] for answer in answers))
        except (socket.gaierror, OSError):
            raise SecurityError("Hostname could not be resolved safely") from None
        if not addresses or any(not _public_ip(item) for item in addresses):
            raise SecurityError("Hostname resolves to a non-public network address")
    else:
        addresses = ()
    return CheckedURL(url, host, port, addresses)


def validate_base_url(url: str, *, resolve: bool = True) -> str:
    checked = validate_url(url, https_only=True, resolve=resolve)
    if urlsplit(checked.url).query:
        raise SecurityError("API base URL must not contain a query")
    return checked.url.rstrip("/")


class PublicNetworkTransport(httpx.BaseTransport):
    """Pin the validated IP while keeping original Host and TLS SNI identity."""

    def __init__(self, inner: httpx.BaseTransport | None = None):
        self.inner = inner
        self._transports: dict[tuple[str, str, int], httpx.BaseTransport] = {}
        self._lock = threading.Lock()

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        checked = validate_url(str(request.url))
        headers = request.headers.copy()
        headers["Host"] = request.url.netloc.decode("ascii")
        extensions = dict(request.extensions)
        extensions["sni_hostname"] = checked.hostname
        pinned = httpx.Request(request.method, request.url.copy_with(host=checked.addresses[0]),
                               headers=headers, stream=request.stream, extensions=extensions)
        # A separate connection pool per original origin is essential: pooling
        # only by the pinned IP could reuse host A's TLS session for host B on
        # the same CDN IP, bypassing host B's certificate identity check.
        if self.inner is not None:
            transport = self.inner
        else:
            origin = (request.url.scheme, checked.hostname, checked.port)
            with self._lock:
                if origin not in self._transports:
                    self._transports[origin] = httpx.HTTPTransport(retries=0)
                transport = self._transports[origin]
        return transport.handle_request(pinned)

    def close(self) -> None:
        if self.inner is not None:
            self.inner.close()
        for transport in self._transports.values():
            transport.close()


def public_client() -> httpx.Client:
    return httpx.Client(transport=PublicNetworkTransport(), follow_redirects=False,
                        trust_env=False, headers={"User-Agent": "cite-audit/1.0 (local citation workbench)"})


def safe_request(client: httpx.Client, method: str, url: str, *, max_bytes: int = MAX_RESPONSE_BYTES,
                 max_redirects: int = 5, on_redirect: Any = None, **kwargs: Any) -> httpx.Response:
    """Bound response size and explicitly check every redirect before sending.

    In production PublicNetworkTransport additionally pins the DNS answer. An
    injected MockTransport is useful for offline contract tests only.
    """
    method = method.upper()
    validate_url(url, resolve=False)
    original_origin = (urlsplit(url).scheme, urlsplit(url).netloc.lower())
    for redirect in range(max_redirects + 1):
        with client.stream(method, url, follow_redirects=False, **kwargs) as response:
            if response.status_code in (301, 302, 303, 307, 308):
                if redirect == max_redirects:
                    raise SecurityError("Too many redirects")
                location = response.headers.get("location")
                if not location:
                    raise SecurityError("Redirect has no destination")
                target = urljoin(str(response.request.url), location)
                validate_url(target, resolve=False)
                origin = (urlsplit(target).scheme, urlsplit(target).netloc.lower())
                if method not in ("GET", "HEAD"):
                    raise SecurityError("Redirects for authenticated POST requests are not allowed")
                if original_origin[0] == "https" and origin[0] != "https":
                    raise SecurityError("HTTPS downgrade redirects are not allowed")
                if origin != original_origin:
                    headers = dict(kwargs.get("headers") or {})
                    kwargs["headers"] = {k: v for k, v in headers.items()
                                         if k.lower() not in ("authorization", "x-api-key", "cookie", "proxy-authorization")}
                if on_redirect is not None:
                    on_redirect(response, target)
                kwargs.pop("params", None)
                url = target
                continue
            try:
                declared = int(response.headers.get("content-length", "0"))
            except ValueError:
                declared = 0
            if declared > max_bytes:
                raise SecurityError("Response exceeds the allowed size")
            chunks, total = [], 0
            for chunk in response.iter_bytes():
                total += len(chunk)
                if total > max_bytes:
                    raise SecurityError("Response exceeds the allowed size")
                chunks.append(chunk)
            return httpx.Response(response.status_code, headers=response.headers,
                                  content=b"".join(chunks), request=response.request,
                                  extensions=response.extensions)
    raise SecurityError("Too many redirects")


_SECRET_NAME = re.compile(r"^(?:api[_-]?key|authorization|password|secret|token|access_token)$", re.I)


def redact(value: Any, secrets: tuple[str, ...] | list[str] = ()) -> Any:
    """Sanitize reports without exposing raw exception messages or credentials."""
    if isinstance(value, dict):
        return {str(k): "[redacted]" if _SECRET_NAME.match(str(k)) else redact(v, secrets)
                for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v, secrets) for v in value]
    if isinstance(value, str):
        result = value
        for secret in sorted((str(s) for s in secrets if s), key=len, reverse=True):
            result = result.replace(secret, "[redacted]")
        result = re.sub(r"(?i)Bearer\s+[^\s\"']+", "Bearer [redacted]", result)
        return result
    return value
