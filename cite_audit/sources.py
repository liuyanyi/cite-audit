"""Bounded bibliographic retrieval, not a bibliographic identity oracle.

Official interfaces checked 2026-10-30:
https://www.crossref.org/documentation/retrieve-metadata/rest-api/
https://info.arxiv.org/help/api/user-manual.html
https://help.openalex.org/api/authentication/
https://api.semanticscholar.org/api-docs/graph
https://aclanthology.org/info/development/
https://proceedings.mlr.press/faq

ACL/PMLR are static publication sites, not general search APIs. Discovery uses
bounded enabled indexes, followed by an official record fetch; PMLR also supports
a single volume's table of contents. No corpus download or paid API is performed.
Aggregators are candidates, never independent votes or proof of version identity.
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import http.client
import ipaddress
import json
import math
import re
import socket
import ssl
import time
import xml.etree.ElementTree as ET
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from html.parser import HTMLParser
from typing import Any, ClassVar
from urllib.parse import quote, unquote, urlencode, urljoin, urlsplit

PROVIDERS = ("crossref", "arxiv", "openalex", "semantic_scholar", "acl", "pmlr")
ALLOWED_HOSTS = frozenset({
    "api.crossref.org", "export.arxiv.org", "arxiv.org", "api.openalex.org",
    "api.semanticscholar.org", "aclanthology.org", "proceedings.mlr.press",
})
MIN_INTERVAL = {"crossref": 0.25, "arxiv": 3.0, "openalex": 0.2,
                "semantic_scholar": 1.0, "acl": 0.5, "pmlr": 0.5}
MAX_BYTES = 2_000_000
MAX_CACHE_BYTES = 16_000_000
DOI_RE = re.compile(r"10\.\d{4,9}/[^\s<>\"{}]+", re.IGNORECASE)
ARXIV_RE = re.compile(r"(?:\d{4}\.\d{4,5}|[a-z][a-z.\-]+/\d{7})(?:v\d+)?", re.IGNORECASE)
ACL_RE = re.compile(r"(?:[A-Z]\d{2}-\d{4}|\d{4}\.[a-z][a-z0-9_\-]*-\d+\.\d+)", re.IGNORECASE)
PMLR_RE = re.compile(r"v\d{1,4}/[a-zA-Z0-9_\-]+")
Audit = Callable[[dict[str, Any]], Awaitable[None]]
Cancelled = Callable[[], bool]


def now() -> str:
    return datetime.now(UTC).isoformat()


def clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def normalize_doi(value: str) -> str:
    value = unquote(clean(value)).removeprefix("doi:")
    match = DOI_RE.search(value)
    return match.group(0).rstrip(".,;").lower() if match else ""


def normalize_arxiv(value: str) -> str:
    value = clean(value).removeprefix("arXiv:")
    value = re.sub(r"^https?://(?:export\.)?arxiv\.org/(?:abs|pdf)/", "", value)
    value = value.removesuffix(".pdf")
    return value if ARXIV_RE.fullmatch(value) else ""


def _acl_id(value: str) -> str:
    value = clean(value)
    if value.lower().startswith("10.18653/v1/"):
        value = value.split("/", 2)[-1]
    elif value.startswith(("https://", "http://")):
        part = urlsplit(value)
        if part.hostname != "aclanthology.org":
            return ""
        value = part.path.strip("/")
    value = re.sub(r"\.(?:bib|pdf)$", "", value)
    if not ACL_RE.fullmatch(value):
        return ""
    return value.upper() if re.fullmatch(r"[a-zA-Z]\d{2}-\d{4}", value) else value.lower()


def _pmlr_id(value: str) -> str:
    value = clean(value)
    if value.startswith(("https://", "http://")):
        part = urlsplit(value)
        if part.hostname != "proceedings.mlr.press":
            return ""
        value = part.path.strip("/")
    value = value.removesuffix(".html")
    if value.endswith(".pdf"):
        value = "/".join(value.split("/")[:2])
    return value if PMLR_RE.fullmatch(value) else ""


def _num(settings: dict, key: str, default: float, low: float, high: float) -> float:
    try:
        number = float(settings.get(key, default))
        return min(high, max(low, number)) if math.isfinite(number) else default
    except (ValueError, TypeError):
        return default


class SourceError(Exception):
    """Safe error text; never contains arbitrary response bodies or credentials."""


class UnsafeURL(SourceError):
    pass


class BudgetExceeded(SourceError):
    pass


class LookupCancelled(SourceError):
    pass


@dataclass(frozen=True)
class Response:
    status: int
    headers: dict[str, str]
    body: bytes
    url: str
    fetched_at: str = ""


async def resolve_public(host: str) -> list[str]:
    results = await asyncio.get_running_loop().getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    return list(dict.fromkeys(row[4][0] for row in results))


def validate_url(url: str) -> str:
    try:
        part = urlsplit(url)
        host = part.hostname or ""
        if (part.scheme != "https" or host not in ALLOWED_HOSTS or part.port not in (None, 443)
                or part.username or part.password or part.fragment or len(url) > 6000
                or any(ord(c) < 32 for c in url)):
            raise UnsafeURL("Only allowlisted public HTTPS bibliographic endpoints are allowed")
        return host
    except ValueError as exc:
        raise UnsafeURL("Malformed bibliographic endpoint") from exc


def validate_addresses(addresses: list[str]) -> None:
    if not addresses:
        raise UnsafeURL("DNS returned no addresses")
    for address in addresses:
        try:
            ip = ipaddress.ip_address(address)
        except ValueError as exc:
            raise UnsafeURL("DNS returned an invalid address") from exc
        if not ip.is_global or ip.is_multicast or ip.is_unspecified or ip.is_reserved:
            raise UnsafeURL("DNS returned a non-public address")


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """Connect to an already-validated IP but verify TLS for the original hostname.

    This avoids the check/second-DNS-lookup rebinding gap. http.client does not
    consult proxy environment variables, netrc, ambient API keys or cookies.
    """
    def __init__(self, host: str, address: str, timeout: float):
        super().__init__(host, port=443, timeout=timeout, context=ssl.create_default_context())
        self.address = address

    def connect(self) -> None:
        sock = socket.create_connection((self.address, 443), self.timeout)
        try:
            self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
        except BaseException:
            sock.close()
            raise


def _blocking_get(url: str, addresses: list[str], timeout: float, max_bytes: int,
                  headers: dict[str, str] | None = None) -> Response:
    part = urlsplit(url)
    deadline = time.monotonic() + timeout
    connection = _PinnedHTTPSConnection(part.hostname or "", addresses[0], timeout)
    try:
        path = part.path or "/"
        if part.query:
            path += "?" + part.query
        connection.request("GET", path, headers={
            "User-Agent": "CiteAuditRemake/0.1 (bibliographic metadata; keyless)",
            "Accept": "application/json, application/atom+xml, text/html, text/plain",
            "Accept-Encoding": "identity", "Connection": "close", **(headers or {}),
        })
        response = connection.getresponse()
        headers = {k.lower(): v for k, v in response.getheaders()}
        if headers.get("content-encoding", "identity").lower() not in ("", "identity"):
            raise SourceError("Unsupported compressed response")
        if int(headers.get("content-length", "0")) > max_bytes:
            raise SourceError("Response exceeds byte limit")
        chunks, size = [], 0
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Response deadline exceeded")
            if connection.sock is not None:
                connection.sock.settimeout(remaining)
            chunk = response.read1(min(65536, max_bytes + 1 - size))
            if not chunk:
                break
            size += len(chunk)
            if size > max_bytes:
                raise SourceError("Response exceeds byte limit")
            chunks.append(chunk)
        return Response(response.status, headers, b"".join(chunks), url, now())
    finally:
        connection.close()


async def public_transport(url: str, addresses: list[str], timeout: float, max_bytes: int,
                           *, headers: dict[str, str] | None = None) -> Response:
    return await asyncio.wait_for(
        asyncio.to_thread(_blocking_get, url, addresses, timeout, max_bytes, headers), timeout=timeout
    )


async def _pause(seconds: float, cancelled: Cancelled) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if cancelled():
            raise LookupCancelled("Lookup cancelled")
        await asyncio.sleep(min(0.1, end - time.monotonic()))


async def _interruptible(awaitable: Awaitable, cancelled: Cancelled, timeout: float) -> Any:
    task = asyncio.ensure_future(awaitable)
    end = time.monotonic() + timeout
    try:
        while not task.done():
            if cancelled():
                raise LookupCancelled("Lookup cancelled")
            remaining = end - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Request deadline exceeded")
            await asyncio.wait({task}, timeout=min(0.1, remaining))
        return task.result()
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


class _Session:
    def __init__(self, registry: SourceRegistry, settings: dict, audit: Audit, cancelled: Cancelled):
        self.registry, self.settings, self.audit, self.cancelled = registry, settings, audit, cancelled
        self.remaining = int(_num(settings, "max_source_requests", 18, 0, 40))
        self.limit = int(_num(settings, "source_max_results", 3, 1, 5))
        self.timeout = _num(settings, "request_timeout_seconds", 12, 1, 30)
        self.retries = int(_num(settings, "retries", 1, 0, 2))
        self.partial: list[dict] = []
        self.responses: list[Response] = []

    async def event(self, provider: str, operation: str, status: str, **extra: Any) -> None:
        event = {"provider": provider, "operation": operation, "status": status,
                 "duration_ms": 0, "request_summary": "", "error": None,
                 "cache_hit": False, "cost": None, **extra}
        await self.audit(event)

    async def get(self, provider: str, operation: str, url: str) -> Response:
        # Adapter version and full query semantics are included in cache identity.
        credential = self.settings.get("credentials", {}).get(provider, "") if provider in {"openalex", "semantic_scholar"} else ""
        # A changed identity must not reuse another account's cached outcome.
        scope = hashlib.sha256(credential.encode()).hexdigest() if credential else "anonymous"
        key = ("sources-v1", provider, operation, url, scope)
        cached = self.registry._cache.get(key)
        if cached and cached[0] > time.monotonic():
            self.registry._cache.move_to_end(key)
            if self.cancelled():
                raise LookupCancelled("Lookup cancelled")
            await self.event(provider, operation, "success" if cached[1].status != 404 else "empty",
                             cache_hit=True, request_summary=url, fetched_at=cached[1].fetched_at)
            self.responses.append(cached[1])
            return cached[1]
        current = url
        for redirect in range(3):
            for attempt in range(self.retries + 1):
                started = time.monotonic()
                if self.cancelled():
                    raise LookupCancelled("Lookup cancelled")
                if self.remaining <= 0:
                    raise BudgetExceeded("Source request budget exhausted")
                host = validate_url(current)
                async with self.registry._locks.setdefault(provider, asyncio.Lock()):
                    delay = self.registry._next_request.get(provider, 0) - time.monotonic()
                    await _pause(max(0, delay), self.cancelled)
                    self.remaining -= 1
                    try:
                        addresses = await _interruptible(self.registry._resolver(host), self.cancelled,
                                                         self.timeout)
                        validate_addresses(addresses)
                        headers = {}
                        # Credentials are scoped to the original provider, never redirect targets.
                        if credential and host == "api.openalex.org" and provider == "openalex":
                            headers["Authorization"] = f"Bearer {credential}"
                        if credential and host == "api.semanticscholar.org" and provider == "semantic_scholar":
                            headers["x-api-key"] = credential
                        request = self.registry._transport(current, addresses, self.timeout, MAX_BYTES,
                                                           **({"headers": headers} if headers else {}))
                        response = await _interruptible(
                            request,
                            self.cancelled, self.timeout,
                        )
                        if len(response.body) > MAX_BYTES:
                            raise SourceError("Response exceeds byte limit")
                    except (OSError, TimeoutError, SourceError) as exc:
                        name = str(exc) if isinstance(exc, SourceError) else type(exc).__name__
                        await self.event(provider, operation, "cancelled" if isinstance(exc, LookupCancelled)
                                         else "error", request_summary=current, error=name,
                                         duration_ms=round((time.monotonic() - started) * 1000), attempt=attempt)
                        if isinstance(exc, (UnsafeURL, LookupCancelled)):
                            raise
                        if attempt == self.retries:
                            raise SourceError(name) from exc
                        await _pause(min(2 ** attempt, 4), self.cancelled)
                        continue
                    finally:
                        self.registry._next_request[provider] = time.monotonic() + MIN_INTERVAL[provider]
                status = ("success" if 200 <= response.status < 300 else
                          "empty" if response.status == 404 else
                          "redirect" if 300 <= response.status < 400 else "error")
                await self.event(provider, operation, status, request_summary=current,
                                 duration_ms=round((time.monotonic() - started) * 1000), attempt=attempt,
                                 http_status=response.status,
                                 error=None if status != "error" else f"HTTP {response.status}",
                                 fetched_at=response.fetched_at)
                if response.status in (429, 500, 502, 503, 504) and attempt < self.retries:
                    try:
                        retry_after = float(response.headers.get("retry-after", 1))
                    except ValueError:
                        retry_after = 1
                    await _pause(max(1, min(retry_after, 5)), self.cancelled)
                    continue
                break
            if 300 <= response.status < 400:
                if redirect == 2:
                    raise SourceError("Redirect limit exceeded")
                target = urljoin(current, response.headers.get("location", ""))
                # Revalidate host and DNS on every hop, including otherwise-trusted hosts.
                validate_url(target)
                current = target
                continue
            if response.status == 404 or 200 <= response.status < 300:
                ttl = _num(self.settings, "source_cache_ttl_seconds", 3600, 0, 86400)
                if response.status == 404:
                    ttl = min(ttl, 60)
                if ttl:
                    self.registry._cache[key] = (time.monotonic() + ttl, response)
                    self.registry._trim_cache()
                self.responses.append(response)
                return response
            if response.status in (401, 403):
                raise SourceError(f"HTTP {response.status}: " + (
                    "provider rejected configured credentials" if credential else
                    "keyless access unavailable; no credentials sent"))
            raise SourceError(f"HTTP {response.status}")
        raise SourceError("Redirect limit exceeded")

    async def json(self, provider: str, operation: str, url: str) -> tuple[Any, Response]:
        response = await self.get(provider, operation, url)
        if response.status == 404:
            return None, response
        try:
            return json.loads(response.body), response
        except (ValueError, UnicodeError) as exc:
            self.registry._invalidate_response(response)
            raise SourceError("Invalid JSON response") from exc


def _snapshot(response: Response, parsed: Any = None) -> dict:
    return {"adapter": "sources-v1", "url": response.url, "http_status": response.status,
            "fetched_at": response.fetched_at, "sha256": hashlib.sha256(response.body).hexdigest(),
            "content_type": response.headers.get("content-type", ""),
            "response": parsed if parsed is not None else response.body.decode("utf-8", errors="replace"),
            "response_body": response.body.decode("utf-8", errors="replace")}


def _record(provider: str, record_id: str, url: str, source_type: str, fields: dict,
            identifiers: dict, raw: dict, *, relations: list | None = None) -> dict:
    identifiers = {k: clean(v) for k, v in identifiers.items() if v}
    candidate = source_type == "aggregator"
    return {"provider": provider, "record_id": record_id, "source_url": url,
            "source_type": source_type, "fetched_at": raw.get("fetched_at") or now(),
            "status": "candidate" if candidate else "verified",
            "fields": {k: clean(v) for k, v in fields.items() if v is not None and clean(v)},
            "identifiers": identifiers, "raw_payload": raw,
            "identity_basis": ("candidate" if candidate else "arxiv" if "arxiv" in identifiers else
                               "doi" if "doi" in identifiers else "official_id"),
            "relations": relations or []}


def _year(value: Any) -> str:
    if isinstance(value, dict):
        parts = value.get("date-parts", [])
        return str(parts[0][0]) if parts and parts[0] else ""
    match = re.match(r"(\d{4})", str(value or ""))
    return match.group(1) if match else ""


def _first(value: Any) -> str:
    return clean(value[0]) if isinstance(value, list) and value else clean(value) if isinstance(value, str) else ""


def _crossref(item: dict, response: Response) -> dict | None:
    doi = normalize_doi(item.get("DOI", ""))
    if not doi or not _first(item.get("title")):
        return None  # Collections and malformed records are not paper evidence.
    kind = item.get("type", "")
    if kind not in {"journal-article", "proceedings-article", "posted-content", "book-chapter"}:
        return None
    authors = " and ".join(clean(f"{a.get('family', '')}, {a.get('given', '')}").strip(", ")
                           if a.get("family") else clean(a.get("name", ""))
                           for a in item.get("author", []) if isinstance(a, dict))
    fields = {"title": _first(item.get("title")), "author": authors, "doi": doi,
              "year": _year(item.get("published-print") or item.get("published") or item.get("issued")),
              "first_online_year": _year(item.get("published-online")),
              "pages": item.get("page"), "volume": item.get("volume"), "number": item.get("issue"),
              "publisher": item.get("publisher"), "url": item.get("URL"),
              "entrytype": {"proceedings-article": "inproceedings", "book-chapter": "incollection",
                            "posted-content": "misc"}.get(kind, "article")}
    fields["booktitle" if kind in {"proceedings-article", "book-chapter"} else "journal"] = _first(
        item.get("container-title"))
    if kind == "posted-content":
        fields["publication_type"] = "preprint"
        fields.pop("journal", None)
    relations = []
    for relation, entries in item.get("relation", {}).items():
        # Crossref's directed relation vocabulary, not citations or similarity.
        direction = {"is-preprint-of": "published_as", "has-preprint": "preprint_of"}.get(relation)
        if direction:
            for entry in entries if isinstance(entries, list) else []:
                target = normalize_doi(entry.get("id", "")) if isinstance(entry, dict) else ""
                if target:
                    relations.append({"relation": direction, "target_identifiers": {"doi": target},
                                      "evidence_url": f"https://api.crossref.org/works/{quote(doi, safe='/')}"})
    return _record("crossref", doi, f"https://doi.org/{doi}", "registry", fields, {"doi": doi},
                   _snapshot(response, item), relations=relations)


class _HTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, list[str]] = {}
        self.links: list[tuple[str, str]] = []
        self.blocks: list[str] = []
        self._link: list[str] | None = None
        self._block: list[str] | None = None
        self._block_depth = 0

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "meta":
            name = (values.get("name") or values.get("property") or "").lower()
            if name and values.get("content"):
                self.meta.setdefault(name, []).append(values["content"])
        if tag == "a" and values.get("href"):
            self._link = [values["href"], ""]
        if tag in ("pre", "code"):
            if self._block is None:
                self._block = []
            self._block_depth += 1

    def handle_data(self, data):
        if self._link is not None:
            self._link[1] += data
        if self._block is not None:
            self._block.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self._link is not None:
            self.links.append((self._link[0], clean(self._link[1])))
            self._link = None
        if tag in ("pre", "code") and self._block is not None:
            self._block_depth -= 1
            if self._block_depth <= 0:
                self.blocks.append("".join(self._block))
                self._block, self._block_depth = None, 0


def parse_official_bib(text: str) -> dict[str, str]:
    """Parse one bounded official citation; no expansion, includes, commands or evaluation.

    Deliberately smaller than the user's Bib parser: macro concatenation and
    unknown macros are omitted rather than interpreted as verified metadata.
    """
    start = re.search(r"@([A-Za-z]+)\s*\{[^,{}]*,", text)
    if not start or len(text) > MAX_BYTES:
        return {}
    fields = {"entrytype": start.group(1).lower()}
    index = start.end()
    while index < len(text):
        match = re.match(r"\s*,?\s*([A-Za-z][A-Za-z0-9_\-]*)\s*=\s*", text[index:])
        if not match:
            break
        key = match.group(1).lower()
        index += match.end()
        if index >= len(text):
            break
        opener = text[index]
        if opener in '{"':
            index += 1
            begin, depth, escaped = index, 1 if opener == "{" else 0, False
            while index < len(text):
                char = text[index]
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif opener == "{" and char == "{":
                    depth += 1
                elif opener == "{" and char == "}":
                    depth -= 1
                    if depth == 0:
                        break
                elif opener == '"' and char == '"' and depth == 0:
                    break
                elif opener == '"' and char == '{':
                    depth += 1
                elif opener == '"' and char == '}':
                    depth = max(0, depth - 1)
                index += 1
            if index == len(text):
                return {}  # An incomplete record is not usable evidence.
            value = text[begin:index]
            index += 1
        else:
            match = re.match(r"[^,}\s]+", text[index:])
            if not match:
                break
            value = match.group()
            index += match.end()
            if not value.isdigit() and value.lower() not in {
                "jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"
            }:
                value = ""
        if text[index:].lstrip().startswith("#"):
            return {}  # Never silently accept only part of an expression.
        if key in {"title", "author", "editor", "year", "month", "pages", "volume", "number",
                   "booktitle", "journal", "publisher", "address", "series", "doi", "url", "eprint",
                   "archiveprefix"} and value:
            fields[key] = clean(value)
    return fields if fields.get("title") and fields.get("author") else {}


class SourceRegistry:
    sources = PROVIDERS
    capabilities: ClassVar[dict] = {
        "crossref": {"lookup": True, "search": True, "sync_index": False},
        "arxiv": {"lookup": True, "search": True, "sync_index": False},
        "openalex": {"lookup": True, "search": True, "sync_index": False,
                     "limitation": "Bounded first-page candidates; optional API key; may pool publication versions"},
        "semantic_scholar": {"lookup": True, "search": True, "sync_index": False,
                             "limitation": "Optional API key or shared keyless rate limit; may pool publication versions"},
        "acl": {"lookup": True, "search": True, "sync_index": False,
                "limitation": "Discovery via enabled indexes, then official BibTeX; not exhaustive"},
        "pmlr": {"lookup": True, "search": True, "sync_index": False,
                 "limitation": "Enabled indexes or one hinted volume, then official page; not exhaustive"},
    }

    def __init__(self, *, transport: Callable[..., Awaitable[Response]] | None = None,
                 resolver: Callable[[str], Awaitable[list[str]]] | None = None):
        self._transport = transport or public_transport
        self._resolver = resolver or resolve_public
        self._cache: OrderedDict[tuple, tuple[float, Response]] = OrderedDict()
        self._locks: dict[str, asyncio.Lock] = {}
        self._next_request: dict[str, float] = {}

    def clear_cache(self) -> int:
        count = len(self._cache)
        self._cache.clear()
        return count

    def _trim_cache(self) -> None:
        while len(self._cache) > 128 or sum(len(item[1].body) for item in self._cache.values()) > MAX_CACHE_BYTES:
            self._cache.popitem(last=False)

    def _invalidate_response(self, response: Response) -> None:
        for key, value in list(self._cache.items()):
            if value[1] is response:
                del self._cache[key]

    async def lookup(self, fields: dict[str, str], mode: str, settings: dict, *, audit: Audit,
                     cancelled: Cancelled) -> list[dict]:
        if mode not in {"online", "mock"}:
            raise ValueError("mode must be online or mock")
        session = _Session(self, settings, audit, cancelled)
        if mode == "mock":
            return await self._mock(fields, session)
        enabled = settings.get("source_enabled", {})
        enabled_list = settings.get("enabled_sources")
        active = {provider for provider in PROVIDERS if enabled.get(provider, True)
                  and (enabled_list is None or provider in enabled_list)}
        records: list[dict] = []
        # Requests contain only bounded identifier/title/first-author query hints.
        hints = {k.lower(): clean(v)[:1500] for k, v in fields.items() if isinstance(v, str)}
        for provider in PROVIDERS:
            if provider not in active:
                continue
            if cancelled():
                await session.event(provider, "lookup", "cancelled", error="Lookup cancelled")
                break
            session.partial, session.responses = [], []
            try:
                found = await getattr(self, f"_{provider}")(hints, records, session)
                records.extend(found)
                if not found:
                    for key, (expiry, response) in list(self._cache.items()):
                        if response in session.responses:
                            self._cache[key] = (min(expiry, time.monotonic() + 60), response)
                await session.event(provider, "result", "success" if found else "empty", record_count=len(found))
            except BudgetExceeded as exc:
                records.extend(session.partial)
                await session.event(provider, "lookup", "budget_exceeded", error=str(exc))
            except LookupCancelled:
                records.extend(session.partial)
                await session.event(provider, "lookup", "cancelled", error="Lookup cancelled")
                break
            except (SourceError, ValueError, TypeError, KeyError, AttributeError, IndexError, UnicodeError, ET.ParseError) as exc:
                records.extend(session.partial)
                for response in session.responses:
                    self._invalidate_response(response)
                # A failed provider cannot discard the records obtained elsewhere.
                message = str(exc) if isinstance(exc, SourceError) else f"Invalid provider record: {type(exc).__name__}"
                await session.event(provider, "lookup", "error", error=message)
        return list({(r["provider"], r["record_id"]): r for r in records}.values())

    async def _crossref(self, fields: dict, records: list, session: _Session) -> list:
        results = session.partial
        doi = normalize_doi(fields.get("doi", ""))
        # A wrong input DOI must not prevent title discovery of the intended work.
        urls = []
        if doi:
            urls.append(("lookup", f"https://api.crossref.org/works/{quote(doi, safe='/')}"))
        if fields.get("title"):
            params = {"query.title": fields["title"][:500], "rows": session.limit}
            if fields.get("author"):
                params["query.author"] = fields["author"].split(" and ")[0][:150]
            urls.append(("search", "https://api.crossref.org/works?" + urlencode(params)))
        for operation, url in urls:
            data, response = await session.json("crossref", operation, url)
            if data is None:
                continue
            message = data.get("message")
            if not isinstance(message, dict):
                raise SourceError("Crossref response missing message object")
            items = message.get("items", []) if operation == "search" else [message]
            for item in items[:session.limit]:
                if isinstance(item, dict) and (record := _crossref(item, response)):
                    results.append(record)
        return results

    async def _arxiv(self, fields: dict, records: list, session: _Session) -> list:
        identifier = normalize_arxiv(fields.get("eprint", "") or fields.get("arxiv", ""))
        if not identifier:
            identifier = normalize_arxiv(fields.get("url", ""))
        params: dict[str, Any] = {"max_results": session.limit}
        if identifier:
            params["id_list"] = identifier
        elif fields.get("title"):
            title = re.sub(r'["{}\\]', " ", fields["title"][:400])
            params["search_query"] = f'ti:"{title}"'
            if fields.get("author"):
                author = fields["author"].split(" and ")[0].split(",")[0].split()[-1]
                params["search_query"] += f' AND au:"{re.sub(r"[^\w-]", "", author)}"'
        else:
            return []
        response = await session.get("arxiv", "lookup" if identifier else "search",
                                     "https://export.arxiv.org/api/query?" + urlencode(params))
        if response.status == 404:
            return []
        # Never resolve XML entities or DTDs supplied by remote data.
        if b"<!DOCTYPE" in response.body.upper() or b"<!ENTITY" in response.body.upper():
            raise SourceError("XML declarations are unsupported")
        root = ET.fromstring(response.body)
        ns = {"a": "http://www.w3.org/2005/Atom", "x": "http://arxiv.org/schemas/atom"}
        if root.tag != "{http://www.w3.org/2005/Atom}feed":
            raise SourceError("arXiv response is not an Atom feed")
        found = session.partial
        for entry in root.findall("a:entry", ns)[:session.limit]:
            identifier = normalize_arxiv(entry.findtext("a:id", "", ns))
            if not identifier:
                if "errors" in entry.findtext("a:id", "", ns):
                    raise SourceError("arXiv returned an API error entry")
                continue
            url = f"https://arxiv.org/abs/{identifier}"
            publication_doi = normalize_doi(entry.findtext("x:doi", "", ns))
            relations = ([{"relation": "published_as", "target_identifiers": {"doi": publication_doi},
                           "evidence_url": url}] if publication_doi else [])
            record_fields = {"title": entry.findtext("a:title", "", ns), "year": _year(
                entry.findtext("a:published", "", ns)), "author": " and ".join(
                a.findtext("a:name", "", ns) for a in entry.findall("a:author", ns)),
                "eprint": identifier, "archiveprefix": "arXiv", "url": url, "entrytype": "article"}
            found.append(_record("arxiv", identifier, url, "official", record_fields, {"arxiv": identifier},
                                 _snapshot(response), relations=relations))
        return found

    async def _openalex(self, fields: dict, records: list, session: _Session) -> list:
        doi = normalize_doi(fields.get("doi", ""))
        params = {"per_page": session.limit}
        urls = []
        if doi:
            urls.append(("lookup", f"https://api.openalex.org/works/https://doi.org/{quote(doi, safe='/')}"))
        if fields.get("title"):
            params["search"] = fields["title"][:500]
            urls.append(("search", "https://api.openalex.org/works?" + urlencode(params)))
        results = session.partial
        for operation, url in urls:
            data, response = await session.json("openalex", operation, url)
            if data is None:
                continue
            items = data.get("results", []) if operation == "search" else [data]
            for item in items[:session.limit]:
                if not isinstance(item, dict) or not item.get("id") or not item.get("title"):
                    continue
                ids = {"doi": normalize_doi(item.get("doi", ""))}
                # Work-level fields can pool versions; none are promoted as official facts.
                record_fields = {"title": item.get("title"), "year": item.get("publication_year"),
                                 "author": " and ".join(a.get("author", {}).get("display_name", "")
                                                        for a in item.get("authorships", []))}
                raw = _snapshot(response, item)
                raw["upstream_urls"] = [loc.get("landing_page_url") for loc in item.get("locations", [])
                                         if isinstance(loc, dict) and loc.get("landing_page_url")]
                results.append(_record("openalex", str(item["id"]), str(item["id"]), "aggregator",
                                       record_fields, ids, raw))
        return results

    async def _semantic_scholar(self, fields: dict, records: list, session: _Session) -> list:
        doi = normalize_doi(fields.get("doi", ""))
        arxiv = normalize_arxiv(fields.get("eprint", "") or fields.get("arxiv", ""))
        selection = "paperId,title,authors,year,venue,externalIds,url,openAccessPdf,publicationVenue"
        urls = []
        identifier = "DOI:" + doi if doi else "ARXIV:" + arxiv if arxiv else ""
        if identifier:
            urls.append(("lookup", "https://api.semanticscholar.org/graph/v1/paper/" +
                         quote(identifier, safe="") + "?" + urlencode({"fields": selection})))
        if fields.get("title"):
            urls.append(("search", "https://api.semanticscholar.org/graph/v1/paper/search?" + urlencode({
                "query": fields["title"][:500], "limit": session.limit, "fields": selection})))
        results = session.partial
        for operation, url in urls:
            data, response = await session.json("semantic_scholar", operation, url)
            if data is None:
                continue
            items = data.get("data", []) if operation == "search" else [data]
            for item in items[:session.limit]:
                if not isinstance(item, dict) or not item.get("paperId") or not item.get("title"):
                    continue
                external = item.get("externalIds") or {}
                ids = {"doi": normalize_doi(external.get("DOI", "")),
                       "arxiv": normalize_arxiv(external.get("ArXiv", "")),
                       "acl": _acl_id(external.get("ACL", ""))}
                raw = _snapshot(response, item)
                raw["upstream_urls"] = [v for v in [(item.get("openAccessPdf") or {}).get("url")] if v]
                # No relation is inferred just because a third-party Work has two IDs.
                results.append(_record("semantic_scholar", str(item["paperId"]), item.get("url") or
                                       "https://www.semanticscholar.org/paper/" + str(item["paperId"]),
                                       "aggregator", {"title": item.get("title"), "year": item.get("year"),
                                       "author": " and ".join(a.get("name", "") for a in item.get("authors", []))},
                                       ids, raw))
        return results

    def _official_hints(self, provider: str, fields: dict, records: list) -> list[str]:
        extract = _acl_id if provider == "acl" else _pmlr_id
        values = [fields.get(provider, ""), fields.get("url", ""), fields.get("doi", "")]
        for record in records:
            values += [record["identifiers"].get(provider, ""), record["identifiers"].get("doi", ""),
                       record["fields"].get("url", "")]
            values += record["raw_payload"].get("upstream_urls", [])
            values += [r.get("target_identifiers", {}).get("doi", "") for r in record.get("relations", [])]
        return list(dict.fromkeys(identifier for v in values if (identifier := extract(v))))

    async def _acl(self, fields: dict, records: list, session: _Session) -> list:
        ids = self._official_hints("acl", fields, records)[:session.limit]
        if not ids:
            await session.event("acl", "discovery", "unsupported", error=(
                "No official record located through enabled indexes; no general ACL search endpoint used"))
        results = session.partial
        for identifier in ids:
            url = f"https://aclanthology.org/{identifier}/"
            response = await session.get("acl", "fetch_publication", f"https://aclanthology.org/{identifier}.bib")
            if response.status == 404:
                continue
            bib = parse_official_bib(response.body.decode("utf-8"))
            if not bib:
                self._invalidate_response(response)
                raise SourceError("Official ACL response has no complete paper BibTeX")
            # The retrieved record must corroborate its requested official identity.
            returned = _acl_id(bib.get("url", "")) or _acl_id(bib.get("doi", ""))
            if returned != identifier:
                raise SourceError("ACL response identity does not match requested record")
            results.append(_record("acl", identifier, url, "official", bib,
                                   {"acl": identifier, "doi": normalize_doi(bib.get("doi", ""))},
                                   _snapshot(response)))
        return results

    async def _pmlr(self, fields: dict, records: list, session: _Session) -> list:
        ids = self._official_hints("pmlr", fields, records)
        volume = fields.get("volume", "")
        if not ids and fields.get("title") and re.fullmatch(r"\d{1,4}", volume):
            response = await session.get("pmlr", "search_volume", f"https://proceedings.mlr.press/v{volume}/")
            if response.status != 404:
                page = _HTML()
                page.feed(response.body.decode("utf-8"))
                title_words = set(re.findall(r"\w+", fields["title"].lower()))
                scored = []
                for href, label in page.links:
                    identifier = _pmlr_id(urljoin(response.url, href))
                    words = set(re.findall(r"\w+", label.lower()))
                    score = len(title_words & words) / max(1, len(title_words | words))
                    if identifier and score >= 0.5:
                        scored.append((score, identifier))
                ids.extend(identifier for _, identifier in sorted(scored, reverse=True)[:session.limit])
        if not ids:
            await session.event("pmlr", "discovery", "unsupported", error=(
                "No official record located through enabled indexes; volume-scoped discovery needs a volume hint"))
        results = session.partial
        for identifier in list(dict.fromkeys(ids))[:session.limit]:
            url = f"https://proceedings.mlr.press/{identifier}.html"
            response = await session.get("pmlr", "fetch_publication", url)
            if response.status == 404:
                continue
            page = _HTML()
            page.feed(response.body.decode("utf-8"))
            bib = next((parsed for block in page.blocks if (parsed := parse_official_bib(block))), {})
            if not bib:
                self._invalidate_response(response)
                raise SourceError("Official PMLR response has no complete paper BibTeX")
            if _pmlr_id(bib.get("url", "")) != identifier:
                raise SourceError("PMLR response identity does not match requested record")
            results.append(_record("pmlr", identifier, url, "official", bib,
                                   {"pmlr": identifier, "doi": normalize_doi(bib.get("doi", ""))},
                                   _snapshot(response)))
        return results

    async def _mock(self, fields: dict, session: _Session) -> list:
        if session.cancelled():
            await session.event("mock", "fixture", "cancelled", error="Lookup cancelled")
            return []
        title = re.sub(r"[^a-z0-9]", "", fields.get("title", "").lower())
        doi = normalize_doi(fields.get("doi", ""))
        arxiv = normalize_arxiv(fields.get("eprint", "") or fields.get("arxiv", ""))
        known = (doi == "10.18653/v1/n19-1423" or arxiv.split("v")[0] == "1810.04805"
                 or title == re.sub(r"[^a-z0-9]", "", BERT_TITLE.lower())
                 or _acl_id(fields.get("url", "")) == "N19-1423")
        result = copy.deepcopy(MOCK_BERT_RECORDS) if known else []
        await session.event("mock", "fixture", "mock" if result else "empty", record_count=len(result),
                            request_summary="Fixed isolated BERT fixtures; unknown inputs are not synthesized")
        return result


BERT_TITLE = "BERT: Pre-training of Deep Bidirectional Transformers for Language Understanding"
BERT_AUTHORS = "Devlin, Jacob and Chang, Ming-Wei and Lee, Kenton and Toutanova, Kristina"
BERT_FORMAL = {"entrytype": "inproceedings", "title": BERT_TITLE, "author": BERT_AUTHORS,
               "year": "2019", "booktitle": "Proceedings of the 2019 Conference of the North American Chapter "
               "of the Association for Computational Linguistics: Human Language Technologies, "
               "Volume 1 (Long and Short Papers)", "pages": "4171--4186",
               "doi": "10.18653/v1/n19-1423", "publisher": "Association for Computational Linguistics",
               "url": "https://aclanthology.org/N19-1423/"}
MOCK_BERT_RECORDS = [
    _record("mock:arxiv", "1810.04805", "https://arxiv.org/abs/1810.04805", "mock",
            {"entrytype": "article", "title": BERT_TITLE, "author": BERT_AUTHORS, "year": "2018",
             "eprint": "1810.04805", "archiveprefix": "arXiv", "url": "https://arxiv.org/abs/1810.04805"},
            {"arxiv": "1810.04805"}, {"fixture": "bert-preprint", "fetched_at": "2026-10-30T00:00:00+00:00"},
            relations=[{"relation": "published_as", "target_identifiers": {"doi": "10.18653/v1/n19-1423",
                        "acl": "N19-1423"}, "evidence_url": "mock://bert-version-relation"}]),
    _record("mock:acl", "N19-1423", "https://aclanthology.org/N19-1423/", "mock", BERT_FORMAL,
            {"acl": "N19-1423", "doi": "10.18653/v1/n19-1423"},
            {"fixture": "bert-formal", "fetched_at": "2026-10-30T00:00:00+00:00"}),
    _record("mock:crossref", "10.18653/v1/n19-1423", "https://doi.org/10.18653/v1/N19-1423", "mock",
            {k: v for k, v in BERT_FORMAL.items() if k != "pages"}, {"doi": "10.18653/v1/n19-1423"},
            {"fixture": "bert-formal-complement", "fetched_at": "2026-10-30T00:00:00+00:00"}),
]
for _fixture in MOCK_BERT_RECORDS:
    _fixture["status"] = "mock"
    _fixture["raw_payload"]["fields"] = copy.deepcopy(_fixture["fields"])
    _fixture["raw_payload"]["notice"] = "Offline fixed fixture, not live verification"
