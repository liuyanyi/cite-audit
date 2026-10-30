"""Small, stateless MMSP adapter. No model response is bibliographic evidence.

MMSP 0.5.6 owns protocol conversion. We own transport policy, retries and cost
audit. Its public constructor has no transport/retry knobs, so the one pinned
compatibility boundary in ``_make_client`` initializes its three state fields
with a transport-controlled vendor SDK. No global SDK monkeypatching.
"""

from __future__ import annotations

import asyncio
import copy
import ipaddress
import socket
import time
import uuid
from collections.abc import Callable
from contextlib import aclosing
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from importlib.metadata import version
from typing import Any
from urllib.parse import urlsplit

import httpx2 as httpx

PROTOCOLS = {"openai-chat", "openai-responses", "ant-messages"}
DEFAULT_URLS = {
    "openai-chat": "https://api.openai.com/v1",
    "openai-responses": "https://api.openai.com/v1",
    "ant-messages": "https://api.anthropic.com",
}
BUCKETS = ("prompt_tokens", "cached_tokens", "response_tokens", "thoughts_tokens")
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_TEXT_CHARS = 65536


class ModelPolicyError(ValueError):
    """A safe, stable policy code; never contains a key or provider response."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _endpoint(config: dict) -> str:
    return str(config.get("base_url") or DEFAULT_URLS.get(config.get("protocol"), "")).rstrip("/")


def validate_model_url(url: str) -> tuple[str, int]:
    """Only public HTTPS is accepted, never local gateways or URL credentials."""
    try:
        parsed = urlsplit(url)
        port = parsed.port or 443
        if (
            parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment or port != 443 or "\\" in url
        ):
            raise ModelPolicyError("unsafe_endpoint")
        host = parsed.hostname.encode("idna").decode("ascii")
        if host.lower() in {"localhost", "localhost.localdomain"} or host.endswith(".localhost"):
            raise ModelPolicyError("unsafe_endpoint")
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if address and not _is_public(address):
            raise ModelPolicyError("unsafe_endpoint")
        return host, port
    except (UnicodeError, ValueError) as exc:
        if isinstance(exc, ModelPolicyError):
            raise
        raise ModelPolicyError("unsafe_endpoint") from None


def _is_public(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return address.is_global and not (address.is_multicast or address.is_reserved or address.is_unspecified)


class _BoundedStream(httpx.AsyncByteStream):
    def __init__(self, stream: httpx.AsyncByteStream, limit: int):
        self.stream, self.limit = stream, limit

    async def __aiter__(self):
        size = 0
        async for chunk in self.stream:
            size += len(chunk)
            if size > self.limit:
                raise ModelPolicyError("response_too_large")
            yield chunk

    async def aclose(self):
        await self.stream.aclose()


class PublicModelTransport(httpx.AsyncBaseTransport):
    """Resolve once, reject nonpublic answers, pin connection IP, keep TLS SNI.

    No ambient proxy, redirects, credentials in URLs, compressed streams or
    unbounded responses. Every request gets a fresh checked DNS resolution.
    """

    def __init__(self):
        self._transport = httpx.AsyncHTTPTransport(retries=0, trust_env=False)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        host, port = validate_model_url(str(request.url))
        answers = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
        addresses = list(dict.fromkeys(item[4][0] for item in answers))
        if not addresses or any(not _is_public(ipaddress.ip_address(ip)) for ip in addresses):
            raise ModelPolicyError("unsafe_dns_address")
        headers = request.headers.copy()
        headers["host"] = host
        headers["accept-encoding"] = "identity"
        pinned = httpx.Request(
            request.method, request.url.copy_with(host=addresses[0]), headers=headers,
            stream=request.stream, extensions={**request.extensions, "sni_hostname": host},
        )
        response = await self._transport.handle_async_request(pinned)
        if 300 <= response.status_code < 400:
            await response.aclose()
            raise ModelPolicyError("redirect_refused")
        if response.headers.get("content-encoding", "identity").lower() != "identity":
            await response.aclose()
            raise ModelPolicyError("compressed_response_refused")
        length = response.headers.get("content-length")
        if length and length.isdigit() and int(length) > MAX_RESPONSE_BYTES:
            await response.aclose()
            raise ModelPolicyError("response_too_large")
        response.stream = _BoundedStream(response.stream, MAX_RESPONSE_BYTES)
        return response

    async def aclose(self):
        await self._transport.aclose()


def model_catalog(currency: str = "USD") -> list[dict]:
    """Read the installed SDK's static registry; never call a provider."""
    if currency not in {"USD", "CNY"}:
        raise ValueError("currency must be USD or CNY")
    from mmsp import list_supported_models

    entries = []
    for source in list_supported_models(currency=currency):
        client = source["client"]
        protocol = {"openai-official": "openai-responses", "anthropic-official": "ant-messages"}.get(
            client, client
        )
        if protocol not in PROTOCOLS:
            continue
        entries.append({**copy.deepcopy(source), "protocol": protocol, "library": "mmsp"})
    return entries


