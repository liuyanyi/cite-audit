"""Backend-only configuration. Saved secrets are local plaintext with mode 0600."""
from __future__ import annotations
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import threading
from urllib.parse import urlsplit

from .audit import RULE_VERSION

DEFAULTS = {
    'mode': 'mock',
    'llm': {'base_url': 'https://api.openai.com/v1', 'api_key': '', 'model': '', 'timeout': 30, 'max_output_tokens': 2000},
    'deepseek': {'api_key': '', 'timeout': 30},
    'semantic_scholar': {'api_key': '', 'timeout': 20, 'rate_per_second': 1.0},
    'budget': {'per_entry': 20, 'per_task': 200},
    'cache': {'metadata_days': 180, 'search_days': 30, 'negative_days': 7},
}
ENV_KEYS = {
    'CITE_AUDIT_LLM_API_KEY': ('llm', 'api_key'), 'CITE_AUDIT_LLM_BASE_URL': ('llm', 'base_url'),
    'CITE_AUDIT_LLM_MODEL': ('llm', 'model'), 'DEEPSEEK_API_KEY': ('deepseek', 'api_key'),
    'SEMANTIC_SCHOLAR_API_KEY': ('semantic_scholar', 'api_key'),
}
MODEL_DEFAULTS = {'temperature': 0.0, 'timeout': 30, 'max_output_tokens': 2000, 'max_retries': 2}
CLIENT_TYPES = {'openai-chat', 'openai-responses', 'ant-messages'}


def load_env(path: Path) -> None:
    """Minimal literal .env reader; never evaluates shell or Python expressions."""
    if not path.exists():
        return
    for line in path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        if re.fullmatch(r'[A-Z][A-Z0-9_]*', key.strip()):
            os.environ.setdefault(key.strip(), value.strip().strip('\"\''))

def validate_endpoint(value):
    if not isinstance(value, str) or len(value) > 2048:
        raise ValueError('Invalid model endpoint')
    value = value.strip()
    url = urlsplit(value)
    if url.scheme != 'https' or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise ValueError('Model endpoint must be a public HTTPS URL without credentials, query or fragment')
    if url.hostname.lower() in ('localhost', 'localhost.localdomain') or url.hostname.endswith('.local'):
        raise ValueError('Local model endpoints are disabled for SSRF protection')
    return value


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', value):
        raise ValueError('IDs must contain 1–64 letters, numbers, underscores or hyphens')
    return value


def numeric(value, name, low, high, integer=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f'{name} must be a finite number')
    if not low <= value <= high or integer and int(value) != value:
        raise ValueError(f'{name} must be between {low} and {high}' + (' and an integer' if integer else ''))
    return int(value) if integer else value


def legacy_provider(llm):
    return {'id': 'legacy', 'name': 'Default provider', 'client_type': 'openai-chat',
            'base_url': llm['base_url'], 'api_key': llm.get('api_key', '')}


def legacy_model(llm):
    return {'id': 'legacy', 'name': 'Default model', 'provider_id': 'legacy',
            'model': llm.get('model', ''), **MODEL_DEFAULTS,
            'timeout': llm.get('timeout', 30), 'max_output_tokens': llm.get('max_output_tokens', 2000)}


