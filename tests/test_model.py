"""Actual MMSP SDK conversion against offline HTTP responses, never a paid API."""
import asyncio
import json

import httpx2 as httpx
import pytest

from cite_audit import model


def config(protocol="openai-chat", **extra):
    return {"enabled": True, "protocol": protocol, "model": "offline-test", "api_key": "test-only-secret",
            "base_url": "https://provider.example/v1", "max_retries": 0, "timeout_seconds": 5,
            "max_output_tokens": 50, **extra}


def sse(items):
    return "".join("data: " + (x if isinstance(x, str) else json.dumps(x)) + "\n\n" for x in items)


def chat_response():
    common = {"id": "test", "object": "chat.completion.chunk", "created": 1, "model": "offline-test"}
    return sse([
        {**common, "choices": [{"index": 0, "delta": {"role": "assistant", "content": "OK"}, "finish_reason": None}]},
        {**common, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
        {**common, "choices": [], "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12,
         "prompt_tokens_details": {"cached_tokens": 0}, "completion_tokens_details": {"reasoning_tokens": 0}}}, "[DONE]"])


async def test_real_mmsp_chat_sdk_success_and_no_secret_audit():
    requests, records = [], []
    def handler(request):
        requests.append(request)
        return httpx.Response(200, text=chat_response(), headers={"content-type": "text/event-stream"})
    result = await model.call_model(config(), "Say OK", system_prompt="Be concise", on_call=records.append,
                                    transport=httpx.MockTransport(handler))
    assert result["status"] == "success", result
    assert result["text"] == "OK" and result["usage"]["input_tokens"] == 10
    assert len(requests) == 1 and requests[0].url.path == "/v1/chat/completions"
    payload = json.loads(requests[0].content)
    assert payload["max_completion_tokens"] == 50
    assert "test-only-secret" not in json.dumps(records)
    assert result["cost"]["known"] is False


async def test_real_mmsp_responses_sdk():
    events = [
        {"type": "response.created", "response": {"id": "r", "status": "in_progress", "output": []}},
        {"type": "response.output_item.added", "output_index": 0, "item": {
            "id": "m", "type": "message", "role": "assistant", "status": "in_progress", "content": []}},
        {"type": "response.output_text.delta", "item_id": "m", "output_index": 0, "content_index": 0, "delta": "OK"},
        {"type": "response.completed", "response": {"id": "r", "status": "completed", "output": [],
            "usage": {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12,
                      "input_tokens_details": {"cached_tokens": 0}, "output_tokens_details": {"reasoning_tokens": 0}}}},
    ]
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200, text=sse(events), headers={"content-type": "text/event-stream"})
    result = await model.call_model(config("openai-responses"), "Say OK", transport=httpx.MockTransport(handler))
    assert result["status"] == "success", result
    assert result["text"] == "OK" and result["usage"]["output_tokens"] == 2
    assert requests[0].url.path == "/v1/responses"


async def test_real_mmsp_anthropic_sdk():
    events = [
        {"type": "message_start", "message": {"id": "m", "type": "message", "role": "assistant",
            "model": "offline-test", "content": [], "stop_reason": None, "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 0, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}}},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "OK"}},
        {"type": "content_block_stop", "index": 0},
        {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None}, "usage": {"output_tokens": 2}},
        {"type": "message_stop"},
    ]
    body = "".join("event: " + e["type"] + "\ndata: " + json.dumps(e) + "\n\n" for e in events)
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})
    result = await model.call_model(config("ant-messages", base_url="https://provider.example"), "Say OK", transport=httpx.MockTransport(handler))
    assert result["status"] == "success", result
    assert result["text"] == "OK" and result["usage"]["input_tokens"] == 10
    assert requests[0].url.path == "/v1/messages"


async def test_application_retry_sdk_internal_retries_disabled(monkeypatch):
    requests = []
    async def no_sleep(*args):
        pass
    monkeypatch.setattr(model.asyncio, "sleep", no_sleep)
    def handler(request):
        requests.append(request)
        return httpx.Response(429, json={"error": {"message": "test-only-secret", "type": "rate_limit"}})
    result = await model.call_model(config(max_retries=1), "Say OK", transport=httpx.MockTransport(handler))
    assert len(requests) == 2 and len(result["calls"]) == 2
    assert "test-only-secret" not in json.dumps(result)
    assert result["error_code"] == "rate_limited"


@pytest.mark.parametrize("url", ["http://example.com", "https://localhost", "https://127.0.0.1",
    "https://10.1.1.1", "https://[::1]", "https://user:secret@example.com", "https://example.com?key=x",
    "https://example.com:444", "https://example.com/#fragment"])
def test_model_endpoint_policy(url):
    with pytest.raises(model.ModelPolicyError):
        model.validate_model_url(url)


async def test_disabled_or_invalid_never_calls_transport():
    def handler(request):
        pytest.fail("No network expected")
    for changes in [{"enabled": False}, {"api_key": ""}, {"base_url": "http://localhost"}, {"max_retries": True}]:
        result = await model.call_model(config(**changes), "OK", transport=httpx.MockTransport(handler))
        assert result["status"] == "failed" and not result["calls"]


def test_cost_disjoint_buckets_unknown_and_zero():
    price = {"currency": "USD", "rates_per_million": dict(zip(model.BUCKETS, [2, 1, 4, 4]))}
    usage = dict(zip(model.BUCKETS, [100, 20, 10, 5]))
    assert model.calculate_cost(usage, price)["amount_decimal"] == "0.00028"
    assert model.calculate_cost(None, price)["known"] is False
    assert model.calculate_cost(usage, None)["known"] is False
    assert model.calculate_cost({**usage, "cached_tokens": None}, price)["known"] is False
    assert model.calculate_cost({**usage, "thoughts_tokens": None}, price)["known"] is True
    assert model.calculate_cost({**usage, "prompt_tokens": -1}, price)["known"] is False
    assert model.calculate_cost(dict.fromkeys(model.BUCKETS, 0), price)["amount"] == 0


def test_catalog_offline_and_price_matches_endpoint():
    catalog = model.model_catalog()
    assert catalog and all(x["protocol"] in model.PROTOCOLS for x in catalog)
    item = next(x for x in catalog if x.get("pricing"))
    assert model.price_snapshot({"model": item["model"], "base_url": item["base_url"], "protocol": item["protocol"]})
    assert model.price_snapshot({"model": item["model"], "base_url": "https://gateway.example", "protocol": item["protocol"]}) is None


async def test_public_transport_rejects_private_dns_before_connect(monkeypatch):
    async def private_dns(*args, **kwargs):
        return [(2, 1, 6, "", ("127.0.0.1", 443))]
    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", private_dns)
    transport = model.PublicModelTransport()
    try:
        with pytest.raises(model.ModelPolicyError, match="unsafe_dns_address"):
            await transport.handle_async_request(httpx.Request("POST", "https://provider.example/v1"))
    finally:
        await transport.aclose()


async def test_partial_sdk_text_is_never_returned_as_success():
    # A length-limited answer is billed/audited but cannot become evidence or advice.
    response = chat_response().replace('"finish_reason": "stop"', '"finish_reason": "length"')
    result = await model.call_model(config(), "OK", transport=httpx.MockTransport(
        lambda request: httpx.Response(200, text=response, headers={"content-type": "text/event-stream"})))
    assert result["status"] == "partial" and result["text"] == ""
    assert result["calls"][0]["status"] == "partial"
