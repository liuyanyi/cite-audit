"""SSE lifecycle, commit ordering, durable recovery and bounded read views."""
import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from cite_audit.api import create_app
from cite_audit.service import AuditService

RAW = b'@misc{first,title={Unknown example},year={2026}}'


def message(value):
    lines = value.splitlines()
    return (next(line[7:] for line in lines if line.startswith('event: ')),
            json.loads(next(line[6:] for line in lines if line.startswith('data: '))))


@pytest.fixture
def service(tmp_path):
    return AuditService(tmp_path)


async def next_message(stream):
    while True:
        value = await asyncio.wait_for(anext(stream), timeout=3)
        if not value.startswith(':'):
            return message(value)


async def test_live_stream_commits_cancel_then_closes_without_changing_report(service):
    task = service.create_task(RAW, 'demo.bib', '', 'demo')
    stream = service.events.stream(task_id=task['id'])
    assert (await next_message(stream))[0] == 'sync'
    # Cancellation is handled in FastAPI's thread pool in production.
    await asyncio.to_thread(service.cancel, task['id'])
    kind, data = await next_message(stream)
    assert kind == 'change'
    assert {'task', 'entry'} <= set(data['items'][0]['changed'])
    frozen = service.freeze(task['id'])
    assert frozen['task']['finalized'] is True
    assert frozen['entries'][0]['execution_status'] == 'cancelled'
    assert (await next_message(stream))[0] == 'done'
    with pytest.raises(StopAsyncIteration):
        await anext(stream)
    assert not service.events._listeners
    # A late provider result cannot generate a call or change the frozen report.
    assert service.call(task['id'], None, {'provider': 'late', 'status': 'success'}, 'demo') is None
    assert service.freeze(task['id']) == frozen


async def test_terminal_status_waits_for_frozen_report(service):
    task = service.create_task(RAW, 'demo.bib', '', 'demo')
    stream = service.events.stream(task_id=task['id'], heartbeat=0.01)
    await next_message(stream)
    service.store.execute("UPDATE tasks SET status='failed' WHERE id=?", (task['id'],))
    assert (await next_message(stream))[0] == 'change'
    assert await anext(stream) == ': heartbeat\n\n'
    assert service.task(task['id'])['finalized'] is False
    service.freeze(task['id'])
    assert (await next_message(stream))[0] == 'change'
    assert (await next_message(stream))[0] == 'done'
    await stream.aclose()


async def test_reconnect_replays_committed_ids_after_service_restart(service, tmp_path):
    task = service.create_task(RAW, 'demo.bib', '', 'demo')
    cursor = service.events.read(None, task['id'])[0]
    await service.run_task(task['id'])
    frozen = service.freeze(task['id'])
    restarted = AuditService(tmp_path)
    stream = restarted.events.stream(cursor, task['id'])
    assert (await next_message(stream)) == ('sync', {'reset': False, 'task_ids': []})
    kind, data = await next_message(stream)
    assert kind == 'change'
    assert data['items'][0]['task_id'] == task['id']
    assert data['items'][0]['entry_ids'] == [service.entries(task['id'])[0]['id']]
    assert 'Unknown example' not in json.dumps(data)
    assert (await next_message(stream))[0] == 'done'
    await stream.aclose()
    assert restarted.freeze(task['id']) == frozen


async def test_cursor_expiry_resync_and_task_isolation(service):
    first = service.create_task(RAW, 'demo.bib', '', 'demo')
    other = service.create_task(RAW, 'other.bib', '', 'demo')
    with service.store.connect() as db:
        db.executemany("INSERT INTO task_events(task_id,kind) VALUES(?,'task')", [(other['id'],)] * 2010)
    assert service.store.one('SELECT COUNT(*) AS n FROM task_events')['n'] == 2000
    stream = service.events.stream(1, first['id'])
    assert (await next_message(stream)) == ('sync', {'reset': True, 'task_ids': [first['id']]})
    await stream.aclose()
    cursor = service.events.read(None, first['id'])[0]
    service.cancel(other['id'])
    _, reset, changes, active = service.events.read(cursor, first['id'])
    assert not reset and changes == [] and active == [first['id']]


async def test_rolled_back_write_is_not_published_and_disconnect_removes_listener(service):
    task = service.create_task(RAW, 'demo.bib', '', 'demo')
    stream = service.events.stream(task_id=task['id'])
    await next_message(stream)
    cursor = service.events.read(None, task['id'])[0]
    with pytest.raises(RuntimeError), service.store.connect() as db:
        db.execute("UPDATE entries SET status='running' WHERE task_id=?", (task['id'],))
        raise RuntimeError('rollback')
    assert service.events.read(cursor, task['id'])[2] == []
    assert len(service.events._listeners) == 1
    await stream.aclose()
    assert not service.events._listeners


async def test_deleted_task_is_not_kept_alive(service):
    task = service.create_task(RAW, 'demo.bib', '', 'demo')
    cursor = service.events.read(None, task['id'])[0]
    service.cancel(task['id'])
    service.delete(task['id'])
    stream = service.events.stream(cursor, task['id'])
    await next_message(stream)
    assert 'deleted' in (await next_message(stream))[1]['items'][0]['changed']
    assert (await next_message(stream))[0] == 'done'
    await stream.aclose()


def test_sse_http_headers_cursor_validation_and_terminal_end(tmp_path):
    app = create_app(tmp_path, start_worker=False)
    with TestClient(app) as client:
        service = app.state.service
        task = service.create_task(RAW, 'demo.bib', '', 'demo')
        cursor = service.events.read(None, task['id'])[0]
        service.cancel(task['id'])
        response = client.get('/api/events', params={'task_id': task['id'], 'after': cursor})
        assert response.headers['content-type'].startswith('text/event-stream')
        assert response.headers['cache-control'] == 'no-cache'
        assert response.headers['x-accel-buffering'] == 'no'
        assert 'event: change' in response.text and response.text.rstrip().endswith('}')
        assert 'event: done' in response.text
        # Native EventSource's header takes priority over the initial URL cursor.
        response = client.get('/api/events', params={'task_id': task['id'], 'after': cursor},
                              headers={'Last-Event-ID': str(service.events.read(None, None)[0])})
        assert 'event: change' not in response.text
        assert 'event: done' in client.get('/api/events').text
        for value in ('bad', '-1', '9223372036854775808'):
            assert client.get('/api/events', headers={'Last-Event-ID': value}).status_code == 400
        assert client.get('/api/events?task_id=missing').status_code == 404


def test_pages_filter_before_presenting_and_summary_does_not_decode_every_entry(service, monkeypatch):
    raw = b''.join(f'@misc{{key{i},title={{Paper {i:03}}},year={{2026}}}}\n'.encode() for i in range(100))
    task = service.create_task(raw, 'many.bib', 'Straße', 'demo', confirm_budget=True)
    original = service._entry
    presented = []

    def track(row, mode, detail):
        presented.append(row['id'])
        return original(row, mode, detail)

    monkeypatch.setattr(service, '_entry', track)
    result = service.entry_page(task['id'], q='Paper', identity_state='insufficient', page=2, page_size=10)
    assert result['total'] == 100 and result['items'][0]['ordinal'] == 10
    assert len(presented) == 10
    presented.clear()
    assert service.task(task['id'])['summary']['queued'] == 100
    assert presented == []
    assert service.task_page(q='STRASSE')['total'] == 1
    assert service.task_page(execution_status='completed')['active_tasks'] == 1
    assert service.call_page(task_id=task['id'])['items'] == []