class Config:
    def __init__(self, directory: Path):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.directory / 'settings.json'
        self.lock = threading.RLock()
        self.data = copy.deepcopy(DEFAULTS)
        saved = json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else {}
        for key, val in saved.items():
            if key in self.data:
                if isinstance(val, dict): self.data[key].update(val)
                else: self.data[key] = val
        # Keep the prior configuration as a named provider/model without losing its key.
        self.data['llm_providers'] = copy.deepcopy(saved.get('llm_providers', [legacy_provider(self.data['llm'])]))
        self.data['llm_models'] = copy.deepcopy(saved.get('llm_models', [legacy_model(self.data['llm'])]))
        self.data['default_model_id'] = saved.get('default_model_id', 'legacy')

    def get(self, model_id=None):
        with self.lock:
            data = copy.deepcopy(self.data)
            for env, (group, key) in ENV_KEYS.items():
                if os.getenv(env): data[group][key] = os.environ[env]
            if os.getenv('CITE_AUDIT_MODE') in ('mock', 'live'): data['mode'] = os.environ['CITE_AUDIT_MODE']
            has_legacy_env = any(os.getenv(env) for env, target in ENV_KEYS.items() if target[0] == 'llm')
            provider = next((p for p in data['llm_providers'] if p['id'] == 'legacy'), None)
            model = next((m for m in data['llm_models'] if m['id'] == 'legacy'), None)
            if has_legacy_env:
                if provider is None:
                    provider = legacy_provider(data['llm']); data['llm_providers'].append(provider)
                if model is None:
                    model = legacy_model(data['llm']); data['llm_models'].append(model)
                for env, (_, key) in ENV_KEYS.items():
                    if not env.startswith('CITE_AUDIT_LLM_') or not os.getenv(env): continue
                    if key in ('base_url', 'api_key'): provider[key] = data['llm'][key]
                    if key == 'model': model[key] = data['llm'][key]
            selected_id = data['default_model_id'] if model_id is None else model_id
            if selected_id:
                selected = next((item for item in data['llm_models'] if item['id'] == selected_id), None)
                if selected is None: raise ValueError('Selected model does not exist')
                selected_provider = next((item for item in data['llm_providers'] if item['id'] == selected['provider_id']), None)
                if selected_provider is None: raise ValueError('Selected model provider does not exist')
                data['llm'] = {**MODEL_DEFAULTS, **selected, **{key: selected_provider[key] for key in ('base_url', 'api_key', 'client_type')},
                               'provider_id': selected_provider['id'], 'provider_name': selected_provider['name'], 'model_config_id': selected['id']}
            else:
                data['llm'] = {**copy.deepcopy(DEFAULTS['llm']), **MODEL_DEFAULTS, 'provider_id': '', 'model_config_id': '', 'client_type': 'openai-chat'}
            return data

    def public(self):
        data = self.get()
        for provider in data['llm_providers']:
            provider['api_key_configured'] = bool(provider.pop('api_key', ''))
            provider['api_key_from_env'] = provider['id'] == 'legacy' and bool(os.getenv('CITE_AUDIT_LLM_API_KEY'))
        for group in ('llm', 'deepseek', 'semantic_scholar'):
            data[group]['api_key_configured'] = bool(data[group].pop('api_key', ''))
            data[group]['api_key_from_env'] = (data[group].get('provider_id') == 'legacy' and bool(os.getenv('CITE_AUDIT_LLM_API_KEY'))) if group == 'llm' else any(os.getenv(env) for env, target in ENV_KEYS.items() if target == (group, 'api_key'))
        return data

    @staticmethod
    def _providers(values, previous):
        if not isinstance(values, list) or len(values) > 100: raise ValueError('Providers must be a list of at most 100 items')
        result, seen = [], set()
        old = {item['id']: item for item in previous}
        allowed = {'id', 'name', 'client_type', 'base_url', 'api_key', 'api_key_configured', 'api_key_from_env'}
        for item in values:
            if not isinstance(item, dict) or set(item) - allowed: raise ValueError('Invalid provider fields')
            provider_id = identifier(item.get('id'))
            if provider_id in seen: raise ValueError('Duplicate provider ID')
            seen.add(provider_id)
            name = item.get('name', provider_id)
            if not isinstance(name, str) or not name.strip() or len(name) > 100: raise ValueError('Invalid provider name')
            client_type = item.get('client_type', 'openai-chat')
            if client_type not in CLIENT_TYPES: raise ValueError('Unsupported model protocol')
            key = item.get('api_key', '')
            if key is None: key = ''
            elif not isinstance(key, str) or len(key) > 4096: raise ValueError('Invalid key')
            elif not key.strip(): key = old.get(provider_id, {}).get('api_key', '')
            result.append({'id': provider_id, 'name': name.strip(), 'client_type': client_type,
                           'base_url': validate_endpoint(item.get('base_url')), 'api_key': key.strip()})
        return result

    @staticmethod
    def _models(values, providers):
        if not isinstance(values, list) or len(values) > 200: raise ValueError('Models must be a list of at most 200 items')
        result, seen = [], set()
        provider_ids = {item['id'] for item in providers}
        allowed = {'id', 'name', 'provider_id', 'model', *MODEL_DEFAULTS}
        for item in values:
            if not isinstance(item, dict) or set(item) - allowed: raise ValueError('Invalid model fields')
            model_id = identifier(item.get('id'))
            if model_id in seen: raise ValueError('Duplicate model ID')
            seen.add(model_id)
            if item.get('provider_id') not in provider_ids: raise ValueError('Model references an unknown provider')
            model = item.get('model', '')
            if not isinstance(model, str) or len(model) > 200 or (not model.strip() and model_id != 'legacy'): raise ValueError('Model ID must not be empty')
            name = item.get('name') or model or 'Default model'
            if not isinstance(name, str) or len(name) > 100: raise ValueError('Invalid model name')
            row = {**MODEL_DEFAULTS, **item, 'name': name.strip(), 'model': model.strip()}
            for key, low, high, integer in [('temperature', 0, 2, False), ('timeout', 1, 120, False), ('max_output_tokens', 128, 8000, True), ('max_retries', 0, 5, True)]:
                row[key] = numeric(row[key], key, low, high, integer)
            result.append(row)
        return result

    def update(self, changes):
        if not isinstance(changes, dict): raise ValueError('Settings must be an object')
        with self.lock:
            candidate = copy.deepcopy(self.data)
            for group, value in changes.items():
                if group in ('llm_providers', 'llm_models', 'default_model_id'): continue
                if group not in DEFAULTS: raise ValueError('Unknown settings group')
                if group == 'mode':
                    if value not in ('mock', 'live'): raise ValueError('Mode must be mock or live')
                    candidate[group] = value
                    continue
                if not isinstance(value, dict): raise ValueError('Settings group must be an object')
                for key, val in value.items():
                    if key not in DEFAULTS[group]: raise ValueError('Unknown setting')
                    if key == 'api_key':
                        if val is None: candidate[group][key] = ''
                        elif isinstance(val, str) and len(val) <= 4096:
                            if val.strip(): candidate[group][key] = val.strip()
                        else: raise ValueError('Invalid key')
                    elif key in ('base_url', 'model'):
                        if not isinstance(val, str) or len(val) > 2048: raise ValueError('Invalid configuration value')
                        candidate[group][key] = validate_endpoint(val) if key == 'base_url' else val.strip()
                    else:
                        low, high = (1, 5000)
                        if key == 'timeout': low, high = 1, 120
                        elif key == 'rate_per_second': low, high = .05, 10
                        elif key == 'max_output_tokens': low, high = 128, 8000
                        elif key.endswith('_days'): low, high = 1, 365
                        elif key == 'per_entry': low, high = 1, 100
                        candidate[group][key] = numeric(val, key, low, high)
            if 'llm' in changes:
                # Legacy clients and .env retain the original configuration slot.
                provider = next((p for p in candidate['llm_providers'] if p['id'] == 'legacy'), None)
                model = next((m for m in candidate['llm_models'] if m['id'] == 'legacy'), None)
                if provider is None: candidate['llm_providers'].append(legacy_provider(candidate['llm']))
                else: provider.update({k: candidate['llm'][k] for k in ('base_url', 'api_key')})
                if model is None: candidate['llm_models'].append(legacy_model(candidate['llm']))
                else: model.update({k: candidate['llm'][k] for k in ('model', 'timeout', 'max_output_tokens')})
                if not candidate['default_model_id']: candidate['default_model_id'] = 'legacy'
            if 'llm_providers' in changes: candidate['llm_providers'] = self._providers(changes['llm_providers'], candidate['llm_providers'])
            if 'llm_models' in changes: candidate['llm_models'] = self._models(changes['llm_models'], candidate['llm_providers'])
            else: candidate['llm_models'] = self._models(candidate['llm_models'], candidate['llm_providers'])
            if 'default_model_id' in changes:
                selected = changes['default_model_id']
                if not isinstance(selected, str): raise ValueError('Default model ID must be a string')
                candidate['default_model_id'] = selected
            if candidate['default_model_id'] and candidate['default_model_id'] not in {m['id'] for m in candidate['llm_models']}:
                raise ValueError('Default model must reference an existing model')
            # The compatibility slot must not retain a removed key and later
            # resurrect it when an older client updates only its model name.
            provider = next((p for p in candidate['llm_providers'] if p['id'] == 'legacy'), None)
            model = next((m for m in candidate['llm_models'] if m['id'] == 'legacy'), None)
            candidate['llm'] = copy.deepcopy(DEFAULTS['llm'])
            if provider:
                candidate['llm'].update({k: provider[k] for k in ('base_url', 'api_key')})
            if model:
                candidate['llm'].update({k: model[k] for k in ('model', 'timeout', 'max_output_tokens')})
            temp = self.path.with_suffix('.tmp')
            descriptor = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
                json.dump(candidate, handle, ensure_ascii=False, indent=2)
            os.replace(temp, self.path)
            os.chmod(self.path, 0o600)
            self.data = candidate
        return self.public()

    def fingerprint(self, mode=None, model_id=None):
        data = self.get(model_id)
        for key in ('budget', 'llm_providers', 'llm_models', 'default_model_id'): data.pop(key, None)
        if mode: data['mode'] = mode
        for group in ('llm', 'deepseek', 'semantic_scholar'):
            data[group]['api_key'] = hashlib.sha256(data[group].get('api_key', '').encode()).hexdigest()
        return hashlib.sha256(json.dumps([RULE_VERSION, data], sort_keys=True).encode()).hexdigest()

    def redact(self, value):
        from .security import redact
        settings = self.get()
        secrets = tuple(settings[group].get('api_key', '') for group in ('llm', 'deepseek', 'semantic_scholar')) + tuple(p.get('api_key', '') for p in settings['llm_providers']) + (self.data['llm'].get('api_key', ''), os.getenv('CITE_AUDIT_LLM_API_KEY', ''))
        def scrub(item):
            cleaned = redact(item, secrets)
            if isinstance(cleaned, dict):
                return {redact(str(key), secrets): scrub(val) for key, val in cleaned.items()}
            if isinstance(cleaned, list): return [scrub(val) for val in cleaned]
            return cleaned
        return scrub(value)