def price_snapshot(config: dict) -> dict | None:
    """Match exact model AND endpoint; a gateway never inherits vendor prices."""
    endpoint = _endpoint(config)
    currency = config.get("currency", "USD")
    matching = [
        item for item in model_catalog(currency)
        if item["model"] == config.get("model") and item["base_url"].rstrip("/") == endpoint
    ]
    matching = [item for item in matching if item.get("pricing")]
    if not matching:
        return None
    # Some endpoints offer several protocols at one price. Only use that fact
    # when the registry agrees, not by guessing from the model name.
    if any(item["pricing"] != matching[0]["pricing"] for item in matching[1:]):
        return None
    return {
        "source": "mmsp_registry", "library_version": version("mmsp"), "captured_at": _now(),
        "model": config.get("model"), "base_url": endpoint, "protocol": config.get("protocol"),
        "currency": matching[0]["pricing"]["currency"], "rates_per_million": matching[0]["pricing"],
        "basis": "static_list_price_estimate", "source_url": "https://github.com/Prism-Shadow/model-message-stream-protocol",
    }


def _unknown_cost(price: dict | None, reason: str) -> dict:
    return {
        "amount": None, "amount_decimal": None, "currency": (price or {}).get("currency"),
        "known": False, "price_snapshot": price, "reason": reason,
    }


def calculate_cost(usage: dict | None, price: dict | None) -> dict:
    """Price MMSP's disjoint buckets exactly with Decimal; do not subtract twice.

    When reasoning is unreported, MMSP response_tokens contains total output;
    that total remains priceable if both output buckets have the same rate.
    Missing cache decomposition is only priceable if cached/input rates match.
    Unknown rates or usage never become zero.
    """
    if not usage or not price:
        return _unknown_cost(price, "usage_unavailable" if not usage else "price_unavailable")
    rates = price.get("rates_per_million", {})
    total = Decimal(0)
    for bucket in BUCKETS:
        count = usage.get(bucket)
        rate = rates.get(bucket)
        if count is None:
            paired = {"cached_tokens": "prompt_tokens", "thoughts_tokens": "response_tokens"}.get(bucket)
            if paired and usage.get(paired) is not None and rate is not None and rate == rates.get(paired):
                continue
            return _unknown_cost(price, f"{bucket}_unavailable")
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            return _unknown_cost(price, "invalid_usage")
        if count == 0:
            continue
        if rate is None:
            return _unknown_cost(price, f"{bucket}_price_unavailable")
        try:
            decimal_rate = Decimal(str(rate))
            if not decimal_rate.is_finite() or decimal_rate < 0:
                return _unknown_cost(price, "invalid_price")
            total += Decimal(count) * decimal_rate / Decimal(1_000_000)
        except (InvalidOperation, ValueError):
            return _unknown_cost(price, "invalid_price")
    return {
        "amount": float(total), "amount_decimal": format(total, "f"), "currency": price["currency"],
        "known": True, "price_snapshot": price, "reason": "static_list_price_estimate",
    }


def _public_usage(raw: dict | None) -> dict | None:
    if raw is None:
        return None
    result = {bucket: raw.get(bucket) for bucket in BUCKETS}
    result.update({
        "input_tokens": raw.get("prompt_tokens"), "output_tokens": raw.get("response_tokens"),
        "cached_input_tokens": raw.get("cached_tokens"), "reasoning_tokens": raw.get("thoughts_tokens"),
        "bucket_semantics": "input_excludes_reported_cache; output_excludes_reported_reasoning",
    })
    return result


