"""Provider configuration, selection and retained offline audit contracts."""
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.config import Config
from app.main import create_app
from app.storage import Database

PROVIDERS = [
    {'id': 'alpha', 'name': 'Alpha', 'client_type': 'openai-chat', 'base_url': 'https://alpha.example/v1', 'api_key': 'alpha-secret'},
    {'id': 'beta', 'name': 'Beta', 'client_type': 'ant-messages', 'base_url': 'https://beta.example', 'api_key': 'beta-secret'},
]
MODELS = [
    {'id': 'fast', 'name': 'Fast', 'provider_id': 'alpha', 'model': 'model-a', 'temperature': 0, 'timeout': 30, 'max_output_tokens': 2000, 'max_retries': 2},
    {'id': 'careful', 'name': 'Careful', 'provider_id': 'beta', 'model': 'model-b', 'temperature': 0.2, 'timeout': 60, 'max_output_tokens': 4000, 'max_retries': 0},
]
SETTINGS = {'llm_providers': PROVIDERS, 'llm_models': MODELS, 'default_model_id': 'fast'}
BIB = b'@article{a,title={An offline test},author={Example, Alice},year={2024}}'


class ProviderConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.config = Config(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_legacy_settings_migrate_without_secret_exposure(self):
        self.config.update({'llm': {'model': 'old-model', 'api_key': 'old-secret'}})
        restored = Config(Path(self.tmp.name))
        self.assertEqual('legacy', restored.get()['llm']['model_config_id'])
        self.assertEqual('old-model', restored.get()['llm']['model'])
        self.assertEqual('old-secret', restored.get()['llm']['api_key'])
        self.assertNotIn('old-secret', json.dumps(restored.public()))

    def test_pre_provider_settings_file_is_read_without_rewriting(self):
        original = {'llm': {'base_url': 'https://custom.example/v1', 'model': 'old', 'api_key': 'old-secret'}}
        self.config.path.write_text(json.dumps(original), encoding='utf-8')
        restored = Config(Path(self.tmp.name))
        self.assertEqual('old', restored.get()['llm']['model'])
        self.assertEqual('https://custom.example/v1', restored.get()['llm']['base_url'])
        self.assertEqual(original, json.loads(restored.path.read_text(encoding='utf-8')))
        self.assertNotIn('old-secret', json.dumps(restored.public()))

    def test_select_models_and_public_secret_flags(self):
        public = self.config.update(copy.deepcopy(SETTINGS))
        self.assertNotIn('alpha-secret', json.dumps(public))
        self.assertNotIn('beta-secret', json.dumps(public))
        self.assertTrue(public['llm_providers'][0]['api_key_configured'])
        self.assertEqual('model-b', self.config.get('careful')['llm']['model'])
        self.assertEqual('ant-messages', self.config.get('careful')['llm']['client_type'])
        self.assertEqual('beta-secret', self.config.get('careful')['llm']['api_key'])
        self.assertNotEqual(self.config.fingerprint('live', 'fast'), self.config.fingerprint('live', 'careful'))
        with self.assertRaises(ValueError): self.config.get('missing')

    def test_blank_preserves_null_removes_key_and_old_slot_is_redacted(self):
        self.config.update(copy.deepcopy(SETTINGS))
        providers = self.config.public()['llm_providers']
        self.config.update({'llm_providers': providers})
        self.assertEqual('alpha-secret', self.config.get()['llm']['api_key'])
        providers[0]['api_key'] = ''
        self.config.update({'llm_providers': providers})
        self.assertEqual('alpha-secret', self.config.get()['llm']['api_key'])
        providers[0]['api_key'] = None
        self.config.update({'llm_providers': providers})
        self.assertFalse(self.config.public()['llm']['api_key_configured'])
        self.assertNotIn('beta-secret', str(self.config.redact({'trace': 'beta-secret'})))

    def test_removed_legacy_key_cannot_be_resurrected_by_older_client(self):
        self.config.update({'llm': {'model': 'old-model', 'api_key': 'remove-this-key'}})
        providers = self.config.public()['llm_providers']
        providers[0]['api_key'] = None
        self.config.update({'llm_providers': providers})
        self.assertNotIn('remove-this-key', self.config.path.read_text(encoding='utf-8'))
        self.config.update({'llm': {'model': 'new-model'}})
        self.assertEqual('', self.config.get()['llm']['api_key'])
        self.assertFalse(self.config.public()['llm']['api_key_configured'])

    def test_environment_only_affects_legacy_slot(self):
        self.config.update(copy.deepcopy(SETTINGS))
        with patch.dict(os.environ, {'CITE_AUDIT_LLM_API_KEY': 'environment-key', 'CITE_AUDIT_LLM_MODEL': 'environment-model'}):
            self.assertEqual('model-a', self.config.get()['llm']['model'])
            self.assertEqual('environment-model', self.config.get('legacy')['llm']['model'])
            self.assertNotIn('environment-key', json.dumps(self.config.public()))
            self.assertNotIn('environment-key', str(self.config.redact({'error': 'environment-key'})))

    def test_bad_references_protocols_and_numbers_are_atomic(self):
        self.config.update(copy.deepcopy(SETTINGS))
        previous = self.config.public()
        bad_changes = [
            {'default_model_id': 'missing'},
            {'llm_providers': [PROVIDERS[0], PROVIDERS[0]]},
            {'llm_models': [{**MODELS[0], 'provider_id': 'missing'}]},
            {'llm_providers': [{**PROVIDERS[0], 'client_type': 'unknown'}]},
            {'llm_providers': [{**PROVIDERS[0], 'base_url': 'http://localhost:8080'}]},
            {'llm_models': [{**MODELS[0], 'temperature': float('nan')}]},
            {'llm_models': [{**MODELS[0], 'max_retries': 1.5}]},
            {'llm_models': [{**MODELS[0], 'timeout': True}]},
        ]
        for changes in bad_changes:
            with self.subTest(changes=changes), self.assertRaises(ValueError): self.config.update(changes)
            self.assertEqual(previous, self.config.public())

    def test_disable_default_and_unused_provider_change_keeps_selected_fingerprint(self):
        self.config.update(copy.deepcopy(SETTINGS))
        fingerprint = self.config.fingerprint('live', 'fast')
        providers = copy.deepcopy(PROVIDERS); providers[1]['name'] = 'Renamed beta'
        self.config.update({'llm_providers': providers})
        self.assertEqual(fingerprint, self.config.fingerprint('live', 'fast'))
        self.config.update({'default_model_id': ''})
        self.assertEqual('', self.config.get()['llm']['model'])
        self.assertEqual('model-a', self.config.get('fast')['llm']['model'])


class AuditApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = create_app(self.tmp.name, start_worker=False)
        self.client = TestClient(self.app)
        self.app.state.config.update(copy.deepcopy(SETTINGS))

    def tearDown(self):
        self.client.close()
        self.tmp.cleanup()

    def test_explicit_model_task_selection_and_validation(self):
        missing = self.client.post('/api/tasks', files={'file': ('a.bib', BIB)}, data={'model_id': 'missing'})
        self.assertEqual(422, missing.status_code)
        response = self.client.post('/api/tasks', files={'file': ('a.bib', BIB)}, data={'model_id': 'careful'})
        self.assertEqual(201, response.status_code, response.text)
        task = response.json()
        self.assertEqual('careful', task['options']['model_id'])
        self.assertEqual(self.app.state.config.fingerprint('mock', 'careful'), task['config_hash'])
        self.assertEqual(409, self.client.put('/api/settings', json={'default_model_id': 'careful'}).status_code)

    def test_nondefault_model_runs_and_default_change_does_not_override_it(self):
        from app.providers import ProviderService
        selected = []
        def service(settings, cache, call_hook=None):
            selected.append(settings['llm']['model_config_id'])
            return ProviderService(settings, cache, call_hook=call_hook)
        task = self.client.post('/api/tasks', files={'file': ('a.bib', BIB)}, data={'model_id': 'careful'}).json()
        # Simulate a restart with a different default; the task captures its own model.
        self.app.state.config.update({'default_model_id': ''})
        with patch('app.worker.ProviderService', side_effect=service):
            while self.app.state.worker.run_next(): pass
        detail = self.app.state.db.task(task['id'], True)
        self.assertEqual('completed', detail['status'])
        self.assertEqual(['careful'], selected)
        self.assertEqual('careful', detail['options']['model_id'])
        self.assertEqual(0, detail['api_calls'])

    def test_retry_refresh_and_manual_keep_selected_model(self):
        task = self.client.post('/api/tasks', files={'file': ('a.bib', BIB)}, data={'model_id': 'careful'}).json()
        self.app.state.db.cancel(task['id'])
        models = copy.deepcopy(MODELS); models[1]['temperature'] = 0.4
        self.app.state.config.update({'llm_models': models})
        retry = self.client.post('/api/tasks/' + task['id'] + '/retry')
        self.assertEqual(200, retry.status_code, retry.text)
        retry_task = retry.json()
        self.assertNotEqual(task['id'], retry_task['id'])
        self.assertEqual('careful', retry_task['options']['model_id'])
        while self.app.state.worker.run_next(): pass
        entry = self.app.state.db.task(retry_task['id'], True)['entries'][0]
        refresh = self.client.post('/api/entries/' + entry['id'] + '/refresh', json={})
        self.assertEqual(200, refresh.status_code, refresh.text)
        self.assertEqual('careful', refresh.json()['options']['model_id'])
        while self.app.state.worker.run_next(): pass
        manual = self.client.post('/api/entries/' + entry['id'] + '/manual', json={
            'format': 'text', 'text': 'title: An offline test\nauthor: Example, Alice\nyear: 2024',
            'source_url': 'https://example.org/record'})
        self.assertEqual(201, manual.status_code, manual.text)
        self.assertEqual('careful', manual.json()['options']['model_id'])

    def test_missing_saved_model_fails_task_without_crashing_worker(self):
        task = self.client.post('/api/tasks', files={'file': ('a.bib', BIB)}, data={'model_id': 'careful'}).json()
        # Settings API blocks this while active. A manually edited saved config
        # after a restart must still fail safely rather than strand the queue.
        self.app.state.config.update({'llm_models': [MODELS[0]]})
        self.assertTrue(self.app.state.worker.run_next())
        self.assertEqual('partial', self.app.state.db.task(task['id'])['status'])
        self.assertEqual('failed', self.app.state.db.task(task['id'], True)['entries'][0]['status'])
        self.assertEqual(422, self.client.post('/api/tasks/' + task['id'] + '/retry').status_code)
        self.assertFalse(self.app.state.worker.run_next())

    def test_audit_survives_task_deletion_and_does_not_double_count(self):
        task = self.client.post('/api/tasks', files={'file': ('a.bib', BIB)}).json()
        db = self.app.state.db
        entry = db.task(task['id'], True)['entries'][0]
        event = {'provider': 'llm', 'provider_id': 'alpha', 'external_call': True,
                 'cost': {'status': 'estimated', 'amount': 0.0123, 'currency': 'USD', 'pricing': {'prompt_tokens': 1}}}
        db.add_call(task['id'], entry['id'], event)
        db.add_call(task['id'], entry['id'], {'provider': 'llm', 'external_call': True, 'cost': {'status': 'unknown', 'amount': None}})
        db.add_call(task['id'], entry['id'], {'provider': 'llm', 'external_call': False, 'cost': {'status': 'estimated', 'amount': 50, 'currency': 'USD'}})
        report = self.client.get('/api/audit', params={'task_id': task['id']}).json()
        self.assertEqual(3, report['total'])
        self.assertEqual(2, report['summary']['total_calls'])
        self.assertEqual(1, report['summary']['unknown_cost_calls'])
        self.assertEqual([{'currency': 'USD', 'amount': 0.0123}], report['summary']['estimated_costs'])
        db.cancel(task['id']); db.delete_task(task['id'])
        self.assertEqual(3, self.client.get('/api/audit', params={'task_id': task['id']}).json()['total'])
        Database(self.tmp.name)
        self.assertEqual(3, self.client.get('/api/audit', params={'task_id': task['id']}).json()['total'])
        self.assertEqual(1, self.client.get('/api/audit', params={'provider_id': 'alpha'}).json()['total'])
        self.assertEqual(422, self.client.get('/api/audit', params={'limit': 1001}).status_code)

    def test_deleted_model_refresh_is_explicit_client_error(self):
        task = self.client.post('/api/tasks', files={'file': ('a.bib', BIB)}, data={'model_id': 'careful'}).json()
        while self.app.state.worker.run_next(): pass
        detail = self.app.state.db.task(task['id'], True)
        self.app.state.config.update({'llm_models': [MODELS[0]]})
        response = self.client.post('/api/entries/' + detail['entries'][0]['id'] + '/refresh', json={})
        self.assertEqual(422, response.status_code)

    def test_connection_test_uses_selected_model_and_records_attempt(self):
        selected = []
        class FakeService:
            def __init__(self, settings, cache, call_hook=None):
                selected.append(settings['llm']['model_config_id']); self.hook = call_hook
            def test_connection(self, provider):
                self.hook({'provider': 'llm', 'provider_id': 'beta', 'external_call': True, 'error': 'beta-secret'})
                return {'ok': False, 'status': 'synthetic_failure'}
            def close(self): pass
        with patch('app.main.ProviderService', FakeService):
            response = self.client.post('/api/settings/test/model/careful')
        self.assertEqual(200, response.status_code)
        self.assertEqual(['careful'], selected)
        audit = self.client.get('/api/audit').json()
        self.assertEqual(1, audit['total'])
        self.assertEqual('connection_test', audit['calls'][0]['scope'])
        self.assertNotIn('beta-secret', json.dumps(audit))
        self.assertEqual(1, audit['summary']['unknown_cost_calls'])


if __name__ == '__main__':
    unittest.main()


class SchemaUpgradeTests(unittest.TestCase):
    def test_v1_upgrade_backfills_audit_once_and_preserves_old_data(self):
        import sqlite3
        import app.storage as storage
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'cite-audit.sqlite3'
            migrations = Path(storage.__file__).parent / 'migrations'
            with sqlite3.connect(path) as conn:
                conn.executescript((migrations / '001_init.sql').read_text())
                conn.execute("INSERT INTO provider_calls(task_id,entry_id,created_at,cache_hit,payload) VALUES(NULL,NULL,'old',0,?)", (json.dumps({'external_call': True, 'provider_id': 'old-provider'}),))
            db = Database(directory)
            self.assertEqual(1, db.audit()['total'])
            self.assertEqual('old-provider', db.audit()['calls'][0]['provider_id'])
            with db.connect() as conn:
                latest = max(int(p.name.split('_', 1)[0]) for p in migrations.glob('*.sql'))
                self.assertEqual(latest, conn.execute('PRAGMA user_version').fetchone()[0])
                self.assertIsNotNone(conn.execute("SELECT name FROM sqlite_master WHERE name='library_items'").fetchone())
                # Deliberately remove the backfilled row: reopening must not
                # replay migration002 and unexpectedly recreate it.
                conn.execute('DELETE FROM audit_calls')
            reopened = Database(directory)
            self.assertEqual(0, reopened.audit()['total'])
            with reopened.connect() as conn:
                self.assertEqual(1, conn.execute('SELECT COUNT(*) FROM provider_calls').fetchone()[0])
                self.assertEqual(latest, conn.execute('PRAGMA user_version').fetchone()[0])

    def test_fresh_database_reopen_does_not_replay_schema_scripts(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Database(directory)
            with db.connect() as conn:
                version = conn.execute('PRAGMA user_version').fetchone()[0]
                conn.execute('DROP INDEX entries_fingerprint')
            db = Database(directory)
            with db.connect() as conn:
                self.assertEqual(version, conn.execute('PRAGMA user_version').fetchone()[0])
                self.assertIsNone(conn.execute("SELECT name FROM sqlite_master WHERE name='entries_fingerprint'").fetchone())

    def test_config_uses_canonical_audit_rules_version(self):
        from app import audit, config
        self.assertEqual(audit.RULE_VERSION, config.RULE_VERSION)

    def test_failed_migration_rolls_back_schema_and_version(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as directory:
            db = Database(directory)
            with db.connect() as conn:
                version = conn.execute('PRAGMA user_version').fetchone()[0]
            migration = Path(directory) / f'{version + 1:03d}_broken.sql'
            migration.write_text('CREATE TABLE must_rollback(id INTEGER);\nINVALID SQL;\n', encoding='utf-8')
            import app.storage as storage
            schemas = list((Path(storage.__file__).parent / 'migrations').glob('*.sql'))
            with patch('app.storage.Path.glob', return_value=schemas + [migration]):
                with self.assertRaises(sqlite3.OperationalError):
                    Database(directory)
            with db.connect() as conn:
                self.assertEqual(version, conn.execute('PRAGMA user_version').fetchone()[0])
                self.assertIsNone(conn.execute("SELECT name FROM sqlite_master WHERE name='must_rollback'").fetchone())

    def test_newer_schema_is_not_opened_or_downgraded(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Database(directory)
            with db.connect() as conn:
                conn.execute('PRAGMA user_version=999')
            with self.assertRaisesRegex(ValueError, 'newer'):
                Database(directory)
            with db.connect() as conn:
                self.assertEqual(999, conn.execute('PRAGMA user_version').fetchone()[0])

    def test_migration_sequence_rejects_gaps_and_duplicates(self):
        import app.storage as storage
        schemas = sorted((Path(storage.__file__).parent / 'migrations').glob('*.sql'))
        for invalid in ([schemas[1]], [schemas[0], schemas[0]]):
            with self.subTest(invalid=invalid), tempfile.TemporaryDirectory() as directory:
                with patch('app.storage.Path.glob', return_value=invalid):
                    with self.assertRaisesRegex(ValueError, 'contiguous'):
                        Database(directory)
