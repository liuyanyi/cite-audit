import copy
import unittest
import httpx
from app.config import DEFAULTS
from app.providers import ProviderService

class CapabilityTests(unittest.TestCase):
    def test_no_search_results_cannot_claim_fetch_capability(self):
        settings=copy.deepcopy(DEFAULTS);settings['mode']='live';settings['deepseek']['api_key']='synthetic-key'
        service=ProviderService(settings,client=httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(200,json={'results':[]}))),sleep=lambda _:None)
        result=service.test_connection('deepseek')
        self.assertFalse(result['ok'])
        self.assertEqual('not_verified',result['capabilities']['fetch'])
        self.assertTrue(result['capabilities']['search'])

    def test_nonfinite_provider_json_is_a_recoverable_error(self):
        settings=copy.deepcopy(DEFAULTS);settings['mode']='live'
        service=ProviderService(settings,client=httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(200,text='{"message":{"items":[],"bad":NaN}}'))),sleep=lambda _:None)
        result=service.collect({'fields':{'title':'Synthetic paper'}},sources=['crossref'])
        self.assertEqual('invalid_response',result['errors'][0]['kind'])
        self.assertEqual([],result['records'])

if __name__=='__main__':unittest.main()
