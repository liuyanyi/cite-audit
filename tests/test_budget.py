"""Request allowances use parsed inputs and real source sessions, without network access."""
import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from cite_audit.api import create_app
from cite_audit.policy import PROVIDERS
from cite_audit.service import AuditService
from cite_audit.sources import Response, SourceRegistry


@pytest.fixture(autouse=True)
def no_source_wait(monkeypatch):
    monkeypatch.setattr("cite_audit.sources.MIN_INTERVAL", dict.fromkeys(PROVIDERS, 0))


async def public_dns(_host):
    return ["93.184.216.34"]


def only_crossref(service):
    service.settings.update({"source_enabled": {p: p == "crossref" for p in PROVIDERS}, "retries": 0})


def test_creation_requires_budget_choice_without_writing_or_queueing(tmp_path):
    raw = b'@string{heading="First"}\n@comment{Ignored}\n@misc{a,title=heading}\n@misc{b,title={Second}}'
    with TestClient(create_app(tmp_path, start_worker=False)) as client:
        service = client.app.state.service
        config = {"entry_request_budget": 3, "request_budget": 5}
        data = {"mode": "demo", "config": json.dumps(config)}
        files = {"file": ("budget.bib", raw)}
        response = client.post("/api/tasks", files=files, data=data)
        assert response.status_code == 409
        assert response.json()["error"] == {
            "code": "budget_confirmation_required",
            "message": "理论请求上限超过任务总上限，请选择继续或调整预算。",
            "details": {"entry_count": 2, "entry_request_budget": 3, "request_budget": 5, "maximum_requests": 6},
        }
        assert not service.wake.is_set()
        for table in ("uploads", "tasks", "entries", "calls", "snapshots"):
            assert service.store.all(f"SELECT * FROM {table}") == []
        # Continuing authorizes the smaller total, not the theoretical maximum.
        created = client.post("/api/tasks", files=files, data={**data, "confirm_budget": "true"})
        assert created.status_code == 201
        task_id = created.json()["id"]
        saved = json.loads(service.raw_task(task_id)["config"])
        assert saved["entry_request_budget"] == 3 and saved["request_budget"] == 5
        assert service.original(task_id) == raw
        # Adjusting to exactly the maximum does not need confirmation.
        adjusted = client.post("/api/tasks", files=files, data={"config": json.dumps({**config, "request_budget": 6})})
        assert adjusted.status_code == 201
        assert adjusted.json()["id"] != task_id


@pytest.mark.parametrize("value", [0, 41, True, 1.5, "2"])
def test_invalid_entry_allowance_is_rejected_without_writes(tmp_path, value):
    with TestClient(create_app(tmp_path, start_worker=False)) as client:
        response = client.post("/api/tasks", files={"file": ("test.bib", b"@misc{x,title={X}}")},
                               data={"config": json.dumps({"entry_request_budget": value})})
        assert response.status_code == 400
        assert client.get("/api/tasks").json()["total"] == 0


def test_runtime_defaults_roundtrip_and_are_frozen_for_each_task(tmp_path):
    with TestClient(create_app(tmp_path, start_worker=False)) as client:
        settings = client.put("/api/settings", json={"runtime": {"entry_request_budget": 4, "request_budget": 80000}})
        assert settings.status_code == 200
        assert settings.json()["runtime"]["entry_request_budget"] == 4
        assert settings.json()["limits"]["runtime"]["request_budget"]["max"] >= 80000
        task = client.post("/api/tasks", files={"file": ("test.bib", b"@misc{x,title={X}}")}).json()
        service = client.app.state.service
        config = service.raw_task(task["id"])["config"]
        client.put("/api/settings", json={"runtime": {"entry_request_budget": 8, "request_budget": 100}})
        assert service.raw_task(task["id"])["config"] == config
        assert json.loads(config)["entry_request_budget"] == 4


