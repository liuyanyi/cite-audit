import asyncio
import json
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from cite_audit.api import create_app
from cite_audit.service import AuditService
from cite_audit.sources import BERT_AUTHORS, BERT_TITLE, MOCK_BERT_RECORDS

RAW = ('% untouched comment\r\n@article{bert, title={' + BERT_TITLE + '}, author={' + BERT_AUTHORS + '}, year={2018}, eprint={1810.04805}, archiveprefix={arXiv}, custom={keep}}\r\n').encode()


@pytest.fixture
def service(tmp_path):
    return AuditService(tmp_path)


async def completed(service, raw=RAW):
    task = service.create_task(raw, 'refs.bib', '', 'demo')
    await service.run_task(task['id'])
    return service.task(task['id'])


@pytest.mark.asyncio
async def test_full_flow_frozen_report_new_upload_and_export(service):
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
    second = service.retry(task['id'])
    assert second['id'] != task['id'] and second['execution_status'] == 'queued'
    assert second['summary']['confirmed'] == 0
    await service.run_task(second['id'])
    assert any(c['cache_hit'] for c in service.calls(second['id']))
    updated = deepcopy(MOCK_BERT_RECORDS[1]); updated['raw_payload']={'revision':99}; updated['fields']['pages']='1--2'
    service.knowledge.ingest([updated], 'mock')
    assert service.freeze(task['id']) == frozen
    assert service.entries(task['id'], True)[0] == frozen['entries'][0]
    service.delete(second['id'])
    assert service.store.all('SELECT id FROM articles')
    assert service.freeze(task['id']) == frozen


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
    service.store.execute('UPDATE source_checks SET checked_at=? WHERE source_id=?', ('2000-01-01T00:00:00+00:00',source_id))
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
