import copy
import json
import os
import unittest
from unittest.mock import patch

import httpx

from app.llm import estimate_cost, invoke, LLMCallError
from app.providers import ProviderService, _LAST_ATTEMPT, _NOT_BEFORE


def sse(events):
    return ''.join(('event: ' + event['type'] + '\n' if 'type' in event else '') + 'data: ' + json.dumps(event) + '\n\n' for event in events)


def chat_reply(text='{"suggestions": []}', usage=True, finish='stop'):
    event = {'id': 'test', 'object': 'chat.completion.chunk', 'created': 0, 'model': 'test',
             'choices': [{'index': 0, 'delta': {'content': text}, 'finish_reason': finish}]}
    if usage:
        event['usage'] = {'prompt_tokens': 10, 'completion_tokens': 5, 'total_tokens': 15,
                          'prompt_tokens_details': {'cached_tokens': 0}, 'completion_tokens_details': {'reasoning_tokens': 0}}
    return sse([event]) + 'data: [DONE]\n\n'


def responses_reply():
    return sse([
        {'type': 'response.output_text.delta', 'item_id': 'msg', 'output_index': 0, 'content_index': 0, 'sequence_number': 0, 'delta': '{"suggestions": []}'},
        {'type': 'response.completed', 'sequence_number': 1, 'response': {'id': 'r1', 'status': 'completed',
         'usage': {'input_tokens': 10, 'output_tokens': 5, 'total_tokens': 15, 'input_tokens_details': {'cached_tokens': 0}, 'output_tokens_details': {'reasoning_tokens': 0}}}},
    ])


