"""Re-audits keep task identity and isolate each report's execution history."""
import asyncio
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from cite_audit.api import create_app
from cite_audit.service import AuditService


class Requests:
    def __init__(self):
        self.lookups = 0
        self.fail = True

    async def lookup(self, fields, mode, settings, *, audit, cancelled):
        self.lookups += 1
        assert settings['max_source_requests'] == 1
        await audit({'provider': 'fixture', 'status': 'failed' if self.fail else 'success', 'request_count': 1})
        return []


async def test_retry_resets_budget_and_failed_calls_but_retains_audit_and_exports(tmp_path):
    registry = Requests()
    service = AuditService(tmp_path, registry=registry)
    raw = b'% immutable\r\n@misc{example,title={Unknown reference}}\r\n'
    task = service.create_task(raw, 'original.bib', 'My task', 'demo', {'entry_request_budget': 1, 'request_budget': 1})
    tid = task['id']
    await service.run_task(tid)
    old = service.freeze(tid)
    assert old['entries'][0]['execution_status'] == 'completed'
    assert old['entries'][0]['identity_state'] == 'source_failed'
    assert old['entries'][0]['calls'][0]['status'] == 'failed'
    preview, _ = service.export_preview(tid, [])
    artifact = service.create_export(tid, [], True, preview['output_sha256'])
    export_row = service.store.one('SELECT * FROM exports WHERE id=?', (artifact['id'],))
    registry.fail = False
    retried = service.retry(tid)
    assert retried['id'] == tid and retried['name'] == 'My task'
    assert retried['created_at'] == task['created_at']
    assert retried['summary']['confirmed'] == 0
    with pytest.raises(ValueError, match='completion'):
        service.export_preview(tid, [])
    await service.run_task(tid)
    assert registry.lookups == 2
    latest = service.freeze(tid)
    assert latest['entries'][0]['execution_status'] == 'completed'
    assert [call['status'] for call in latest['entries'][0]['calls']] == ['success']
    assert [call['report_revision'] for call in service.calls(tid)] == [1, 2]
    assert service.historical_report(tid, 1) == old
    assert service.store.one('SELECT * FROM exports WHERE id=?', (artifact['id'],)) == export_row
    assert service.original(tid) == raw
    with pytest.raises(sqlite3.IntegrityError, match='immutable'):
        service.store.execute('UPDATE task_reports SET report=? WHERE task_id=?', ('{}', tid))


async def test_retry_is_atomic_and_scoped_to_task_and_entry(tmp_path):
    service = AuditService(tmp_path)
    first = service.create_task(b'@misc{one,title={One}}', 'one.bib', '', 'demo')
    second = service.create_task(b'@misc{two,title={Two}}', 'two.bib', '', 'demo')
    await service.run_task(first['id'])
    foreign = service.entries(second['id'])[0]['id']
    with pytest.raises(KeyError):
        service.retry_entry(first['id'], foreign)
    assert service.report_revision(first['id']) == 1
    def retry():
        try:
            return service.retry(first['id'])['id']
        except ValueError:
            return 'already running'
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: retry(), range(2)))
    assert sorted(results) == sorted([first['id'], 'already running'])
    assert len(service.store.all('SELECT * FROM task_reports')) == 1
    assert service.task_page()['total'] == 2


async def test_cancelled_old_request_cannot_write_into_retry(tmp_path):
    entered, release = asyncio.Event(), asyncio.Event()
    class Delayed:
        async def lookup(self, fields, mode, settings, *, audit, cancelled):
            entered.set()
            await release.wait()
            assert cancelled()
            assert await audit({'provider': 'late', 'status': 'failed', 'request_count': 1}) is None
            return []
    service = AuditService(tmp_path, registry=Delayed())
    tid = service.create_task(b'@misc{one,title={One}}', 'one.bib', '', 'demo')['id']
    running = asyncio.create_task(service.run_task(tid))
    await asyncio.wait_for(entered.wait(), 2)
    service.cancel(tid)
    old = service.freeze(tid)
    service.retry(tid)
    release.set()
    await running
    assert service.task(tid)['execution_status'] == 'queued'
    assert service.calls(tid) == []
    assert service.entries(tid)[0]['execution_status'] == 'queued'
    assert service.historical_report(tid, 1) == old
    reopened = AuditService(tmp_path)
    await reopened.start()
    try:
        for _ in range(100):
            if reopened.task(tid)['finalized']:
                break
            await asyncio.sleep(.01)
        assert reopened.task(tid)['finalized']
        assert reopened.task(tid)['report_revision'] == 2
        assert reopened.historical_report(tid, 1) == old
    finally:
        await reopened.stop()


def test_report_routes_and_retries_never_create_tasks(tmp_path):
    with TestClient(create_app(tmp_path, start_worker=False)) as client:
        service = client.app.state.service
        tid = service.create_task(b'@misc{x,title={Example}}', 'one.bib', '', 'demo')['id']
        assert client.post(f'/api/tasks/{tid}/retry').status_code == 400
        asyncio.run(service.run_task(tid))
        old = client.get(f'/api/tasks/{tid}/report').content
        assert client.get(f'/api/tasks/{tid}/reports/1').content == old
        assert client.post(f'/api/tasks/{tid}/retry').status_code == 200
        assert client.get('/api/tasks').json()['total'] == 1
        assert client.get(f'/api/tasks/{tid}/reports/1').content == old
        assert client.get(f'/api/tasks/{tid}/reports/2').status_code == 404
        assert client.get('/api/tasks/missing/reports').status_code == 404
        assert client.get(f'/api/tasks/{tid}/reports/0').status_code == 404
        service.cancel(tid)
        assert len(client.get(f'/api/tasks/{tid}/reports').json()['items']) == 2
