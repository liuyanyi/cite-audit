"""Local single-user configuration; secrets never cross the read API."""
import re
from copy import deepcopy

from .storage import Store, dump, load

PROVIDERS = ("crossref", "arxiv", "openalex", "semantic_scholar", "acl", "pmlr")
DEFAULTS = {
    "default_mode": "mock", "source_enabled": {p: True for p in PROVIDERS},
    "request_timeout_seconds": 12, "retries": 1, "max_source_requests": 18,
    "max_task_requests": 180, "source_cache_ttl_seconds": 3600,
    "source_max_results": 3, "knowledge_ttl_seconds": 86400,
    "max_upload_bytes": 5_000_000, "max_entries": 2000,
    "credentials": {},
    "model": {"enabled": False, "library": "mmsp", "protocol": "openai-chat", "model": "", "base_url": "",
              "api_key": "", "max_retries": 1, "timeout_seconds": 30, "max_output_tokens": 1024,
              "price_source": "mmsp_registry"},
}
SECRET_NAMES = {"api_key", "key", "token", "authorization", "password", "secret", "credentials"}


def redact(value, secrets=()):
    if isinstance(value, dict):
        return {k: ("[redacted]" if k.casefold() in SECRET_NAMES else redact(v, secrets))
                for k, v in value.items()}
    if isinstance(value, list):
        return [redact(x, secrets) for x in value]
    if isinstance(value, str):
        result = value
        for secret in secrets:
            if secret and len(str(secret)) >= 4:
                result = result.replace(str(secret), "[redacted]")
        result = re.sub(r"(?i)(api[_-]?key|token|authorization)([=:]\s*)[^&\s,]+", r"\1\2[redacted]", result)
        result = re.sub(r"(?i)bearer\s+[^\s]+", "Bearer [redacted]", result)
        return result
    return value


def secrets_from(config):
    found = []
    def walk(value, key=""):
        if isinstance(value, dict):
            for k, v in value.items():
                walk(v, k)
        elif isinstance(value, str) and (key.casefold() in SECRET_NAMES or key in PROVIDERS):
            found.append(value)
    walk(config)
    return found


class Settings:
    def __init__(self, store: Store):
        self.store = store
        with store.connect() as db:
            db.execute("INSERT OR IGNORE INTO settings VALUES(1,?)", (dump(DEFAULTS),))

    def get(self, public=False):
        config = deepcopy(DEFAULTS)
        saved = load(self.store.one("SELECT data FROM settings WHERE id=1")["data"])
        config.update(saved)
        if public:
            config = deepcopy(config)
            credentials = config.pop("credentials", {})
            config["credentials_configured"] = {k: bool(v) for k, v in credentials.items()}
            model = config["model"]
            model["api_key_configured"] = bool(model.pop("api_key", ""))
            config["providers"] = [{"id": p, "name": p.replace("_", " ").title(),
                "enabled": config["source_enabled"].get(p, False),
                "implemented": True, "status": "not_tested",
                "credential_configured": bool(credentials.get(p)),
                "requires_key": False, "supports_api_key": p in {"openalex", "semantic_scholar"}} for p in PROVIDERS]
            config["single_user_local_only"] = True
        return config

    def update(self, incoming: dict):
        allowed = set(DEFAULTS)
        unknown = set(incoming) - allowed
        if unknown:
            raise ValueError("Unknown settings: " + ", ".join(sorted(unknown)))
        config = self.get()
        for key, value in incoming.items():
            if key in {"source_enabled", "credentials", "model"}:
                if not isinstance(value, dict):
                    raise ValueError(f"{key} must be an object")
                config[key].update(value)
            else:
                config[key] = value
        for key, lower, upper in [
            ("request_timeout_seconds", 1, 60), ("retries", 0, 2), ("max_source_requests", 1, 40),
            ("max_task_requests", 1, 5000), ("source_cache_ttl_seconds", 0, 604800),
            ("source_max_results", 1, 5), ("knowledge_ttl_seconds", 0, 2592000),
            ("max_upload_bytes", 1024, 20_000_000), ("max_entries", 1, 10000)]:
            value = config.get(key)
            if (type(value) not in (int, float) or not lower <= value <= upper
                    or (key != "request_timeout_seconds" and int(value) != value)):
                raise ValueError(f"{key} must be between {lower} and {upper}")
        if config["default_mode"] not in {"mock", "online"}:
            raise ValueError("default_mode must be mock or online")
        if any(k not in PROVIDERS or type(v) is not bool for k, v in config["source_enabled"].items()):
            raise ValueError("source_enabled requires supported provider boolean values")
        if any(k not in {"openalex", "semantic_scholar"} or not isinstance(v, str)
               or len(v) > 4096 or any(ord(c) < 32 for c in v)
               for k, v in config["credentials"].items()):
            raise ValueError("Only OpenAlex and Semantic Scholar support configured API keys")
        model = config["model"]
        if set(model) - set(DEFAULTS["model"]):
            raise ValueError("Unknown model setting")
        if type(model.get("enabled")) is not bool:
            raise ValueError("model.enabled must be a boolean")
        from .model import PROTOCOLS, validate_model_url
        if model.get("protocol") not in PROTOCOLS:
            raise ValueError("Unsupported model protocol")
        for key, limit in [("model", 200), ("api_key", 4096), ("base_url", 2048)]:
            value = model.get(key)
            if not isinstance(value, str) or len(value) > limit or any(ord(c) < 32 for c in value):
                raise ValueError(f"Invalid model {key}")
        if model["base_url"]:
            validate_model_url(model["base_url"])
        for key, low, high in [("timeout_seconds", 1, 120), ("max_retries", 0, 3),
                               ("max_output_tokens", 1, 8192)]:
            value = model.get(key)
            if type(value) not in (int, float) or not low <= value <= high:
                raise ValueError(f"Invalid model {key}")
            if key != "timeout_seconds" and int(value) != value:
                raise ValueError(f"model {key} must be an integer")
        self.store.execute("UPDATE settings SET data=? WHERE id=1", (dump(config),))
        return self.get(public=True)
