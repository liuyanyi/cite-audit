"""Local single-user configuration; secrets never cross the read API."""
import re
from copy import deepcopy

from .policy import (
    AUDIT_POLICY,
    CREDENTIAL_PROVIDERS,
    MODEL_DEFAULTS,
    MODEL_LIMITS,
    MODEL_WRITABLE,
    NEGATIVE_TTL_SECONDS,
    PROVIDERS,
    SETTING_GROUPS,
    SETTING_LIMITS,
    SOURCE_NAMES,
    public_limits,
)
from .storage import Store, dump, load

DEFAULTS = {
    "source_enabled": {p: True for p in PROVIDERS},
    "request_timeout_seconds": 12, "retries": 1, "max_source_requests": 18,
    "max_task_requests": 180, "source_cache_ttl_seconds": 3600,
    "source_max_results": 3, "knowledge_ttl_seconds": 86400,
    "max_upload_bytes": 5_000_000, "max_entries": 2000,
    "credentials": {},
    "model": MODEL_DEFAULTS,
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

    def get(self):
        config = deepcopy(DEFAULTS)
        saved = load(self.store.one("SELECT data FROM settings WHERE id=1")["data"])
        config.update({key: value for key, value in saved.items() if key in DEFAULTS})
        config["source_enabled"] = {**DEFAULTS["source_enabled"], **config["source_enabled"]}
        return config

    def public(self):
        raw = self.get()
        model = dict(raw["model"])
        model["has_api_key"] = bool(model.pop("api_key"))
        from .model import price_snapshot
        model["price"] = price_snapshot(raw["model"])
        groups = {group: {key: raw[internal] for key, internal in fields.items()}
                  for group, fields in SETTING_GROUPS.items()}
        groups["runtime"]["concurrency"] = 1
        groups["cache"]["negative_ttl_seconds"] = NEGATIVE_TTL_SECONDS
        return {
            "sources": [{"id": p, "name": name, "enabled": raw["source_enabled"].get(p, False),
                         "implemented": True, "requires_key": False, "supports_api_key": p in CREDENTIAL_PROVIDERS,
                         "has_api_key": bool(raw["credentials"].get(p)), "status": "not_tested",
                         "capabilities": ["lookup", "search"]} for p, name in SOURCE_NAMES.items()],
            "model": model, **groups, "audit": dict(AUDIT_POLICY), "limits": public_limits(),
        }

    def update_public(self, body):
        if set(body) - {"sources", "model", "runtime", "cache", "audit"}:
            raise ValueError("Unknown settings group")
        mapped = {}
        if "sources" in body:
            if not isinstance(body["sources"], list):
                raise ValueError("sources must be a list")
            mapped["source_enabled"], mapped["credentials"] = {}, {}
            for item in body["sources"]:
                if not isinstance(item, dict) or item.get("id") not in PROVIDERS:
                    raise ValueError("Unknown source provider")
                if "enabled" in item:
                    mapped["source_enabled"][item["id"]] = item["enabled"]
                if "api_key" in item:
                    mapped["credentials"][item["id"]] = item["api_key"]
        if "model" in body:
            if not isinstance(body["model"], dict):
                raise ValueError("model must be an object")
            mapped["model"] = {k: v for k, v in body["model"].items() if k in MODEL_WRITABLE}
        fixed = {"runtime": {"concurrency": 1}, "cache": {"negative_ttl_seconds": NEGATIVE_TTL_SECONDS}}
        for group, translations in SETTING_GROUPS.items():
            values = body.get(group, {})
            if not isinstance(values, dict):
                raise ValueError(f"{group} must be an object")  # noqa: TRY004 - HTTP settings boundary
            for key, value in values.items():
                if key in translations:
                    mapped[translations[key]] = value
                elif key not in fixed[group] or type(value) is not int or value != fixed[group][key]:
                    raise ValueError(f"Unsupported configurable setting: {group}.{key}")
        if "audit" in body and body["audit"] != AUDIT_POLICY:
            raise ValueError("Audit logging and secret redaction cannot be disabled")
        self.update(mapped)
        return self.public()

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
        for key, (lower, upper) in SETTING_LIMITS.items():
            value = config.get(key)
            if (type(value) not in (int, float) or not lower <= value <= upper
                    or (key != "request_timeout_seconds" and int(value) != value)):
                raise ValueError(f"{key} must be between {lower} and {upper}")
        if any(k not in PROVIDERS or type(v) is not bool for k, v in config["source_enabled"].items()):
            raise ValueError("source_enabled requires supported provider boolean values")
        if any(k not in CREDENTIAL_PROVIDERS or not isinstance(v, str)
               or len(v) > 4096 or any(ord(c) < 32 for c in v)
               for k, v in config["credentials"].items()):
            raise ValueError("Unsupported source or invalid credential")
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
        for key, (low, high) in MODEL_LIMITS.items():
            value = model.get(key)
            if type(value) not in (int, float) or not low <= value <= high:
                raise ValueError(f"Invalid model {key}")
            if key != "timeout_seconds" and int(value) != value:
                raise ValueError(f"model {key} must be an integer")
        self.store.execute("UPDATE settings SET data=? WHERE id=1", (dump(config),))
