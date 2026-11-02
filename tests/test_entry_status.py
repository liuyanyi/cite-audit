"""Result categories must agree across detail, filters and whole-task statistics."""
import json

import pytest
from fastapi.testclient import TestClient

from cite_audit.api import create_app
from cite_audit.storage import dump


@pytest.mark.parametrize('call_status,error_code,identity', [
    ('partial', 'coverage_unavailable', 'not_found'),
    ('failed', None, 'source_failed'),
    ('rate_limited', None, 'source_failed'),
    ('budget_exhausted', None, 'source_failed'),
    ('no_result', None, 'not_found'),
])
async def test_finished_assessment_is_completed_without_hiding_source_limits(
    tmp_path, call_status, error_code, identity,
):
    app = create_app(tmp_path, start_worker=False)
    service = app.state.service

    class Registry:
        async def lookup(self, fields, mode, settings, *, audit, cancelled):
            await audit({'provider': 'fixture', 'status': call_status, 'error_code': error_code})
            return []

    service.registry = Registry()
    tid = service.create_task(b'@misc{x,title={Unknown reference}}', 'status.bib', '', 'demo')['id']
    await service.run_task(tid)
    with TestClient(app) as client:
        task = client.get(f'/api/tasks/{tid}').json()
        assert task['execution_status'] == 'completed' and task['finalized']
        assert task['summary']['completed'] == 1
        assert task['summary']['unprocessed'] == 0
        entries = client.get(f'/api/tasks/{tid}/entries').json()['items']
        assert entries[0]['execution_status'] == 'completed'
        report = client.get(f'/api/tasks/{tid}/report').json()
        assert report['task']['execution_status'] == 'completed'
        assert report['entries'][0]['identity_state'] == identity
        calls = client.get('/api/calls', params={'task_id': tid}).json()['items']
        assert calls[0]['status'] == call_status
        assert calls[0]['error_code'] == error_code


def test_classifications_and_counts_cover_failures_ambiguity_unsupported_and_pending(tmp_path):
    app = create_app(tmp_path, start_worker=False)
    service = app.state.service
    cases = [
        ('confirmed', 'completed', 'confirmed', 'current_version_confirmed', 'version_confirmed'),
        ('conflict', 'completed', 'confirmed', 'current_version_confirmed', 'version_confirmed'),
        ('missing', 'completed', 'not_found', 'unknown', 'not_found'),
        ('source_failed', 'partial', 'source_failed', 'unknown', 'not_found'),
        ('ambiguous', 'completed', 'ambiguous', 'candidate', 'unverifiable'),
        ('insufficient', 'completed', 'insufficient', 'unknown', 'unverifiable'),
        ('patent', 'completed', 'insufficient', 'unknown', 'unverifiable'),
        ('coverage', 'completed', 'insufficient', 'unknown', 'unverifiable'),
        ('parse_failed', 'failed', 'not_found', 'unknown', 'unverifiable'),
        ('cancelled', 'cancelled', 'not_found', 'unknown', 'unverifiable'),
        ('running', 'running', 'insufficient', 'unknown', None),
        ('queued', 'queued', 'insufficient', 'unknown', None),
    ]
    raw = '\n'.join(f'@{"patent" if key == "patent" else "misc"}{{{key},title={{Synthetic {key}}}}}'
                    for key, *_ in cases).encode()
    task = service.create_task(raw, 'classification.bib', '', 'demo', confirm_budget=True)
    entries = service.entries(task['id'])
    with service.store.connect() as db:
        for entry, (key, status, identity, version, _) in zip(entries, cases, strict=True):
            result = {'identity_state': identity, 'version_status': version, 'has_conflict': key == 'conflict'}
            if key == 'coverage':
                result['calls'] = [{'provider': 'coverage', 'error_code': 'coverage_unavailable'}]
            db.execute('UPDATE entries SET status=?,result=? WHERE id=?', (status, dump(result), entry['id']))
    with TestClient(app) as client:
        path = f"/api/tasks/{task['id']}/entries"
        items = client.get(path).json()['items']
        assert [item['classification'] for item in items] == [case[-1] for case in cases]
        for item in items:
            detail = client.get(f"{path}/{item['id']}").json()
            assert detail['classification'] == item['classification']
            if item['classification'] is None:
                assert detail['evidence_revision']['merge_revision'] is None
        summary = client.get(f"/api/tasks/{task['id']}").json()['summary']
        assert summary['total'] == 12
        assert summary['completed'] == 8
        assert summary['unprocessed'] == 4
        assert summary['version_confirmed'] == 2
        assert summary['not_found'] == 2
        assert summary['unverifiable'] == 6
        assert summary['conflicts'] == 1
        for category, keys in {
            'version_confirmed': ['confirmed', 'conflict'],
            'not_found': ['missing', 'source_failed'],
            'unverifiable': ['ambiguous', 'insufficient', 'patent', 'coverage', 'parse_failed', 'cancelled'],
        }.items():
            found = []
            for page in range(1, 5):
                result = client.get(path, params={'classification': category, 'page': page, 'page_size': 2}).json()
                assert result['total'] == len(keys)
                found.extend(item['citation_key'] for item in result['items'])
            assert found == keys
        conflicts = client.get(path, params={'classification': 'version_confirmed', 'has_conflict': True}).json()
        assert [item['citation_key'] for item in conflicts['items']] == ['conflict']
        assert client.get(path, params={'classification': 'invalid'}).status_code == 400


async def test_classifying_existing_results_does_not_rewrite_frozen_reports(tmp_path):
    app = create_app(tmp_path, start_worker=False)
    service = app.state.service
    task = service.create_task(b'@misc{missing,title={Synthetic unknown}}', 'history.bib', '', 'demo')
    await service.run_task(task['id'])
    report = service.freeze(task['id'])
    # Model a report frozen before result categories existed.
    for key in ('not_found', 'version_confirmed', 'unverifiable', 'unprocessed'):
        report['task']['summary'].pop(key)
    for entry in report['entries']:
        entry.pop('classification')
    original = dump(report)
    service.store.execute('UPDATE tasks SET report=? WHERE id=?', (original, task['id']))
    with TestClient(app) as client:
        assert client.get(f"/api/tasks/{task['id']}").json()['summary']['not_found'] == 1
        assert client.get(f"/api/tasks/{task['id']}/entries", params={'classification': 'not_found'}).json()['total'] == 1
        assert client.get(f"/api/tasks/{task['id']}/report").json() == json.loads(original)
    assert service.raw_task(task['id'])['report'] == original
