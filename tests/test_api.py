import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import httpx
from fastapi.testclient import TestClient
from app.main import create_app
from app.providers import ProviderService

BIB = b'@article{key, title={A Deterministic Study}, author={Example, Alice}, year={2024}}\n'
CROSSREF = {'DOI':'10.5555/study','title':['A Deterministic Study'],'author':[{'family':'Example','given':'Alice'}],'published':{'date-parts':[[2024]]},'container-title':['Evidence Journal'],'volume':'7','page':'10-20','type':'journal-article','URL':'https://doi.org/10.5555/study'}

class ApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.app=create_app(self.tmp.name,start_worker=False)
        self.client=TestClient(self.app)
    def tearDown(self): self.client.close(); self.tmp.cleanup()
    def upload(self, content=BIB, **data):
        response=self.client.post('/api/tasks', files={'file':('source.bib',content)},data=data)
        self.assertEqual(201,response.status_code,response.text)
        return response.json()
    def drain(self):
        while self.app.state.worker.run_next(): pass
    def details(self, task): return self.client.get('/api/tasks/'+task['id']).json()
    def network_factory(self, requests):
        def transport(request):
            requests.append(request)
            if request.url.host=='api.crossref.org':
                data={'message':{'items':[CROSSREF]}} if request.url.path=='/works' else {'message':CROSSREF}
            elif request.url.host=='api.semanticscholar.org':
                row={'paperId':'paper-1','title':'A Deterministic Study','authors':[{'name':'Alice Example'}],'year':2024,'externalIds':{'DOI':'10.5555/study'},'journal':{'name':'Evidence Journal','volume':'7','pages':'10-20'},'url':'https://www.semanticscholar.org/paper/paper-1'}
                data={'data':[row],'total':1} if request.url.path.endswith('/search') else row
            elif request.url.host=='doi.org': data={'title':'A Deterministic Study','author':[{'family':'Example','given':'Alice'}],'DOI':'10.5555/study','issued':{'date-parts':[[2024]]}}
            else: raise AssertionError('Unexpected provider URL: '+str(request.url))
            return httpx.Response(200,json=data)
        def make(settings, cache, call_hook=None):
            return ProviderService(settings,cache,call_hook=call_hook,client=httpx.Client(transport=httpx.MockTransport(transport)),sleep=lambda _:None)
        return make
    def test_upload_original_and_mock_marker(self):
        raw=b'\xef\xbb\xbf'+BIB.replace(b'\n',b'\r\n')
        task=self.upload(raw);self.drain();detail=self.details(task)
        self.assertEqual('completed',detail['status'])
        self.assertTrue(detail['entries'][0]['result']['mock'])
        self.assertEqual(0,detail['api_calls'])
        self.assertEqual(raw,self.client.get('/api/tasks/'+task['id']+'/source').content)
    def test_repeat_upload_no_provider_calls_and_changed_entry_reassessed(self):
        requests=[]
        with patch('app.worker.ProviderService',side_effect=self.network_factory(requests)):
            first=self.upload(mode='live');self.drain();count=len(requests)
            self.assertGreater(count,0)
            second=self.upload(mode='live');self.drain()
            self.assertEqual(count,len(requests))
            self.assertEqual(1,self.details(second)['cache_hits'])
            third=self.upload(BIB.replace(b'2024',b'2023'),mode='live',parent_id=first['id']);self.drain()
            self.assertEqual(count,len(requests))
            result=self.details(third)['entries'][0]['result']
            self.assertTrue(any(a['field']=='year' and a['original']=='2023' and a['judgment']=='unverified' for a in result['assessments']))
            self.assertEqual('2024',self.details(first)['entries'][0]['fields']['year'])
    def test_author_completion_reuses_available_evidence(self):
        requests=[]
        without_author=BIB.replace(b' author={Example, Alice},',b'')
        with patch('app.worker.ProviderService',side_effect=self.network_factory(requests)):
            first=self.upload(without_author,mode='live');self.drain();count=len(requests)
            second=self.upload(mode='live',parent_id=first['id']);self.drain()
            self.assertEqual(count,len(requests))
            self.assertEqual('unique',self.details(second)['entries'][0]['result']['identity_state'])

    def test_cache_survives_database_and_app_restart(self):
        requests=[]
        with patch('app.worker.ProviderService',side_effect=self.network_factory(requests)):
            self.upload(mode='live');self.drain();count=len(requests)
            self.client.close();self.app=create_app(self.tmp.name,start_worker=False);self.client=TestClient(self.app)
            task=self.upload(mode='live');self.drain()
            self.assertEqual(count,len(requests));self.assertEqual('completed',self.details(task)['status'])
    def test_task_budget_preserves_partial_evidence(self):
        self.app.state.config.update({'budget':{'per_task':1,'per_entry':5}})
        requests=[]
        with patch('app.worker.ProviderService',side_effect=self.network_factory(requests)):
            task=self.upload(mode='live');self.drain()
        detail=self.details(task)
        self.assertEqual(1,len(requests));self.assertEqual(1,detail['api_calls'])
        self.assertEqual('partial',detail['status'])
        self.assertTrue(detail['entries'][0]['result']['evidence'])
        self.assertTrue(any(e['kind']=='budget_exhausted' for e in detail['entries'][0]['result']['provider_errors']))
    def test_enable_cnki_only_runs_new_branch(self):
        first=self.upload();self.drain()
        second=self.upload(cnki_mode='all',parent_id=first['id']);self.drain()
        result=self.details(second)['entries'][0]['result']
        self.assertTrue(result['calls'])
        self.assertEqual({'cnki'}, {c['provider'] for c in result['calls']})
        self.assertTrue(any(r['provider']=='crossref' for r in result['evidence']))
    def test_cancel_and_retry_queued_entries(self):
        task=self.upload();self.client.post('/api/tasks/'+task['id']+'/cancel')
        self.assertFalse(self.app.state.worker.run_next())
        self.client.post('/api/tasks/'+task['id']+'/retry');self.drain()
        self.assertEqual('completed',self.details(task)['status'])
    def test_manual_import_preserves_historical_result(self):
        task=self.upload();self.drain();old=self.details(task);entry=old['entries'][0]
        r=self.client.post('/api/entries/'+entry['id']+'/manual',json={'format':'ris','source_url':'https://example.org/record','text':'TY  - JOUR\nTI  - A Deterministic Study\nAU  - Example, Alice\nPY  - 2024\nDO  - 10.5555/manual\nER  - '})
        self.assertEqual(201,r.status_code,r.text);self.drain();new=self.details(r.json())
        self.assertNotEqual(task['id'],new['id'])
        self.assertEqual(old,self.details(task))
        self.assertTrue(any(e['method']=='manual' for e in new['entries'][0]['result']['evidence']))
        self.assertEqual(0,new['api_calls'])
    def test_scoped_refresh_preserves_other_sources(self):
        task=self.upload(cnki_mode='all');self.drain();entry=self.details(task)['entries'][0]
        r=self.client.post('/api/entries/'+entry['id']+'/refresh',json={'sources':['cnki']})
        self.assertEqual(200,r.status_code,r.text);self.drain();new=self.details(r.json())
        result=new['entries'][0]['result']
        self.assertEqual({'cnki'},{c['provider'] for c in result['calls']})
        self.assertTrue(any(e['provider']=='crossref' for e in result['evidence']))
    def test_api_secret_redaction(self):
        secret='fake-"secret\\key'
        r=self.client.put('/api/settings',json={'llm':{'api_key':secret}})
        self.assertEqual(200,r.status_code)
        self.assertNotIn(secret,r.text)
        self.assertNotIn('api_key"',r.text)
        task=self.upload();self.drain()
        self.assertNotIn(secret,self.client.get('/api/tasks/'+task['id']+'/report').text)
    def test_local_security_and_limits(self):
        r=self.client.post('/api/cache/clear',headers={'Origin':'https://attacker.example'})
        self.assertEqual(403,r.status_code)
        r=self.client.get('/api/health',headers={'Host':'attacker.example'})
        self.assertEqual(400,r.status_code)
        r=self.client.post('/api/tasks',files={'file':('bad.txt',BIB)})
        self.assertEqual(422,r.status_code)
        r=self.client.post('/api/tasks',files={'file':('bad.bib',b'\xff')})
        self.assertEqual(422,r.status_code)
        r=self.client.post('/api/tasks',files={'file':('large.bib',b' '*2_200_000)})
        self.assertEqual(413,r.status_code)
        self.assertIn("frame-ancestors 'none'",self.client.get('/api/health').headers['content-security-policy'])
    def test_malformed_bodies_return_client_errors(self):
        task=self.upload();self.drain();entry=self.details(task)['entries'][0]
        for path in ('/api/entries/'+entry['id']+'/refresh','/api/entries/'+entry['id']+'/manual'):
            self.assertEqual(422,self.client.post(path,content='bad').status_code)
            self.assertEqual(422,self.client.post(path,json=[]).status_code)
        self.assertEqual(422,self.client.post('/api/entries/'+entry['id']+'/refresh',json={'sources':[{}]}).status_code)
    def test_changed_entry_retains_still_active_manual_evidence(self):
        first=self.upload();self.drain();entry=self.details(first)['entries'][0]
        manual=self.client.post('/api/entries/'+entry['id']+'/manual',json={'format':'text','text':'title: A Deterministic Study\nauthor: Example, Alice\nyear: 2024','source_url':'https://example.org/manual'}).json()
        self.drain()
        again=self.upload(BIB.replace(b'2024',b'2023'),parent_id=manual['id']);self.drain()
        result=self.details(again)['entries'][0]['result']
        self.assertTrue(any(r['method']=='manual' for r in result['evidence']))
        self.assertEqual(1,result['manual_records'])
        self.assertEqual([],result['calls'])

    def test_removed_manual_record_cannot_return_from_history(self):
        first=self.upload();self.drain();entry=self.details(first)['entries'][0]
        manual=self.client.post('/api/entries/'+entry['id']+'/manual',json={'format':'text','text':'title: A Deterministic Study\nauthor: Example, Alice\nyear: 2024','source_url':'https://example.org/manual'}).json()
        self.drain();historical=self.details(manual)
        self.assertTrue(any(r['method']=='manual' for r in historical['entries'][0]['result']['evidence']))
        self.assertEqual(200,self.client.delete('/api/manual-records').status_code)
        again=self.upload(parent_id=manual['id']);self.drain()
        result=self.details(again)['entries'][0]['result']
        self.assertFalse(any(r['method']=='manual' for r in result['evidence']))
        self.assertEqual(0,result['manual_records'])
        self.assertEqual(historical,self.details(manual))

    def test_restart_with_changed_environment_does_not_reuse_old_hash(self):
        first=self.upload()
        with patch.dict('os.environ',{'CITE_AUDIT_LLM_MODEL':'new-environment-model'}):
            self.drain()
            failed=self.details(first)
            self.assertEqual('failed',failed['entries'][0]['status'])
            self.assertIn('configuration changed',failed['entries'][0]['error'])
            self.assertEqual(0,failed['api_calls'])
            second=self.client.post('/api/tasks/'+first['id']+'/retry').json()
            self.assertNotEqual(first['id'],second['id'])
            self.drain()
            self.assertEqual('completed',self.details(second)['status'])

    def test_configuration_change_retry_uses_new_task_and_hash(self):
        self.client.put('/api/settings',json={'llm':{'model':'model-A'}})
        first=self.upload();self.client.post('/api/tasks/'+first['id']+'/cancel')
        self.client.put('/api/settings',json={'llm':{'model':'model-B'}})
        retry=self.client.post('/api/tasks/'+first['id']+'/retry')
        self.assertEqual(200,retry.status_code)
        second=retry.json();self.assertNotEqual(first['id'],second['id'])
        self.assertNotEqual(first['config_hash'],second['config_hash'])
        self.drain()
        self.client.put('/api/settings',json={'llm':{'model':'model-A'}})
        third=self.upload();self.drain()
        self.assertEqual('providers',self.details(third)['entries'][0]['result']['cache_source'])

    def test_cancelled_inflight_blocks_configuration_changes(self):
        task=self.upload();entry=self.app.state.db.claim();self.client.post('/api/tasks/'+task['id']+'/cancel')
        self.assertEqual(409,self.client.put('/api/settings',json={'llm':{'model':'other'}}).status_code)
        self.app.state.db.finish(entry['id'],{})
        self.assertEqual(200,self.client.put('/api/settings',json={'llm':{'model':'other'}}).status_code)

    def test_negative_result_has_expiry_and_is_not_immediately_stale(self):
        from datetime import datetime, timezone
        first=self.upload(BIB.replace(b'key,',b'unresolved,'));self.drain()
        result=self.details(first)['entries'][0]['result']
        expiry=datetime.fromisoformat(result['expires_at'])
        self.assertLessEqual((expiry-datetime.now(timezone.utc)).total_seconds(),7*86400)
        response=self.client.post('/api/tasks/'+first['id']+'/refresh-stale').json()
        self.assertTrue(response.get('unchanged'))
        second=self.upload(BIB.replace(b'key,',b'unresolved,'));self.drain()
        self.assertEqual(result['expires_at'],self.details(second)['entries'][0]['result']['expires_at'])

    def test_positive_preprint_and_negative_publication_expires_in_seven_days(self):
        from datetime import datetime, timezone
        def transport(request):
            if request.url.host=='api.crossref.org':return httpx.Response(200,json={'message':{'items':[]}})
            if request.url.host=='api.semanticscholar.org':
                return httpx.Response(200,json={'data':[]}) if request.url.path.endswith('/search') else httpx.Response(404)
            if request.url.host=='export.arxiv.org':
                return httpx.Response(200,text='<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>http://arxiv.org/abs/2401.00001v1</id><title>A Deterministic Study</title><author><name>Alice Example</name></author><published>2024-01-01T00:00:00Z</published></entry></feed>')
            raise AssertionError(str(request.url))
        def make(settings,cache,call_hook=None):return ProviderService(settings,cache,call_hook=call_hook,client=httpx.Client(transport=httpx.MockTransport(transport)),sleep=lambda _:None)
        data=BIB.replace(b'@article',b'@misc').replace(b'year={2024}',b'year={2024}, eprint={2401.00001}, archivePrefix={arXiv}')
        with patch('app.worker.ProviderService',side_effect=make):
            task=self.upload(data,mode='live');self.drain()
        result=self.details(task)['entries'][0]['result']
        self.assertEqual('not_found',result['publication_state'])
        self.assertTrue(result['evidence'])
        self.assertLessEqual((datetime.fromisoformat(result['expires_at'])-datetime.now(timezone.utc)).total_seconds(),7*86400)

    def test_citation_key_rename_rebinds_cached_suggestion(self):
        self.upload();self.drain()
        renamed=self.upload(BIB.replace(b'{key,',b'{renamed,'));self.drain()
        result=self.details(renamed)['entries'][0]['result']
        self.assertTrue(result['suggested_bibtex'].startswith('@article{renamed,'))
        self.assertEqual(1,self.details(renamed)['cache_hits'])

    def test_raw_xss_remains_data_and_no_external_auto_requests(self):
        task=self.upload(BIB.replace(b'A Deterministic Study',b'<script>alert(1)</script>'));self.drain()
        self.assertIn('<script>',self.details(task)['entries'][0]['fields']['title'])
        self.assertEqual(0,self.details(task)['api_calls'])
    def test_full_demo_processes_and_issues_surface(self):
        data=(Path(__file__).resolve().parent.parent/'examples/demo.bib').read_bytes()
        task=self.upload(data);self.drain();detail=self.details(task)
        self.assertEqual(8,detail['total']);self.assertEqual(8,detail['completed'])
        self.assertEqual(0,detail['failed'])
        self.assertTrue(any(e['result']['publication_state']=='candidate' for e in detail['entries']))

if __name__=='__main__':unittest.main()