def anthropic_reply():
    return sse([
        {'type': 'message_start', 'message': {'id': 'm1', 'type': 'message', 'role': 'assistant', 'content': [], 'model': 'test', 'stop_reason': None, 'stop_sequence': None,
         'usage': {'input_tokens': 10, 'output_tokens': 0, 'cache_read_input_tokens': 0, 'cache_creation_input_tokens': 0}}},
        {'type': 'content_block_start', 'index': 0, 'content_block': {'type': 'tool_use', 'id': 'tool1', 'name': 'submit_audit', 'input': {}}},
        {'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'input_json_delta', 'partial_json': '{"suggestions": []}'}},
        {'type': 'content_block_stop', 'index': 0},
        {'type': 'message_delta', 'delta': {'stop_reason': 'tool_use', 'stop_sequence': None},
         'usage': {'output_tokens': 5, 'output_tokens_details': {'thinking_tokens': 0}}},
        {'type': 'message_stop'},
    ])


class MMSPTests(unittest.TestCase):
    def setUp(self):
        _LAST_ATTEMPT.clear()
        _NOT_BEFORE.clear()
        self.config = {'provider_id': 'p1', 'model_config_id': 'm1', 'client_type': 'openai-chat',
                       'model': 'test', 'api_key': 'test-secret', 'base_url': 'https://example.org/v1', 'max_retries': 2}

    def service(self, handler, config=None):
        return ProviderService({'mode': 'live', 'llm': config or self.config},
            client=httpx.Client(transport=httpx.MockTransport(handler)), sleep=lambda _: None)

    def test_three_protocols_use_real_mmsp_and_guarded_transport(self):
        for protocol, wire, path in [('openai-chat', chat_reply(), '/v1/chat/completions'),
                                      ('openai-responses', responses_reply(), '/v1/responses'),
                                      ('ant-messages', anthropic_reply(), '/v1/messages')]:
            seen = []
            def handler(request):
                seen.append(request)
                return httpx.Response(200, headers={'content-type': 'text/event-stream', 'x-request-id': 'req1'}, content=wire)
            with self.subTest(protocol=protocol):
                service = self.service(handler, {**self.config, 'client_type': protocol,
                    'base_url': 'https://example.org' if protocol == 'ant-messages' else self.config['base_url']})
                result = service.test_connection('llm')
                self.assertTrue(result['ok'], result)
                self.assertEqual(1, len(seen))
                self.assertEqual(path, seen[0].url.path)
                self.assertEqual('req1', result['calls'][0]['request_id'])
                self.assertEqual(10, result['calls'][0]['usage_metadata']['prompt_tokens'])
                self.assertNotIn('test-secret', json.dumps(result))
                body = json.loads(seen[0].content)
                if protocol == 'openai-chat': self.assertEqual({'type': 'json_object'}, body['response_format'])
                elif protocol == 'openai-responses': self.assertEqual({'type': 'json_object'}, body['text']['format'])
                else: self.assertEqual({'type': 'tool', 'name': 'submit_audit'}, body['tool_choice'])

    def test_gzip_stream_and_error_use_decoded_bytes_once(self):
        import gzip
        for status, content in [(200, chat_reply()), (401, '{"error":"denied"}')]:
            with self.subTest(status=status):
                result = self.service(lambda req: httpx.Response(status, headers={
                    'content-type':'text/event-stream' if status == 200 else 'application/json',
                    'content-encoding':'gzip'}, content=gzip.compress(content.encode()))).test_connection('llm')
                self.assertEqual(status == 200, result['ok'])
                self.assertEqual(status, result['calls'][0]['http_status'])

    def test_anthropic_cache_write_is_preserved_on_failed_finish(self):
        wire = anthropic_reply().replace('"cache_creation_input_tokens": 0', '"cache_creation_input_tokens": 7').replace('"stop_reason": "tool_use"', '"stop_reason": "max_tokens"')
        result = self.service(lambda req: httpx.Response(200, headers={'content-type':'text/event-stream'}, content=wire),
            {**self.config,'client_type':'ant-messages','base_url':'https://example.org'}).test_connection('llm')
        self.assertFalse(result['ok'])
        self.assertEqual(7, result['calls'][0]['usage_metadata']['cache_write_tokens'])
        self.assertEqual('unknown', result['calls'][0]['cost']['status'])

    def test_application_retries_once_per_http_attempt_without_sdk_retry(self):
        requests = []
        def handler(request):
            requests.append(request)
            return httpx.Response(500, json={'error': {'message': 'secret error'}})
        service = self.service(handler)
        result = service.test_connection('llm')
        self.assertFalse(result['ok'])
        self.assertEqual(3, len(requests))
        self.assertEqual([1, 2, 3], [call['attempt'] for call in result['calls']])
        self.assertTrue(all(call['cost']['status'] == 'unknown' for call in result['calls']))

    def test_nonretryable_auth_and_long_retry_after_do_not_repeat(self):
        for status, headers in [(401, {}), (429, {'retry-after': '120'})]:
            requests = []
            def handler(request):
                requests.append(request)
                return httpx.Response(status, headers=headers, json={'error': 'test-secret'})
            with self.subTest(status=status):
                result = self.service(handler).test_connection('llm')
                self.assertFalse(result['ok'])
                self.assertEqual(1, len(requests))
                self.assertNotIn('test-secret', json.dumps(result))

    def test_http_date_cooldown_blocks_same_provider_not_other_provider(self):
        from datetime import datetime, timedelta, timezone
        from email.utils import format_datetime
        seen = []
        retry_at = format_datetime(datetime.now(timezone.utc) + timedelta(seconds=120), usegmt=True)
        def handler(request):
            seen.append(request)
            return httpx.Response(429, headers={'retry-after':retry_at}, json={})
        self.service(handler).test_connection('llm')
        blocked = self.service(handler).test_connection('llm')
        self.assertEqual(1, len(seen))
        self.assertFalse(blocked['calls'][0]['external_call'])
        self.service(handler, {**self.config,'provider_id':'other'}).test_connection('llm')
        self.assertEqual(2, len(seen))

    def test_budget_blocks_retry_before_second_request(self):
        requests = []
        service = self.service(lambda request: requests.append(request) or httpx.Response(500, json={}))
        service._budget = lambda: not requests
        with self.assertRaises(Exception): service.llm_suggestions({}, [])
        self.assertEqual(1, len(requests))
        self.assertEqual(False, service.calls[-1]['external_call'])

    def test_missing_usage_and_truncated_output_remain_logged(self):
        for wire in [chat_reply(usage=False), chat_reply(finish='length')]:
            with self.subTest(wire=wire):
                result = self.service(lambda req: httpx.Response(200, headers={'content-type':'text/event-stream'}, content=wire)).test_connection('llm')
                self.assertFalse(result['ok'])
                self.assertEqual('invalid_model_output', result['status'])
                self.assertIn('response', result['calls'][0])
                self.assertNotIn('raw_response', result['calls'][0])
                self.assertEqual(1, len(result['calls']))

    def test_redacts_all_provider_keys_from_outputs_before_hook(self):
        stored = []
        settings = {'mode':'live','llm':self.config, 'llm_providers':[{'api_key':'other-provider-secret'}]}
        service = ProviderService(settings, call_hook=stored.append, client=httpx.Client(transport=httpx.MockTransport(
            lambda req: httpx.Response(200, headers={'content-type':'text/event-stream'}, content=chat_reply('{"suggestions": [], "leak":"other-provider-secret test-secret"}')))),sleep=lambda _:None)
        service.test_connection('llm')
        self.assertNotIn('other-provider-secret', json.dumps(stored))
        self.assertNotIn('test-secret', json.dumps(stored))
        self.assertEqual('rejected', stored[0]['validation']['status'])

    def test_deeply_encoded_secrets_and_object_keys_are_sanitized(self):
        secret = 'review-key"with\\escape'
        encoded = {secret: secret}
        for _ in range(6): encoded = json.dumps(encoded)
        service = self.service(lambda req: httpx.Response(200), {**self.config,'api_key':secret})
        service._event({'response':encoded})
        decoded = service.calls[0]['response']
        while isinstance(decoded, str):
            try: decoded = json.loads(decoded)
            except ValueError: break
        self.assertNotIn(secret, str(decoded))

    def test_dns_security_rejection_is_not_a_remote_billable_attempt(self):
        from app.security import PublicNetworkTransport
        seen = []
        service = ProviderService({'mode':'live','llm':self.config}, sleep=lambda _:None,
            client=httpx.Client(transport=PublicNetworkTransport(httpx.MockTransport(lambda req: seen.append(req) or httpx.Response(200)))))
        with patch('socket.getaddrinfo', return_value=[(2, 1, 6, '', ('127.0.0.1', 443))]):
            result = service.test_connection('llm')
        self.assertEqual([], seen)
        self.assertEqual('unsafe_url', result['status'])
        self.assertFalse(result['calls'][0]['external_call'])
        self.assertEqual('not_applicable', result['calls'][0]['cost']['status'])

    def test_model_post_redirect_is_not_followed(self):
        seen = []
        service = self.service(lambda req: seen.append(req) or httpx.Response(307, headers={'location':'https://other.org'}))
        result = service.test_connection('llm')
        self.assertEqual('unsafe_url', result['status'])
        self.assertEqual(1, len(seen))
        self.assertTrue(result['calls'][0]['external_call'])

    def test_explicit_credentials_override_environment(self):
        seen = []
        with patch.dict(os.environ, {'OPENAI_API_KEY':'ambient-openai', 'ANTHROPIC_API_KEY':'ambient-ant', 'ANTHROPIC_AUTH_TOKEN':'ambient-token', 'OPENAI_BASE_URL':'https://wrong.org'}):
            for protocol, wire in [('openai-chat', chat_reply()), ('ant-messages', anthropic_reply())]:
                self.service(lambda req: seen.append(req) or httpx.Response(200, headers={'content-type':'text/event-stream'}, content=wire), {**self.config,'client_type':protocol}).test_connection('llm')
        self.assertEqual(2,len(seen))
        for req in seen:
            self.assertEqual('example.org', req.url.host)
            self.assertNotIn('ambient',str(req.headers))
            self.assertIn('test-secret',req.headers.get('authorization',''))

    def test_escaped_key_echo_is_not_persisted_as_raw_wire(self):
        secret = 'review-key"with\\escape'
        echoed = json.dumps({'suggestions': [], 'secret': secret})
        captured = []
        config = {**self.config, 'api_key': secret}
        service = self.service(lambda request: httpx.Response(200, headers={'content-type':'text/event-stream'}, content=chat_reply(echoed)), config)
        service.call_hook = captured.append
        service.test_connection('llm')
        call = captured[0]
        self.assertNotIn('raw_response', call)
        for item in call['response']:
            if item['type'] == 'text.done':
                self.assertNotEqual(secret, json.loads(item['text']).get('secret'))
        self.assertNotIn(secret, json.dumps(call))

    def test_catalog_pricing_uses_endpoint_and_records_snapshot(self):
        model = {'model':'test','base_url':'https://example.org/v1','client':'openai-chat',
                 'pricing':{'currency':'USD','cached_tokens':1,'prompt_tokens':2,'thoughts_tokens':3,'response_tokens':4}}
        usage = dict(zip(('cached_tokens','prompt_tokens','thoughts_tokens','response_tokens'), (10,20,30,40)))
        with patch('app.llm.catalog',return_value={'models':[model]}):
            cost=estimate_cost(self.config,usage)
            self.assertEqual('estimated',cost['status'])
            self.assertAlmostEqual(.0003,cost['amount'])
            self.assertEqual(model['pricing'],cost['pricing'])
            self.assertEqual('unknown',estimate_cost({**self.config,'base_url':'https://different.org'},usage)['status'])
            self.assertEqual('unknown',estimate_cost(self.config,{**usage,'cached_tokens':None})['status'])
            self.assertEqual('unknown',estimate_cost(self.config,None)['status'])
