"""Backend-only configuration. Saved secrets are local plaintext with mode 0600."""
from __future__ import annotations
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import threading
from urllib.parse import urlsplit

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
RULE_VERSION = '2026-10-24.1'

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

class Config:
    def __init__(self, directory: Path):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.directory / 'settings.json'
        self.lock = threading.RLock()
        self.data = copy.deepcopy(DEFAULTS)
        if self.path.exists():
            saved = json.loads(self.path.read_text(encoding='utf-8'))
            for key, val in saved.items():
                if key in self.data:
                    if isinstance(val, dict): self.data[key].update(val)
                    else: self.data[key] = val

    def get(self):
        with self.lock:
            data = copy.deepcopy(self.data)
            for env, (group, key) in ENV_KEYS.items():
                if os.getenv(env): data[group][key] = os.environ[env]
            if os.getenv('CITE_AUDIT_MODE') in ('mock', 'live'): data['mode'] = os.environ['CITE_AUDIT_MODE']
            return data

    def public(self):
        data = self.get()
        for group in ('llm', 'deepseek', 'semantic_scholar'):
            data[group]['api_key_configured'] = bool(data[group].pop('api_key', ''))
            data[group]['api_key_from_env'] = any(os.getenv(env) for env, target in ENV_KEYS.items() if target == (group, 'api_key'))
        return data

    def update(self, changes):
        if not isinstance(changes, dict): raise ValueError('Settings must be an object')
        with self.lock:
            candidate = copy.deepcopy(self.data)
            for group, value in changes.items():
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
                        if key == 'base_url':
                            url = urlsplit(val)
                            if url.scheme != 'https' or not url.hostname or url.username or url.password or url.query or url.fragment:
                                raise ValueError('Model endpoint must be a public HTTPS URL without credentials, query or fragment')
                            # Full public-address validation occurs before every outbound request.
                            if url.hostname.lower() in ('localhost', 'localhost.localdomain') or url.hostname.endswith('.local'):
                                raise ValueError('Local model endpoints are disabled for SSRF protection')
                        candidate[group][key] = val.strip()
                    else:
                        if isinstance(val, bool) or not isinstance(val, (int, float)): raise ValueError('Numeric setting required')
                        low, high = (1, 5000)
                        if key == 'timeout': low, high = 1, 120
                        elif key == 'rate_per_second': low, high = .05, 10
                        elif key == 'max_output_tokens': low, high = 128, 8000
                        elif key.endswith('_days'): low, high = 1, 365
                        elif key == 'per_entry': low, high = 1, 100
                        if not low <= val <= high: raise ValueError(f'{key} must be between {low} and {high}')
                        candidate[group][key] = val
            temp = self.path.with_suffix('.tmp')
            descriptor = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
                json.dump(candidate, handle, ensure_ascii=False, indent=2)
            os.replace(temp, self.path)
            os.chmod(self.path, 0o600)
            self.data = candidate
        return self.public()

    def fingerprint(self, mode=None):
        data = self.get()
        data.pop('budget', None)
        if mode: data['mode'] = mode
        # Secret hashes invalidate prior authentication outcomes without storing secrets in task rows.
        for group in ('llm', 'deepseek', 'semantic_scholar'):
            data[group]['api_key'] = hashlib.sha256(data[group].get('api_key', '').encode()).hexdigest()
        return hashlib.sha256(json.dumps([RULE_VERSION, data], sort_keys=True).encode()).hexdigest()

    def redact(self, value):
        from .security import redact
        settings = self.get()
        secrets = tuple(settings[group].get('api_key', '') for group in ('llm', 'deepseek', 'semantic_scholar'))
        def scrub(item):
            cleaned = redact(item, secrets)
            if isinstance(cleaned, dict):
                return {redact(str(key), secrets): scrub(val) for key, val in cleaned.items()}
            if isinstance(cleaned, list): return [scrub(val) for val in cleaned]
            return cleaned
        return scrub(value)
