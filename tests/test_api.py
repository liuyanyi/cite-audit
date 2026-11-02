import asyncio
import json
import os
import subprocess
import sys
from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from cite_audit.api import create_app
from cite_audit.service import AuditService
from cite_audit.sources import BERT_AUTHORS, BERT_TITLE, MOCK_BERT_RECORDS, Response, SourceRegistry

RAW = ('% untouched comment\r\n@article{bert, title={' + BERT_TITLE + '}, author={' + BERT_AUTHORS + '}, year={2018}, eprint={1810.04805}, archiveprefix={arXiv}, custom={keep}}\r\n').encode()


@pytest.mark.parametrize("outcome,expected", [
    (200, "available"), (404, "no_result"), (429, "rate_limited"),
    (403, "unavailable"), ("timeout", "unavailable"), ("invalid", "unavailable"),
])
def test_pmlr_connection_uses_own_record_and_distinguishes_outcomes(tmp_path, outcome, expected):
    urls = []
    url = "https://proceedings.mlr.press/v139/radford21a.html"

    async def resolve(host):
        assert host == "proceedings.mlr.press"
        return ["93.184.216.34"]

    async def transport(request_url, addresses, timeout, max_bytes):
        urls.append(request_url)
        if outcome == "timeout":
            raise TimeoutError()
        # Synthetic response at the probe URL; never online factual evidence.
        body = ('<pre>@inproceedings{probe, title={Synthetic probe}, author={Example, Alice}, '
                'year={2021}, booktitle={Synthetic conference}, '
                f'url={{{url}}}}}</pre>').encode() if outcome == 200 else b"not a paper"
        return Response(outcome if isinstance(outcome, int) else 200, {}, body, request_url)

    registry = SourceRegistry(transport=transport, resolver=resolve)
    with TestClient(create_app(tmp_path, start_worker=False, registry=registry)) as client:
        service = client.app.state.service
        before = client.get("/api/settings").json()
        # Two clicks must each test the connection, not reuse a cached result.
        for _ in range(2):
            response = client.post("/api/settings/sources/pmlr/test")
            assert response.status_code == 200
            result = response.json()
            assert result["status"] == expected
            assert result["message"]
            assert result["records_received"] == (1 if outcome == 200 else 0)
            assert result["knowledge_written"] is False
            assert sum(call["request_count"] for call in result["calls"]) == 1
            assert not any(call["cache_hit"] for call in result["calls"])
            assert {call["provider"] for call in result["calls"]} == {"pmlr"}
        assert urls == [url, url]
        assert client.get("/api/settings").json() == before
        assert client.get("/api/calls?provider=pmlr").json()["total"] >= 4
        for table in ("articles", "versions", "sources", "snapshots", "tasks"):
            assert service.store.all(f"SELECT * FROM {table}") == []


def test_unattempted_source_test_is_inconclusive(tmp_path, monkeypatch):
    with TestClient(create_app(tmp_path, start_worker=False)) as client:
        async def unsupported(fields, mode, config, *, audit, cancelled):
            await audit({"provider": "pmlr", "operation": "discovery", "status": "unsupported"})
            await audit({"provider": "pmlr", "operation": "result", "status": "empty"})
            return []
        monkeypatch.setattr(client.app.state.service.registry, "lookup", unsupported)
        result = client.post("/api/settings/sources/pmlr/test").json()
        assert result["status"] == "inconclusive"
        assert all(call["request_count"] == 0 for call in result["calls"])


@pytest.fixture
def service(tmp_path, monkeypatch):
    # These API tests model a newly captured demo response, not the historical
    # fixture capture date. Tests of expiry override this timestamp explicitly.
    records = deepcopy(MOCK_BERT_RECORDS)
    for record in records:
        record["fetched_at"] = datetime.now(UTC).isoformat()
    monkeypatch.setattr("cite_audit.sources.MOCK_BERT_RECORDS", records)
    return AuditService(tmp_path)


async def completed(service, raw=RAW):
    task = service.create_task(raw, 'refs.bib', '', 'demo')
    await service.run_task(task['id'])
    return service.task(task['id'])


