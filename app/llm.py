"""MMSP protocol adapters with application-owned transport, retries and accounting.

MMSP 0.5.6 does not expose SDK transport/retry or JSON-output injection. These
small pinned subclasses supply those constructor boundaries while inheriting
MMSP's message conversion and stream normalization. No SDK is monkeypatched.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal, InvalidOperation
from importlib.metadata import version
import json
import math
from urllib.parse import urlsplit

import httpx

from .security import SecurityError, safe_request, validate_base_url

CLIENT_TYPES = ('openai-chat', 'openai-responses', 'ant-messages')
PRICE_CLIENTS = {
    'openai-chat': {'openai-chat', 'openai-official', 'deepseek-official', 'zai-official', 'moonshot-official', 'minimax-official'},
    'openai-responses': {'openai-responses', 'openai-official'},
    'ant-messages': {'ant-messages', 'anthropic-official'},
}
TOKEN_BUCKETS = ('cached_tokens', 'prompt_tokens', 'thoughts_tokens', 'response_tokens')


def catalog():
    from mmsp import list_supported_models
    models = list_supported_models(currency='USD')
    for item in models:
        item['supported_client_types'] = [name for name, clients in PRICE_CLIENTS.items() if item['client'] in clients]
    return {'models': models, 'currency': 'USD'}


def estimate_cost(config, usage):
    """Record list-price estimates, never invent zero for an unknown price/usage."""
    result = {'status': 'unknown', 'amount': None, 'currency': 'USD', 'pricing': None,
              'source': 'mmsp', 'catalog_version': version('mmsp')}
    endpoint = config.get('base_url', '').rstrip('/')
    matches = [item for item in catalog()['models']
               if item['model'] == config.get('model') and item['base_url'].rstrip('/') == endpoint
               and item['client'] in PRICE_CLIENTS.get(config.get('client_type', 'openai-chat'), set())]
    prices = [item.get('pricing') for item in matches if item.get('pricing')]
    if not prices or any(price != prices[0] for price in prices):
        return result
    result['pricing'] = prices[0]
    if not isinstance(usage, dict):
        return result
    if usage.get('cache_write_tokens', 0):
        result['reason'] = 'Cache-write pricing is not represented in the MMSP catalog'
        return result
    total = Decimal(0)
    for bucket in TOKEN_BUCKETS:
        count = usage.get(bucket)
        if bucket == 'thoughts_tokens' and count is None and prices[0].get('thoughts_tokens') == prices[0].get('response_tokens'):
            # MMSP leaves all output tokens in response_tokens when the source
            # does not split thinking. Equal rates make that split irrelevant.
            count = 0
        if isinstance(count, bool) or not isinstance(count, (int, float)) or not math.isfinite(count) or count < 0:
            return result
        if not count:
            continue
        rate = prices[0].get(bucket)
        if isinstance(rate, bool) or not isinstance(rate, (int, float)) or not math.isfinite(rate) or rate < 0:
            return result
        try:
            total += Decimal(str(count)) * Decimal(str(rate)) / Decimal(1_000_000)
        except InvalidOperation:
            return result
    result.update(status='estimated', amount=float(total))
    return result


class LLMCallError(Exception):
    def __init__(self, kind, details, status=None, retry_after=None):
        super().__init__(kind)
        self.kind, self.details, self.status, self.retry_after = kind, details, status, retry_after


async def _client(config, transport):
    # The pinned SDKs use httpx2; citation sources still use httpx 0.28. The
    # bridge buffers at the existing guarded boundary rather than opening a
    # second unvalidated network path or detaching a thread on cancellation.
    import httpx2
    from openai import AsyncOpenAI
    from anthropic import AsyncAnthropic
    from mmsp.openai_chat import OpenaiChatClient
    from mmsp.openai_responses import OpenaiResponsesClient
    from mmsp.ant_messages import AntMessagesClient

    common = dict(api_key=config['api_key'], base_url=config['base_url'], max_retries=0,
                  timeout=config.get('timeout', 30), http_client=httpx2.AsyncClient(
                      transport=transport, trust_env=False, follow_redirects=False))

    class Chat(OpenaiChatClient):
        def __init__(self):
            self._model, self._history = config['model'], []
            self._client = AsyncOpenAI(**common, organization='', project='')

        def transform_uni_config_to_model_config(self, value):
            result = super().transform_uni_config_to_model_config(value)
            result['response_format'] = {'type': 'json_object'}
            return result

    class Responses(OpenaiResponsesClient):
        def __init__(self):
            self._model, self._history = config['model'], []
            self._client = AsyncOpenAI(**common, organization='', project='')

        def transform_uni_config_to_model_config(self, value):
            result = super().transform_uni_config_to_model_config(value)
            result['text'] = {'format': {'type': 'json_object'}}
            return result

    class ExplicitAnthropic(AsyncAnthropic):
        @property
        def auth_headers(self):
            return {'x-api-key': config['api_key'], 'Authorization': 'Bearer ' + config['api_key']}

    class Messages(AntMessagesClient):
        def __init__(self):
            self._model, self._history = config['model'], []
            self._client = ExplicitAnthropic(**common)

        def transform_uni_config_to_model_config(self, value):
            result = super().transform_uni_config_to_model_config(value)
            # A forced, inert output tool supplies structured JSON. This tool is
            # never executed and the caller still validates every evidence ID.
            result['tools'] = [{'name': 'submit_audit', 'description': 'Return grounded bibliographic suggestions.',
                                'input_schema': {'type': 'object', 'properties': {'suggestions': {'type': 'array', 'items': {'type': 'object'}}},
                                                 'required': ['suggestions'], 'additionalProperties': False}}]
            result['tool_choice'] = {'type': 'tool', 'name': 'submit_audit'}
            return result

    kind = config.get('client_type', 'openai-chat')
    if kind not in CLIENT_TYPES:
        raise ValueError('Unsupported MMSP protocol')
    try:
        return {'openai-chat': Chat, 'openai-responses': Responses, 'ant-messages': Messages}[kind]()
    except Exception:
        await common['http_client'].aclose()
        raise


def invoke(config, messages, client):
    """Run one stateless MMSP attempt. The caller owns retries and budgets."""
    import httpx2
    details = {'request': None, 'response': [], 'usage_metadata': None, 'finish_reason': None,
               'http_status': None, 'request_id': None, 'external_call': False}
    try:
        base = validate_base_url(config['base_url'], resolve=False)
    except SecurityError:
        raise LLMCallError('unsafe_url', details) from None
    transport_failure = []
    raw_wire = []
    headers = {}

    class GuardedTransport(httpx2.AsyncBaseTransport):
        async def handle_async_request(self, request):
            # Synchronous by design: the application worker retains ownership
            # until this bounded network operation ends; no orphaned retries.
            if request.method != 'POST' or urlsplit(str(request.url)).netloc != urlsplit(base).netloc:
                raise SecurityError('Model request changed the configured origin or method')
            content = await request.aread()
            try:
                details['request'] = json.loads(content)
                details['external_call'] = True
                response = safe_request(client, request.method, str(request.url),
                                        headers=dict(request.headers), content=content,
                                        timeout=max(1, min(float(config.get('timeout', 30)), 120)))
                details['http_status'] = response.status_code
                details['request_id'] = response.headers.get('x-request-id') or response.headers.get('request-id')
                headers.update(response.headers)
                if response.status_code >= 400:
                    try:
                        details['error_response'] = response.json()
                    except ValueError:
                        details['error_response'] = {'format': 'non-json', 'body_omitted': True}
                # Wire bytes stay transient; only normalized, sanitized output is audited.
                raw_wire.append(response.text)
                return httpx2.Response(response.status_code, headers=dict(response.headers),
                                       content=response.content, request=request)
            except Exception as error:
                if isinstance(error, SecurityError) and getattr(error, 'request_sent', None) is False:
                    details['external_call'] = False
                transport_failure.append(error)
                raise

    async def run():
        model = await _client({**config, 'base_url': base}, GuardedTransport())
        unified = [{'role': item['role'], 'content_items': [{'type': 'text.done', 'text': item['content']}]} for item in messages]
        options = {'max_tokens': int(config.get('max_output_tokens', 2000)),
                   'temperature': float(config.get('temperature', 0))}
        try:
            async for event in model.streaming_response(unified, options):
                if event['event_type'] == 'stop':
                    details['usage_metadata'] = event.get('usage_metadata')
                    details['finish_reason'] = event.get('finish_reason')
                for item in event.get('content_items', []):
                    if item['type'] == 'text.delta':
                        details['partial_text'] = details.get('partial_text', '') + item.get('text', '')
                    if item['type'] == 'tool_call.delta':
                        key = item.get('fidelity', {}).get('item_id') or item.get('tool_call_id') or 'output'
                        partial = details.setdefault('partial_tools', {}).setdefault(key, {'name': '', 'arguments': ''})
                        partial['name'] += item.get('name', '')
                        partial['arguments'] += item.get('arguments', '')
                    if item['type'].endswith('.done'):
                        details['response'].append(item)
        except Exception as error:
            if getattr(error, 'usage_metadata', None) is not None:
                details['usage_metadata'] = error.usage_metadata
            if getattr(error, 'finish_reason', None) is not None:
                details['finish_reason'] = error.finish_reason
            raise
        finally:
            # MMSP folds Anthropic cache writes into ordinary input. Preserve this
            # distinct usage so unrepresentable cache-write tariffs stay unknown.
            if config.get('client_type') == 'ant-messages' and isinstance(details.get('usage_metadata'), dict):
                writes = []
                for line in ''.join(raw_wire).splitlines():
                    if not line.startswith('data: '): continue
                    try: event = json.loads(line[6:])
                    except ValueError: continue
                    usage = event.get('usage') or event.get('message', {}).get('usage') or {}
                    if usage.get('cache_creation_input_tokens') is not None:
                        writes.append(usage['cache_creation_input_tokens'])
                if writes and any(isinstance(value, (int, float)) and value > 0 for value in writes):
                    details['usage_metadata']['cache_write_tokens'] = max(writes)
            await model._client.close()

    try:
        asyncio.run(run())
        items = details['response']
        tools = [item for item in items if item['type'] == 'tool_call.done']
        if config.get('client_type') == 'ant-messages':
            if len(tools) != 1 or tools[0].get('name') != 'submit_audit':
                raise ValueError('Structured output tool missing')
            details['content'] = tools[0]['arguments']
        else:
            details['content'] = ''.join(item['text'] for item in items if item['type'] == 'text.done')
        if details.get('finish_reason') not in ('stop', 'tool_call'):
            raise ValueError('Model response was not completed')
        details.pop('partial_text', None)
        details.pop('partial_tools', None)
        return details
    except Exception as error:
        status = details['http_status']
        cause = transport_failure[-1] if transport_failure else error
        if isinstance(cause, SecurityError): kind = 'unsafe_url'
        elif isinstance(cause, httpx.TimeoutException): kind = 'timeout'
        elif isinstance(cause, httpx.HTTPError): kind = 'network_error'
        elif status == 429: kind = 'rate_limited'
        elif status is not None and status >= 500: kind = 'server_error'
        elif status == 401: kind = 'authentication'
        elif status == 403: kind = 'permission_denied'
        elif status is not None and status >= 400: kind = 'http_error'
        else: kind = 'invalid_model_output'
        details['error_type'] = type(error).__name__
        raise LLMCallError(kind, details, status, headers.get('retry-after')) from None
