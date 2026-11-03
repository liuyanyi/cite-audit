"""Call details preserve the attempted query and its own redacted response."""
import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from cite_audit.api import create_app
from cite_audit.sources import Response
from tests.test_sources import CROSSREF, Network, lookup


async def test_call_response_cache_and_result_details():
    network = Network(lambda url: (200, {"message": {"items": [CROSSREF]}}))
    records, events, registry = await lookup(network)
    response = next(event for event in events if event["operation"] == "search")
    assert "query.title=" in response["request_summary"]
    assert response["response_summary"]["http_status"] == 200
    assert json.loads(response["response_summary"]["body"])["message"]["items"][0] == CROSSREF
    result = next(event for event in events if event["operation"] == "result")
    assert result["response_summary"]["record_count"] == len(records) == 1
    assert result["response_summary"]["records"][0]["fields"] == records[0]["fields"]
    _, cached, _ = await lookup(network, registry=registry)
    hit = next(event for event in cached if event["cache_hit"])
    assert hit["response_summary"] == response["response_summary"]
    assert hit["request_count"] == 0


@pytest.mark.parametrize("status,body", [(404, ""), (429, '{"error":"limited"}'), (200, "invalid-json")])
async def test_failed_and_empty_response_details(status, body):
    _, events, _ = await lookup(Network(lambda url: (status, body)))
    response = next(event["response_summary"] for event in events if "response_summary" in event)
    assert response["http_status"] == status
    assert response["body"] == body or json.loads(response["body"]) == json.loads(body)


def test_call_detail_api_is_redacted_immutable_and_not_in_list_payloads(tmp_path):
    app = create_app(tmp_path, start_worker=False)
    with TestClient(app) as client:
        service = app.state.service
        service.settings.update({"credentials": {"openalex": "private-value"}})
        record = service.call(None, None, {
            "provider": "crossref", "operation": "search", "status": "success", "http_status": 200,
            "request_summary": "https://api.crossref.org/works?query.title=BERT&api_key=private-value",
            "response_summary": {"body": "Bearer private-value", "nested": {"token": "other-value"}},
        }, "online")
        detail = client.get(f"/api/calls/{record['id']}")
        assert detail.status_code == 200
        assert "private-value" not in detail.text and "other-value" not in detail.text
        assert detail.json()["response_summary"]["nested"]["token"] == "[redacted]"
        assert detail.json()["http_status"] == 200
        assert "response_summary" not in client.get("/api/calls").json()["items"][0]
        assert "response_summary" not in service.calls()[0]
        service.settings.update({"credentials": {"openalex": "changed-value"}})
        assert client.get(f"/api/calls/{record['id']}").content == detail.content
        old = service.call(None, None, {"provider": "acl", "status": "success"}, "online")
        assert "response_summary" not in client.get(f"/api/calls/{old['id']}").json()
        assert client.get("/api/calls/missing").status_code == 404


async def test_response_preview_redacts_before_truncation_and_never_saves_headers():
    from cite_audit.sources import _response_summary

    secret = "private-value"
    body = ("x" * 31_995 + secret + '<script>alert(1)</script>').encode()
    summary = _response_summary(Response(503, {"authorization": secret}, body,
                                         "https://api.crossref.org/works"),
                                {"credentials": {"crossref": secret}})
    assert summary["truncated"] is True
    assert len(summary["body"]) == 32_000
    assert summary["byte_size"] == len(body)
    assert "priva" not in summary["body"]
    assert "authorization" not in summary
    summary = _response_summary(Response(200, {}, b'{"api_key":"unknown-secret"}',
                                         "https://api.crossref.org/works"), {})
    assert "unknown-secret" not in summary["body"]


def test_demo_detail_is_available_without_mutating_frozen_report(tmp_path):
    app = create_app(tmp_path, start_worker=False)
    with TestClient(app) as client:
        task = client.post("/api/tasks", files={"file": ("demo.bib", b'@article{x,eprint={1810.04805}}')},
                           data={"mode": "demo"}).json()
        asyncio.run(app.state.service.run_task(task["id"]))
        report = client.get(task["report_url"]).content
        call = client.get("/api/calls", params={"task_id": task["id"]}).json()["items"][0]
        detail = client.get(f"/api/calls/{call['id']}").json()
        assert detail["mode"] == "demo" and detail["response_summary"]["record_count"] == 3
        assert client.get(task["report_url"]).content == report