@pytest.mark.asyncio
async def test_single_entry_retry_preserves_original_scope_and_report(service, tmp_path, monkeypatch):
    raw = b'@string{heading = "Other paper"}\r\n' + RAW + b'@misc{other,title=heading}\r\n'
    task = await completed(service, raw)
    frozen = service.freeze(task['id'])
    untouched = service.entries(task['id'], True)[0]
    selected = service.entries(task['id'])[1]
    app = create_app(data_dir=tmp_path, start_worker=False)
    with TestClient(app) as client:
        response = client.post(f"/api/tasks/{task['id']}/entries/{selected['id']}/retry")
        assert response.status_code == 200, response.text
        fresh = response.json()
        assert fresh['id'] == task['id']
        assert fresh['summary']['total'] == 2
        assert fresh['summary']['queued'] == 1
        assert fresh['report_revision'] == 2 and not fresh['finalized']
        assert client.get('/api/tasks').json()['total'] == 1
        assert service.original(fresh['id']) == raw
        entry = service.entries(fresh['id'])[1]
        assert entry['id'] == selected['id']
        assert entry['citation_key'] == 'other'
        assert entry['title'] == 'Other paper'
        assert entry['ordinal'] == 1
        assert client.post(f"/api/tasks/{fresh['id']}/entries/missing/retry").status_code == 404
        assert client.post(f"/api/tasks/{fresh['id']}/entries/{entry['id']}/retry").status_code == 400
        assert client.get(f"/api/tasks/{task['id']}/reports/1").json() == frozen
        assert client.get(f"/api/tasks/{task['id']}/reports/2").status_code == 404
    def no_reuse(*args):
        raise AssertionError("A single-entry retry must fetch fresh evidence")
    monkeypatch.setattr(service, "reusable_sources", no_reuse)
    await service.run_task(fresh['id'])
    assert service.task(fresh['id'])['execution_status'] == 'completed'
    assert service.historical_report(task['id'], 1) == frozen
    assert service.entries(task['id'], True)[0] == untouched
    assert service.report_history(task['id'])['items'][0]['revision'] == 2
    second = service.freeze(task['id'])
    assert second['task']['report_revision'] == 2
    assert service.retry(fresh['id'])['id'] == task['id']
    assert service.historical_report(task['id'], 2) == second
    await service.run_task(task['id'])
    assert len(service.entries(task['id'])) == 2
    assert service.export_preview(fresh['id'], [])[1] == raw


def test_unsupported_verification_is_distinct_from_insufficient(tmp_path):
    with TestClient(create_app(data_dir=tmp_path, start_worker=False)) as client:
        service = client.app.state.service
        task = service.create_task(
            b'@patent{patent,title={Example}}\n@misc{cn,edition={CN116664598B}}\n@misc{paper,title={Unknown}}',
            'coverage.bib', '', 'demo')
        entries = service.entries(task['id'])
        assert [e['verification_supported'] for e in entries] == [False, False, True]
        path = f"/api/tasks/{task['id']}/entries"
        unsupported = client.get(path, params={'identity_state': 'unsupported'}).json()['items']
        assert [e['citation_key'] for e in unsupported] == ['patent', 'cn']
        insufficient = client.get(path, params={'identity_state': 'insufficient'}).json()['items']
        assert [e['citation_key'] for e in insufficient] == ['paper']



@pytest.mark.asyncio
@pytest.mark.parametrize("evidence_age_seconds,expect_cache_hit", [(0, True), (86401, False)])
async def test_full_flow_frozen_report_new_upload_and_export(service, monkeypatch, evidence_age_seconds,
                                                         expect_cache_hit):
    # Fixture capture dates are historical, so wall-clock passage must not decide
    # whether this test expects reusable evidence. Exercise both TTL branches.
    records = deepcopy(MOCK_BERT_RECORDS)
    captured_at = (datetime.now(UTC) - timedelta(seconds=evidence_age_seconds)).isoformat()
    for record in records:
        record["fetched_at"] = captured_at
    monkeypatch.setattr("cite_audit.sources.MOCK_BERT_RECORDS", records)
    task = await completed(service)
    assert task['execution_status'] == 'completed'
    entry = service.entries(task['id'], True)[0]
    assert entry['recommendation_status'] == 'upgrade_available'
    assert entry['conflict_count'] == 0
    assert all(c['status'] == 'success' for c in entry['calls'])
    frozen = service.freeze(task['id'])
    selection = [{'entry_id': entry['id'], 'proposal_id': next(p['id'] for p in entry['proposals'] if p['proposal_type']=='whole_version_switch')}]
    preview, new = service.export_preview(task['id'], selection)
    assert not preview['blocked']
    assert preview['changed_entries'] == 1
    assert preview['unchanged_entries'] == 0
    assert preview['changes'][0]['citation_key'] == 'bert'
    assert b'eprint' not in new and b'2019' in new and b'custom={keep}' in new
    assert new.startswith(b'% untouched comment\r\n')
    with pytest.raises(ValueError, match='confirmation'):
        service.create_export(task['id'], selection, False, preview['output_sha256'])
    with pytest.raises(ValueError, match='hash mismatch'):
        service.create_export(task['id'], selection, True, 'bad')
    exported = service.create_export(task['id'], selection, True, preview['output_sha256'])
    assert exported['output_sha256'] == preview['output_sha256']
    assert service.original(task['id']) == RAW
    second = service.create_task(RAW, 'refs.bib', '', 'demo')
    assert second['id'] != task['id'] and second['execution_status'] == 'queued'
    assert second['summary']['confirmed'] == 0
    await service.run_task(second['id'])
    assert any(c['cache_hit'] for c in service.calls(second['id'])) is expect_cache_hit
    updated = deepcopy(MOCK_BERT_RECORDS[1]); updated['raw_payload']={'revision':99}; updated['fields']['pages']='1--2'
    service.knowledge.ingest([updated], 'mock')
    assert service.freeze(task['id']) == frozen
    assert service.entries(task['id'], True)[0] == frozen['entries'][0]
    service.delete(second['id'])
    assert service.store.all('SELECT id FROM articles')
    assert service.freeze(task['id']) == frozen


