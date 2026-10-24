import os
import tempfile
import time
import unittest
from pathlib import Path
from app.config import Config
from app.storage import Database, PersistentCache

ENTRY = {'citation_key':'test','entry_type':'article','fields':{'title':'A title'},'raw':'@article{test,title={A title}}','fingerprint':'one','issues':[],'is_chinese':False}

class StorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(self.tmp.name)
    def tearDown(self): self.tmp.cleanup()
    def create(self): return self.db.create_task('test.bib', b'\xef\xbb\xbf@article{test,title={A title}}\r\n', {'entries':[ENTRY],'issues':[]}, 'mock', 'off', 'config')
    def test_raw_bytes_and_reload(self):
        task = self.create()
        expected = self.db.source(task['id'])['content']
        reloaded = Database(self.tmp.name)
        self.assertEqual(expected, reloaded.source(task['id'])['content'])
        self.assertEqual(1, reloaded.task(task['id'])['total'])
    def test_durable_cache_and_expiry(self):
        cache = PersistentCache(self.db)
        cache.put('a', {'value':'α'}, 3600)
        self.assertEqual({'value':'α'}, PersistentCache(Database(self.tmp.name)).get('a'))
        cache.put('b', {}, -1)
        self.assertIsNone(cache.get('b'))
    def test_atomic_claim_recovery_cancel_retry(self):
        task = self.create()
        entry = self.db.claim()
        self.assertIsNone(self.db.claim())
        self.db.recover()
        self.assertEqual(entry['id'], self.db.claim()['id'])
        self.db.cancel(task['id'])
        with self.assertRaises(ValueError): self.db.retry(task['id'])
        self.db.finish(entry['id'], {'evidence':[]})
        self.assertEqual('cancelled', self.db.entry(entry['id'])['status'])
        self.db.retry(task['id'])
        self.assertIsNotNone(self.db.claim())
    def test_cancelled_crash_recovery(self):
        task = self.create(); entry = self.db.claim(); self.db.cancel(task['id']); self.db.recover()
        self.assertEqual('cancelled', self.db.entry(entry['id'])['status'])
        self.assertIsNone(self.db.claim())
    def test_history_survives_cache_clear(self):
        task = self.create(); entry = self.db.claim()
        result = {'evidence':[{'id':'e','fields':{'title':'Original'}}]}
        self.db.finish(entry['id'], result)
        cache = PersistentCache(self.db); cache.put('e', {'changed':True}, 30); cache.clear()
        self.assertEqual(result, self.db.entry(entry['id'])['result'])
    def test_delete_removes_source_not_shared_cache(self):
        task = self.create()
        with self.assertRaises(ValueError): self.db.delete_task(task['id'])
        self.db.cancel(task['id']); PersistentCache(self.db).put('shared', {'a':1}, 30)
        self.db.delete_task(task['id'])
        self.assertIsNone(self.db.source(task['id']))
        self.assertEqual({'a':1}, PersistentCache(self.db).get('shared'))
    def test_secret_never_in_public_config(self):
        cfg = Config(Path(self.tmp.name))
        cfg.update({'llm':{'api_key':'unique-test-secret'}})
        self.assertNotIn('unique-test-secret', str(cfg.public()))
        self.assertTrue(cfg.public()['llm']['api_key_configured'])
        self.assertNotIn('unique-test-secret', str(cfg.redact({'error':'oops unique-test-secret'})))
        if os.name != 'nt':
            self.assertEqual(0o600, cfg.path.stat().st_mode & 0o777)
        else:
            # Windows chmod controls read-only, not POSIX owner/group ACLs.
            self.assertTrue(cfg.path.stat().st_mode & 0o200)
    def test_unicode_configuration_is_utf8_and_survives_reload(self):
        cfg=Config(Path(self.tmp.name));cfg.update({'llm':{'model':'中文模型','api_key':'测试-key'}})
        self.assertIn('中文模型',cfg.path.read_bytes().decode('utf-8'))
        restored=Config(Path(self.tmp.name))
        self.assertEqual('中文模型',restored.get()['llm']['model'])
        self.assertNotIn('测试-key',str(restored.public()))

    def test_secret_with_json_metacharacters(self):
        cfg = Config(Path(self.tmp.name))
        secret = 'synthetic-"quoted\\key'
        cfg.update({'deepseek':{'api_key':secret}})
        scrubbed = cfg.redact({'error':secret, secret:[{'nested':secret}]})
        self.assertNotIn(secret, str(scrubbed))
        self.assertEqual('[redacted]', scrubbed['error'])
        self.assertNotIn(secret, str(cfg.public()))

    def test_blank_environment_template_does_not_shadow_saved_key(self):
        from unittest.mock import patch
        cfg=Config(Path(self.tmp.name));cfg.update({'llm':{'api_key':'local-key','model':'local-model'}})
        with patch.dict('os.environ',{'CITE_AUDIT_LLM_API_KEY':'','CITE_AUDIT_LLM_MODEL':''}):
            self.assertEqual('local-key',cfg.get()['llm']['api_key'])
            self.assertEqual('local-model',cfg.get()['llm']['model'])

    def test_bad_config_is_transactional(self):
        cfg = Config(Path(self.tmp.name))
        with self.assertRaises(ValueError): cfg.update({'budget':{'per_entry':0}})
        self.assertEqual(20,cfg.get()['budget']['per_entry'])
        with self.assertRaises(ValueError): cfg.update({'llm':{'base_url':'http://127.0.0.1'}})
        with self.assertRaises(ValueError): cfg.update({'llm':{'base_url':'https://user:secret@example.com'}})

if __name__ == '__main__': unittest.main()