async def _make_client(config: dict, transport: httpx.AsyncBaseTransport | None):
    from mmsp.ant_messages import AntMessagesClient
    from mmsp.ant_messages.client import _AsyncAnthropic
    from mmsp.openai_chat import OpenaiChatClient
    from mmsp.openai_responses import OpenaiResponsesClient
    from openai import AsyncOpenAI

    if version("mmsp") != "0.5.6":
        raise ModelPolicyError("unsupported_mmsp_sdk_layout")
    http = httpx.AsyncClient(
        transport=transport if transport is not None else PublicModelTransport(),
        timeout=config["timeout_seconds"], follow_redirects=False, trust_env=False,
        limits=httpx.Limits(max_connections=1, max_keepalive_connections=0),
        headers={"accept-encoding": "identity"},
    )
    base = {"openai-chat": OpenaiChatClient, "openai-responses": OpenaiResponsesClient,
            "ant-messages": AntMessagesClient}[config["protocol"]]
    # MMSP 0.5.6's constructors cannot accept a transport. Initialize exactly its
    # three state fields, retaining all SDK conversion/streaming implementation.
    # This avoids constructing an ambient-proxy client even momentarily.
    class SafeClient(base):
        def __init__(self):
            self._model = config["model"]
            self._history = []
            sdk = _AsyncAnthropic if config["protocol"] == "ant-messages" else AsyncOpenAI
            self._client = sdk(api_key=config["api_key"], base_url=_endpoint(config),
                               max_retries=0, timeout=config["timeout_seconds"], http_client=http)
    try:
        return SafeClient(), http
    except Exception:
        await http.aclose()
        raise


def _error(exc: Exception) -> tuple[str, str, bool]:
    """No raw exception text: provider errors may echo keys or prompts."""
    cause: BaseException | None = exc
    while cause is not None:
        if isinstance(cause, ModelPolicyError):
            return str(cause), "Model request blocked by transport policy.", False
        cause = cause.__cause__
    code = getattr(exc, "status_code", None) or getattr(exc, "status", None)
    if code == 429:
        return "rate_limited", "Model provider rate limit reached.", True
    if code in {401, 403}:
        return "authentication_failed", "Model provider rejected credentials or access.", False
    if isinstance(code, int):
        return "provider_error", f"Model provider returned HTTP {code}.", code >= 500 or code in {408, 409}
    if isinstance(exc, (TimeoutError, httpx.TimeoutException)) or "Timeout" in type(exc).__name__:
        return "timeout", "Model request timed out.", True
    if isinstance(exc, (httpx.TransportError, OSError)) or type(exc).__name__ in {
        "APIConnectionError", "IncompleteStreamError", "EmptyResponseError",
    }:
        return "incomplete_response", "Model request did not finish successfully.", True
    return "model_error", "Model request failed; configuration or protocol may be unsupported.", False


def _validated_config(config: dict) -> dict:
    value = dict(config)
    if value.get("protocol") not in PROTOCOLS:
        raise ModelPolicyError("unsupported_protocol")
    if not isinstance(value.get("model"), str) or not value["model"].strip() or len(value["model"]) > 200:
        raise ModelPolicyError("model_required")
    if not isinstance(value.get("api_key"), str) or not value["api_key"].strip():
        raise ModelPolicyError("api_key_required")
    validate_model_url(_endpoint(value))
    for key, default, low, high in [
        ("timeout_seconds", 30, 1, 120), ("max_retries", 2, 0, 3),
        ("max_output_tokens", 1024, 1, 8192),
    ]:
        number = value.get(key, default)
        if isinstance(number, bool) or not isinstance(number, (float, int)) or not low <= number <= high:
            raise ModelPolicyError("invalid_model_limits")
        if key != "timeout_seconds" and int(number) != number:
            raise ModelPolicyError("invalid_model_limits")
        value[key] = number if key == "timeout_seconds" else int(number)
    return value