async def test_wrong_cached_doi_still_performs_discovery(service):
    records = deepcopy(MOCK_BERT_RECORDS)
    for record in records:
        record['fetched_at'] = datetime.now(UTC).isoformat()
    service.knowledge.ingest(records, 'mock')
    wrong = {'doi': '10.18653/v1/n19-1423', 'title': 'A completely different publication'}
    assert service.reusable_sources(wrong, 'demo', 86400) == []


async def test_api_excluded_candidates_and_immutable_report(tmp_path):
    from fastapi.testclient import TestClient

    from cite_audit.api import create_app

    class Records:
        async def lookup(self, fields, mode, settings, *, audit, cancelled):
            await audit({'provider': 'fixture', 'status': 'success'})
            return deepcopy(MOCK_BERT_RECORDS)

    app = create_app(tmp_path, start_worker=False, registry=Records())
    service = app.state.service
    raw = b'@article{unrelated,title={Cell microscopy image segmentation},author={Liu, Yanyi},year={2025}}'
    with TestClient(app) as client:
        task = client.post('/api/tasks', files={'file': ('test.bib', raw)}, data={'mode': 'demo'}).json()
        await service.run_task(task['id'])
        row = client.get(f"/api/tasks/{task['id']}/entries").json()['items'][0]
        detail = client.get(f"/api/tasks/{task['id']}/entries/{row['id']}").json()
        assert detail['identity_state'] == 'not_found'
        assert not detail['candidates'] and detail['excluded_candidates']
        for candidate in detail['excluded_candidates']:
            assert client.get(f"/api/snapshots/{candidate['snapshot_id']}").status_code == 200
        frozen = client.get(f"/api/tasks/{task['id']}/report").json()
        assert client.get(f"/api/tasks/{task['id']}/original").content == raw
        assert client.post(f"/api/tasks/{task['id']}/export-preview", json={'selections': []}).status_code == 200
        service.knowledge.ingest(deepcopy(MOCK_BERT_RECORDS), 'mock')
        assert client.get(f"/api/tasks/{task['id']}/report").json() == frozen


@pytest.mark.asyncio
async def test_version_subset_and_foreign_selections_rejected(service):
    task = await completed(service); entry = service.entries(task['id'],True)[0]
    p = next(p for p in entry['proposals'] if p['proposal_type']=='whole_version_switch')
    with pytest.raises(ValueError,match='atomic'):
        service.export_preview(task['id'],[{'entry_id':entry['id'],'proposal_id':p['id'],'fields':['year']}])
    with pytest.raises(ValueError,match='belong'):
        service.export_preview(task['id'],[{'entry_id':'foreign','proposal_id':p['id']}])
    assert service.export_preview(task['id'],[])[1] == RAW


@pytest.mark.asyncio
async def test_invalid_bytes_retained_with_valid_entry_continuing(service):
    raw = b'@misc{bad,title={\xff}}\n' + RAW
    task = await completed(service, raw)
    assert task['summary']['total']==2
    assert task['summary']['failed']==1
    assert task['summary']['confirmed']==1
    assert service.original(task['id']) == raw
    assert service.export_preview(task['id'],[])[1] == raw
    json.dumps(service.freeze(task['id']), ensure_ascii=False).encode('utf-8')


