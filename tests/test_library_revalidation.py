import copy
from datetime import datetime, timedelta, timezone
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import httpx

from app.config import Config
from app.library import Library
from app.providers import ProviderService, _LAST_ATTEMPT, _NOT_BEFORE
from app.storage import Database, PersistentCache


class LibraryRevalidationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.db = Database(self.temporary.name)
        self.cache = PersistentCache(self.db)
        self.library = Library(self.db)
        self.settings = Config(Path(self.temporary.name)).get()
        self.settings['mode'] = 'live'
        self.settings['llm'].update(api_key='fake-key', model='test-model', base_url='https://api.openai.com/v1')
        _LAST_ATTEMPT.clear()
        _NOT_BEFORE.clear()
        self.clients = []

    def tearDown(self):
        for client in self.clients:
            client.close()
        self.temporary.cleanup()

    def service(self, handler):
        client = httpx.Client(transport=httpx.MockTransport(handler))
        self.clients.append(client)
        return ProviderService(self.settings, self.cache, client=client, sleep=lambda _: None)

    def request(self, service):
        return service._request('source', 'known_id', 'https://example.org/record/1')

    def expire(self):
        with self.db.connect() as conn:
            conn.execute("UPDATE query_cache SET expires_at=0 WHERE key LIKE 'request:%'")

    @staticmethod
    def record(identifier='one', **extra):
        future = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
        return {'id': identifier, 'provider': 'crossref', 'source_id': '10.5555/library',
                'version': 'published', 'method': 'api', 'fields': {'title': 'An explicit title', 'author': 'Smith, Jane', 'doi': '10.5555/library'},
                'raw': {}, 'provenance': 'crossref', 'retrieved_at': '2026-10-01T00:00:00Z',
                'expires_at': future, **extra}

    def test_stale_content_is_retained_and_conditional_304_confirms(self):
        requests = []
        def handler(request):
            requests.append(request)
            if len(requests) == 1:
                return httpx.Response(200, json={'title': 'Original'}, headers={'ETag': '"one"'})
            self.assertEqual('"one"', request.headers['if-none-match'])
            return httpx.Response(304)
        service = self.service(handler)
        original = self.request(service)
        self.expire()
        retained = self.cache.inspect(next(iter(self._keys())))
        self.assertEqual('stale', retained['state'])
        self.assertEqual(original['data'], retained['value']['data'])
        refreshed = self.request(service)
        self.assertEqual(original['data'], refreshed['data'])
        self.assertEqual(original['retrieved_at'], refreshed['retrieved_at'])
        self.assertEqual('fresh', refreshed['cache_state'])
        self.assertEqual('not_modified', service.calls[-1]['revalidation'])
        self.assertTrue(service.calls[-1]['external_call'])
        self.assertEqual(304, service.calls[-1]['http_status'])
        with self.db.connect() as conn:
            self.assertEqual(1, conn.execute('SELECT count(*) FROM source_response_snapshots').fetchone()[0])

    def _keys(self):
        with self.db.connect() as conn:
            return [row[0] for row in conn.execute('SELECT key FROM query_cache')]

    def test_same_200_without_validators_reuses_content(self):
        calls = []
        service = self.service(lambda request: (calls.append(request), httpx.Response(200, json={'title': 'Same'}))[1])
        original = self.request(service)
        self.expire()
        current = self.request(service)
        self.assertEqual('unchanged', service.calls[-1]['revalidation'])
        self.assertEqual(original['retrieved_at'], current['retrieved_at'])
        self.assertEqual(2, len(calls))

    def test_changed_response_creates_another_immutable_snapshot(self):
        responses = iter([{'title': 'Old'}, {'title': 'New'}])
        service = self.service(lambda request: httpx.Response(200, json=next(responses)))
        self.request(service)
        self.expire()
        self.assertEqual({'title': 'New'}, self.request(service)['data'])
        self.assertEqual('changed', service.calls[-1]['status'])
        with self.db.connect() as conn:
            rows = conn.execute('SELECT payload FROM source_response_snapshots').fetchall()
        self.assertEqual({'Old', 'New'}, {json.loads(row[0])['data']['title'] for row in rows})

    def test_failed_revalidation_retains_stale_without_resetting_freshness(self):
        responses = iter([httpx.Response(200, json={'title': 'History'}), *[httpx.Response(503) for _ in range(3)]])
        service = self.service(lambda request: next(responses))
        original = self.request(service)
        self.expire()
        failed = self.request(service)
        self.assertEqual('stale', failed['cache_state'])
        self.assertEqual(original['expires_at'], failed['expires_at'])
        self.assertTrue(service._partial_errors[-1]['retained_history'])
        self.assertIsNone(self.cache.get(self._keys()[0]))
        self.assertEqual(4, sum(call.get('external_call', False) for call in service.calls))

    def test_permanent_library_readthrough_and_explicit_cache_clear(self):
        requests = []
        def handler(request):
            requests.append(request)
            return httpx.Response(304) if len(requests) > 1 else httpx.Response(200, json={'title': 'Durable'}, headers={'ETag': '"v1"'})
        service = self.service(handler)
        self.request(service)
        with self.db.connect() as conn:
            conn.execute('DELETE FROM query_cache')
        self.assertEqual({'title': 'Durable'}, self.request(service)['data'])
        self.assertEqual(1, len(requests))
        self.assertTrue(service.calls[-1]['cache_hit'])
        self.cache.clear()
        self.request(service)
        self.assertEqual(2, len(requests))
        self.assertEqual('"v1"', requests[-1].headers['if-none-match'])

    def test_search_negative_and_positive_freshness_are_separate(self):
        service = self.service(lambda request: httpx.Response(200, json={'results': []} if request.url.path.endswith('/search') else {'title': 'Known'}))
        positive = service._request('source', 'known', 'https://example.org/id/1')
        negative = service._request('source', 'publication_search', 'https://example.org/search', kind='search')
        self.assertLess(negative['expires_at'], positive['expires_at'])
        with self.db.connect() as conn:
            self.assertEqual(2, conn.execute('SELECT count(*) FROM source_response_snapshots').fetchone()[0])

    def test_exact_model_input_reuses_without_timestamp_or_other_model_invalidation(self):
        service = self.service(lambda request: self.fail('No HTTP expected'))
        service._model_request = Mock(return_value=[])
        record = self.record()
        service.llm_suggestions({'title': 'An explicit title'}, [record])
        changed_observation = {**record, 'retrieved_at': '2030-01-01T00:00:00Z', 'expires_at': '2031-01-01T00:00:00Z', 'last_checked_at': '2030-01-01T00:00:00Z'}
        service.settings['llm_models'] = [{'id': 'unrelated', 'model': 'changed'}]
        service.llm_suggestions({'title': 'An explicit title'}, [changed_observation])
        self.assertEqual(1, service._model_request.call_count)
        self.assertEqual('cache_hit', service.calls[-1]['status'])
        service.settings['llm']['temperature'] = 0.6
        service.llm_suggestions({'title': 'An explicit title'}, [record])
        self.assertEqual(2, service._model_request.call_count)
        service.llm_suggestions({'title': 'Different input'}, [record])
        self.assertEqual(3, service._model_request.call_count)

    def test_failed_stale_source_does_not_trigger_new_model_call(self):
        service = self.service(lambda request: self.fail('No HTTP expected'))
        service._model_request = Mock(return_value=[])
        self.assertEqual([], service.llm_suggestions({'title': 'A'}, [self.record(cache_state='stale')]))
        service._model_request.assert_not_called()

    def test_all_candidate_records_are_archived_before_assessment(self):
        service = self.service(lambda request: self.fail('No HTTP expected'))
        envelope = {'retrieved_at': '2026-10-01T00:00:00Z', 'expires_at': '2030-01-01T00:00:00Z'}
        service._record('crossref', 'candidate-1', 'https://example.org/1', {'title': 'Candidate one'}, {}, envelope)
        service._record('crossref', 'candidate-2', 'https://example.org/2', {'title': 'Candidate two'}, {}, envelope)
        listing = self.library.list()
        self.assertEqual(2, listing['total'])
        self.assertEqual({'candidate'}, {item['status'] for item in listing['items']})

    def test_confirmed_identity_survives_task_deletion_and_keeps_aliases(self):
        record = self.record()
        entry = {'citation_key': 'paper', 'fields': dict(record['fields']), 'fingerprint': 'same', 'entry_type': 'article', 'raw': '@article{paper}'}
        task = self.db.create_task('test.bib', b'@article{paper}', {'entries': [entry], 'issues': []}, 'live', 'off', 'hash')
        claimed = self.db.claim()
        result = {'identity_state': 'unique', 'doi_state': 'verified', 'evidence': [record],
                  'assessments': [{'field': 'title', 'suggested': record['fields']['title'], 'judgment': 'consistent', 'evidence_ids': [record['id']]}]}
        self.library.ingest(claimed, result, task)
        self.db.finish(claimed['id'], result)
        self.db.delete_task(task['id'])
        listing = self.library.list(status='confirmed')
        self.assertEqual(1, listing['total'])
        self.assertEqual(['paper'], listing['items'][0]['citation_keys'])
        detail = self.library.detail(listing['items'][0]['id'])
        self.assertEqual(1, len(detail['snapshots']))
        self.assertEqual(task['id'], detail['decisions'][0]['task_id'])
        self.assertIsNone(self.db.task(task['id']))

    def test_preprint_versions_and_published_doi_stay_separate(self):
        for index, eprint in enumerate(['2401.12345v1', '2401.12345v2']):
            record = self.record(str(index), provider='arxiv', source_id=eprint, version='preprint', fields={'title': 'Same work', 'eprint': eprint})
            result = {'identity_state': 'unique', 'doi_state': 'missing', 'evidence': [record],
                      'assessments': [{'field': 'title', 'suggested': 'Same work', 'judgment': 'consistent', 'evidence_ids': [record['id']]}]}
            self.library.ingest({'id': str(index), 'citation_key': 'same', 'fields': record['fields']}, result, {'id': 'task', 'mode': 'live'})
        self.assertEqual(2, self.library.list(status='confirmed')['total'])
        self.library.archive_record(self.record('mock'), 'mock')
        self.assertEqual(1, self.library.list(mode='mock', status='candidate')['total'])
        self.assertEqual(0, self.library.list(mode='mock', status='confirmed')['total'])

    def test_snapshot_observation_failure_and_expiry_are_visible(self):
        record = self.record()
        self.library.archive_record(record)
        record.update(cache_state='stale', revalidation_error={'kind': 'http_error'})
        self.library.archive_record(record)
        item = self.library.list()['items'][0]
        detail = self.library.detail(item['id'])
        self.assertEqual('http_error', detail['snapshots'][0]['revalidation_error']['kind'])
        record.update(cache_state='fresh', revalidation_error=None, expires_at='2000-01-01T00:00:00Z')
        self.library.archive_record(record)
        self.assertEqual('stale', self.library.detail(item['id'])['snapshots'][0]['cache_state'])

    def test_request_cache_is_scoped_to_provider_url_and_auth_configuration(self):
        service = self.service(lambda request: httpx.Response(200, json={'title': 'A'}))
        for provider, url, token in [('one', 'https://example.org/1', 'a'), ('two', 'https://example.org/1', 'a'), ('one', 'https://example.org/2', 'a'), ('one', 'https://example.org/1', 'b')]:
            service._request(provider, 'known_id', url, headers={'Authorization': 'Bearer ' + token})
        self.assertEqual(4, len(self._keys()))
        self.assertEqual(4, sum(bool(call.get('external_call')) for call in service.calls))

    def test_api_library_and_revalidate_without_forcing_fresh_sources(self):
        from fastapi.testclient import TestClient
        from app.main import create_app
        app = create_app(self.temporary.name, start_worker=False)
        self.library.archive_record(self.record())
        with TestClient(app) as client:
            listing = client.get('/api/library?q=explicit&mode=live&status=candidate').json()
            self.assertEqual(1, listing['total'])
            self.assertEqual(200, client.get('/api/library/' + listing['items'][0]['id']).status_code)
            self.assertEqual(404, client.get('/api/library/missing').status_code)
            self.assertEqual(422, client.get('/api/library?mode=invalid').status_code)
            task = client.post('/api/tasks', files={'file': ('paper.bib', b'@article{paper,title={An explicit title},author={Smith, Jane}}')}).json()
            while app.state.worker.run_next():
                pass
            entry = app.state.db.task(task['id'], True)['entries'][0]
            result = entry['result']
            result['expires_at'] = '2000-01-01T00:00:00Z'
            with self.db.connect() as conn:
                conn.execute('UPDATE entries SET result=? WHERE id=?', (json.dumps(result), entry['id']))
            refresh = client.post('/api/tasks/' + task['id'] + '/refresh-stale').json()
            refreshed = app.state.db.task(refresh['id'])
            self.assertTrue(refreshed['options']['revalidate'])
            self.assertFalse(refreshed['options'].get('force', False))
            with patch('app.worker.ProviderService') as factory:
                factory.return_value.collect.return_value = {'records': [], 'errors': [], 'model_suggestions': []}
                app.state.worker.run_next()
                self.assertFalse(factory.return_value.collect.call_args.kwargs['force'])

    def test_worker_keeps_stale_history_but_no_actionable_correction(self):
        from app.bibtex import parse_bibtex
        from app.worker import Worker
        config = Config(Path(self.temporary.name))
        content = b'@article{paper,title={An explicit title},author={Smith, Jane},year={2020}}'
        task = self.db.create_task('test.bib', content, parse_bibtex(content.decode()), 'live', 'off', config.fingerprint('live'))
        record = self.record(cache_state='stale', expires_at='2000-01-01T00:00:00Z')
        record['fields']['year'] = '2024'
        with patch('app.worker.ProviderService') as factory:
            factory.return_value.collect.return_value = {'records': [record], 'errors': [{'provider': 'crossref', 'kind': 'http_error'}], 'model_suggestions': []}
            Worker(self.db, config).run_next()
        result = self.db.task(task['id'], True)['entries'][0]['result']
        self.assertEqual('stale', result['freshness_state'])
        self.assertEqual([record], result['evidence'])
        self.assertFalse(result['accepted_changes'])
        self.assertIsNone(result['suggested_bibtex'])

    def test_literal_search(self):
        self.library.archive_record(self.record(fields={'title': r'100% A_B\C'}))
        self.assertEqual(1, self.library.list(q=r'A_B\C')['total'])
        self.assertEqual(0, self.library.list(q=r'A_B\D')['total'])


if __name__ == '__main__':
    unittest.main()