async def call_model(
    config: dict,
    prompt: str,
    *,
    system_prompt: str = "",
    on_call: Callable[[dict], None] | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    """One text-only model operation, audited application attempts, no tools.

    ``transport`` exists for offline protocol tests; application callers must
    leave it unset. Prompt/response contents and secrets never enter call logs.
    Failed partial output is not returned as usable model advice.
    """
    calls: list[dict] = []
    empty = {"text": "", "usage": None, "cost": _unknown_cost(None, "not_called"), "calls": calls}
    if not config.get("enabled", False):
        return {**empty, "status": "failed", "error_code": "model_disabled", "message": "Model is disabled."}
    try:
        config = _validated_config(config)
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) + len(system_prompt) > MAX_TEXT_CHARS:
            raise ModelPolicyError("invalid_prompt_size")
        price = price_snapshot(config)
    except (ValueError, TypeError) as exc:
        code = str(exc) if isinstance(exc, ModelPolicyError) else "invalid_model_configuration"
        return {**empty, "status": "failed", "error_code": code, "message": "Model configuration is invalid."}

    for attempt in range(1, config["max_retries"] + 2):
        started, started_at = time.monotonic(), _now()
        usage, complete, partial = None, False, False
        texts: list[str] = []
        http = None
        result: dict = {}
        retry = False
        try:
            async with asyncio.timeout(config["timeout_seconds"]):
                client, http = await _make_client(config, transport)
                generation = {"max_tokens": config["max_output_tokens"]}
                if system_prompt:
                    generation["system_prompt"] = system_prompt
                messages = [{"role": "user", "content_items": [{"type": "text.done", "text": prompt}]}]
                async with aclosing(client.streaming_response(messages=messages, config=generation)) as stream:
                    async for event in stream:
                        for item in event.get("content_items", []):
                            if item.get("type") == "text.delta":
                                partial = True
                            if item.get("type") == "text.done":
                                texts.append(item.get("text", ""))
                                if sum(map(len, texts)) > MAX_TEXT_CHARS:
                                    raise ModelPolicyError("response_too_large")
                        if event.get("event_type") == "stop":
                            usage = event.get("usage_metadata")
                            complete = event.get("finish_reason") == "stop"
                if not complete or not texts:
                    raise ModelPolicyError("incomplete_generation")
                result = {"status": "success", "text": "".join(texts), "error_code": None, "message": None}
        except asyncio.CancelledError:
            record = {
                "id": str(uuid.uuid4()), "provider": "mmsp", "kind": "model", "operation": "generate",
                "status": "cancelled", "started_at": started_at,
                "duration_ms": round((time.monotonic() - started) * 1000), "attempt": attempt,
                "cache_hit": False, "mode": "online", "error_code": "cancelled", "message": "Cancelled.",
                "usage": _public_usage(usage), "cost": calculate_cost(usage, price),
            }
            calls.append(record)
            if on_call:
                on_call(copy.deepcopy(record))
            raise
        except Exception as exc:  # noqa: BLE001 - SDK errors are sanitized at this external boundary.
            usage = getattr(exc, "usage_metadata", None) or usage
            code, message, retry = _error(exc)
            result = {
                "status": "partial" if partial or texts else "failed", "text": "",
                "error_code": code, "message": message,
            }
        finally:
            if http is not None:
                await http.aclose()

        cost = calculate_cost(usage, price)
        record = {
            "id": str(uuid.uuid4()), "provider": "mmsp", "kind": "model", "operation": "generate",
            "model": config["model"], "protocol": config["protocol"], "status": result["status"],
            "started_at": started_at, "duration_ms": round((time.monotonic() - started) * 1000),
            "attempt": attempt, "cache_hit": False, "mode": "online",
            "error_code": result["error_code"], "message": result["message"],
            "usage": _public_usage(usage), "cost": cost,
        }
        calls.append(record)
        if on_call:
            on_call(copy.deepcopy(record))
        if result["status"] == "success" or not retry or attempt > config["max_retries"]:
            return {**result, "usage": _public_usage(usage), "cost": cost, "calls": calls}
        await asyncio.sleep(min(0.25 * 2 ** (attempt - 1), 2))
    raise AssertionError("unreachable")