@pytest.mark.asyncio
async def test_cancel_and_restart_preserve_finished_entries(service):
    task = service.create_task(RAW,'refs.bib','','demo')
    service.cancel(task['id'])
    await service.run_task(task['id'])
    assert service.task(task['id'])['execution_status']=='cancelled'
    assert service.calls(task['id']) == []
    fresh = service.retry(task['id'])
    service.store.execute("UPDATE tasks SET status='running' WHERE id=?", (fresh['id'],))
    service.store.execute("UPDATE entries SET status='running' WHERE task_id=?", (fresh['id'],))
    await service.start()
    for _ in range(100):
        if service.task(fresh['id'])['execution_status']=='completed':
            break
        await asyncio.sleep(.01)
    await service.stop()
    assert service.task(fresh['id'])['execution_status']=='completed'


def test_api_contract_routes_guards_and_secret_redaction(tmp_path):
    app = create_app(tmp_path, start_worker=False)
    with TestClient(app) as client:
        assert client.get('/api/health').status_code==200
        r=client.post('/api/tasks',files={'file':('refs.bib',RAW)},data={'mode':'demo'})
        assert r.status_code==201
        tid=r.json()['id']
        asyncio.run(app.state.service.run_task(tid))
        assert client.get(f'/api/tasks/{tid}/original').content == RAW
        detail=client.get(f'/api/tasks/{tid}/report').json()
        assert detail['entries'][0]['current_version']['field_selections'][0]['field']
        aid=detail['entries'][0]['article_id']
        art=client.get(f'/api/articles/{aid}').json()
        assert art['version_count']==2 and art['version_relations']
        src=detail['entries'][0]['sources'][0]
        assert client.get(src['snapshot_url']).json()['mode']=='demo'
        settings=client.get('/api/settings').json()
        assert 'api_key' not in settings['model']
        r=client.put('/api/settings',json={'model':{'api_key':'never-expose-secret'}})
        assert r.status_code==200 and 'never-expose-secret' not in r.text
        assert client.post('/api/settings/model/test',json={'confirmed':False}).status_code==400
        assert client.post('/api/tasks',headers={'Origin':'https://evil.invalid'},files={'file':('refs.bib',RAW)}).status_code==403
        assert client.get('/api/no-such-route').status_code==404
        assert client.delete('/api/query-cache').json()['knowledge_preserved']
        assert client.delete(f'/api/tasks/{tid}').json()['knowledge_preserved']
        assert client.get('/api/articles').json()['total']==1


@pytest.mark.asyncio
async def test_cancel_delete_during_request_does_not_kill_queue(tmp_path):
    started, release = asyncio.Event(), asyncio.Event()
    class SlowRegistry:
        calls = 0
        async def lookup(self, fields, mode, config, audit, cancelled):
            self.calls += 1
            if self.calls == 1:
                started.set()
                await release.wait()
            await audit({'provider':'fixture','status':'success'})
            return deepcopy(MOCK_BERT_RECORDS)
    service=AuditService(tmp_path, registry=SlowRegistry())
    first=service.create_task(RAW,'refs.bib','','demo')
    second=service.create_task(RAW,'refs.bib','','demo')
    await service.start()
    await asyncio.wait_for(started.wait(),2)
    service.cancel(first['id']); service.delete(first['id']); release.set()
    for _ in range(100):
        if service.task(second['id'])['execution_status']=='completed': break
        await asyncio.sleep(.01)
    assert not service.worker_task.done()
    assert service.task(second['id'])['execution_status']=='completed'
    await service.stop()


@pytest.mark.asyncio
async def test_same_publication_entrytype_correction_is_exportable(service):
    raw = ('@misc{k, title={' + BERT_TITLE + '}, doi={10.18653/v1/n19-1423}}').encode()
    task=await completed(service,raw)
    entry=service.entries(task['id'],True)[0]
    p=next(p for p in entry['proposals'] if p['proposal_type']=='same_version_patch')
    preview,content=service.export_preview(task['id'],[{'entry_id':entry['id'],'proposal_id':p['id'],'fields':['entrytype']}])
    assert not preview['blocked']
    assert content.startswith(b'@inproceedings{k,')
    assert preview['changed_entries']==1


@pytest.mark.asyncio
async def test_recent_complement_does_not_refresh_old_evidence(service):
    task=await completed(service)
    frozen=service.freeze(task['id'])
    source_id=service.knowledge.article(service.entries(task['id'])[0]['article_id'])['versions'][0]['sources'][0]['id']
    assert service.reusable_sources({'eprint':'1810.04805'},'demo',86400)
    expired_at = (datetime.now(UTC) - timedelta(days=2)).isoformat()
    service.store.execute('UPDATE source_checks SET checked_at=? WHERE source_id=?', (expired_at,source_id))
    assert service.knowledge.source(source_id)['stale']
    assert service.reusable_sources({'eprint':'1810.04805'},'demo',86400)==[]
    assert service.freeze(task['id'])==frozen