@pytest.mark.parametrize("entry_limit,total_limit,expected", [(1, 2, [1, 1, 0]), (2, 5, [2, 2, 1]), (2, 6, [2, 2, 2])])
async def test_entries_have_independent_allowances_bounded_by_task_total(tmp_path, entry_limit, total_limit, expected):
    requests = []

    async def transport(url, *_args):
        requests.append(url)
        return Response(404, {}, b"", url)

    service = AuditService(tmp_path, registry=SourceRegistry(transport=transport, resolver=public_dns))
    only_crossref(service)
    raw = b'\n'.join(f'@misc{{k{i},title={{Synthetic paper {i}}},doi={{10.1000/example{i}}}}}'.encode() for i in range(3))
    task = service.create_task(raw, "budget.bib", "", "online",
                               {"entry_request_budget": entry_limit, "request_budget": total_limit}, confirm_budget=True)
    await service.run_task(task["id"])
    entries = service.entries(task["id"], True)
    assert [sum(c["request_count"] for c in e["calls"]) for e in entries] == expected
    assert len(requests) == sum(expected)
    assert sum(c["request_count"] for c in service.calls(task["id"])) == len(requests)
    assert all(e["execution_status"] == "completed" for e in entries)
    assert all(e["identity_state"] != "confirmed" for e in entries)


@pytest.mark.parametrize("entry_limit,expected_journals", [(1, 0), (2, 1)])
async def test_primary_lookup_and_journal_recheck_share_entry_allowance(tmp_path, entry_limit, expected_journals):
    requests = []
    record = {"DOI": "10.1000/synthetic", "type": "journal-article", "title": ["Synthetic budget paper"],
              "author": [{"family": "Example", "given": "Alice"}], "published": {"date-parts": [[2026]]},
              "ISSN": ["1546-2226"], "container-title": ["Synthetic Journal"]}

    async def transport(url, *_args):
        requests.append(url)
        message = {"title": "Synthetic Journal", "ISSN": ["1546-2226"]} if "/journals/" in url else record
        return Response(200, {}, json.dumps({"message": message}).encode(), url)

    service = AuditService(tmp_path, registry=SourceRegistry(transport=transport, resolver=public_dns))
    only_crossref(service)
    raw = (b'@article{x,title={Synthetic budget paper},author={Example, Alice},year={2026},'
           b'doi={10.1000/synthetic},journal={Different Journal}}')
    task = service.create_task(raw, "journal.bib", "", "online", {"entry_request_budget": entry_limit, "request_budget": 10})
    await service.run_task(task["id"])
    entry = service.entries(task["id"], True)[0]
    assert entry["identity_state"] == "confirmed"
    assert len(requests) == entry_limit
    assert sum("/journals/" in url for url in requests) == expected_journals
    assert len(entry.get("journal_checks", [])) == expected_journals
    assert next(f for f in entry["field_assessments"] if f["field"] == "journal")["judgment"] == "conflict"


async def test_recovery_keeps_spent_allowance_and_reaudit_resets_it(tmp_path):
    class InterruptedRequests:
        def __init__(self):
            self.allowances = []

        async def lookup(self, fields, mode, config, *, audit, cancelled):
            self.allowances.append(config["max_source_requests"])
            await audit({"provider": "fixture", "status": "failed", "request_count": 1})
            if len(self.allowances) == 1:
                raise asyncio.CancelledError()
            return []

    registry = InterruptedRequests()
    service = AuditService(tmp_path, registry=registry)
    raw = b'@misc{x,title={Synthetic interrupted reference}}'
    task = service.create_task(raw, "recovery.bib", "", "online", {"entry_request_budget": 2, "request_budget": 2})
    with pytest.raises(asyncio.CancelledError):
        await service.run_task(task["id"])
    recovered = AuditService(tmp_path, registry=registry)
    await recovered.start()
    try:
        for _ in range(100):
            if recovered.task(task["id"])["finalized"]:
                break
            await asyncio.sleep(0.01)
        assert recovered.task(task["id"])["finalized"]
    finally:
        await recovered.stop()
    assert registry.allowances == [2, 1]
    recovered = AuditService(tmp_path, registry=registry)
    old_report = recovered.freeze(task["id"])
    recovered.retry(task["id"])
    await recovered.run_task(task["id"])
    assert registry.allowances == [2, 1, 2]
    assert recovered.historical_report(task["id"], 1) == old_report
    assert recovered.original(task["id"]) == raw