@pytest.mark.asyncio
async def test_duplicate_publication_warning_is_task_local(service):
    task=await completed(service,RAW + RAW.replace(b'{bert,',b'{bertAgain,'))
    entries=service.entries(task['id'],True)
    assert len(entries)==2
    assert all(any('same confirmed publication' in w for w in e['warnings']) for e in entries)
    assert service.original(task['id']).count(b'@article')==2
    assert len(service.store.all('SELECT id FROM articles'))==1


@pytest.mark.parametrize("budget,raw,expected_requests", [
    (1, b'@misc{k,doi={10.1000/example},title={Example}}', 1),
    (2, b'@misc{a,title={First}}\n@misc{b,title={Second}}', 2),
])
async def test_request_budget_uses_attempts_and_completed_assessment_retains_exhaustion(tmp_path, budget, raw, expected_requests):
    from cite_audit.policy import PROVIDERS
    from cite_audit.sources import Response, SourceRegistry

    requests = []
    async def resolver(_host):
        return ["93.184.216.34"]
    async def transport(url, _addresses, _timeout, _limit):
        requests.append(url)
        return Response(404, {}, b"", url)
    registry = SourceRegistry(transport=transport, resolver=resolver)
    service = AuditService(tmp_path, registry=registry)
    service.settings.update({"source_enabled": {p: p == "crossref" for p in PROVIDERS}, "retries": 0})
    task = service.create_task(raw, "budget.bib", "", "online", {"request_budget": budget}, confirm_budget=True)
    await service.run_task(task["id"])
    assert len(requests) == expected_requests
    calls = service.calls(task["id"])
    assert sum(c["request_count"] for c in calls) == expected_requests
    assert all(c["attempt"] >= 1 for c in calls)
    if budget == 1:
        assert service.task(task["id"])["execution_status"] == "completed"
        assert service.entries(task["id"])[0]["identity_state"] == "source_failed"
        assert any(c["status"] == "budget_exhausted" for c in calls)
    else:
        assert service.task(task["id"])["execution_status"] == "completed"
        assert not any(c["status"] == "budget_exhausted" for c in calls)


async def test_deleting_shared_upload_keeps_other_report_until_last_task_deleted(service):
    first = await completed(service)
    second = await completed(service)
    frozen = service.freeze(second["id"])
    snapshots = service.store.all("SELECT * FROM snapshots")
    service.delete(first["id"])
    assert service.original(second["id"]) == RAW
    assert service.freeze(second["id"]) == frozen
    service.delete(second["id"])
    assert service.store.all("SELECT * FROM uploads") == []
    assert service.store.all("SELECT * FROM snapshots") == snapshots
    assert service.store.all("SELECT * FROM articles")


def test_task_warnings_are_displayable_strings_and_import_does_not_create_data(tmp_path):
    service = AuditService(tmp_path / "actual")
    task = service.create_task(b'@misc{a,title={First}}\n@misc{a,title={Duplicate}}', "demo.bib", "", "demo")
    assert task["warnings"] and all(isinstance(warning, str) for warning in task["warnings"])
    assert any("Duplicate" in warning or "occurs" in warning for warning in task["warnings"])
    unexpected = tmp_path / "import-only"
    subprocess.run([sys.executable, "-B", "-c", "import cite_audit.api"],
                   env={**os.environ, "CITE_AUDIT_DATA_DIR": str(unexpected)}, check=True)
    assert not unexpected.exists()


def test_listing_entries_uses_a_bounded_number_of_reads(service, monkeypatch):
    raw = b"\n".join(f'@misc{{k{i},title={{Paper {i}}}}}'.encode() for i in range(200))
    task = service.create_task(raw, "many.bib", "", "demo", confirm_budget=True)
    reads = []
    for method in ("one", "all"):
        original = getattr(service.store, method)
        def tracked(sql, args=(), original=original):
            reads.append(sql)
            return original(sql, args)
        monkeypatch.setattr(service.store, method, tracked)
    assert len(service.entries(task["id"])) == 200
    assert len(reads) <= 3
    reads.clear()
    assert service.task(task["id"])["summary"]["total"] == 200
    assert len(reads) <= 4
