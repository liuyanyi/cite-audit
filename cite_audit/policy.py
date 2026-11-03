"""Shared source metadata and configuration limits."""

import re

from .bib import MAX_BYTES, MAX_ENTRIES


def cnki_search_title(title: str) -> str:
    # BibTeX groups control capitalization; escaped braces are literal title characters.
    text = re.sub(r"\\([{}])|[{}]", lambda match: match.group(1) or "", title)
    return " ".join(text.split())[:1500]


def cnki_title_eligible(title: str) -> bool:
    """Accept titles containing Han characters, excluding visible kana or Hangul."""
    return bool(re.search(r"[㐀-䶿一-鿿\uf900-\ufaff\U00020000-\U0002FA1F\U00030000-\U000323AF]", title)
                and not re.search(r"[ぁ-ヿｦ-ﾟ가-힣ᄀ-ᇿㄱ-ㆎ]", title))


SOURCE_NAMES = {
    "hf_model": "DataCite / Hugging Face",
    "iclr": "ICLR Proceedings",
    "neurips": "NeurIPS Proceedings", "colm": "COLM",
    "openreview": "OpenReview",
    "tmlr": "TMLR",
    "cnki": "中国知网",
    "crossref": "Crossref", "arxiv": "arXiv", "openalex": "OpenAlex",
    "semantic_scholar": "Semantic Scholar", "acl": "ACL Anthology", "pmlr": "PMLR",
}
PROVIDERS = tuple(SOURCE_NAMES)
CREDENTIAL_PROVIDERS = frozenset({"openalex", "semantic_scholar", "openreview"})
NEGATIVE_TTL_SECONDS = 60
AUDIT_POLICY = {"enabled": True, "retain_prompt_text": False, "redact_secrets": True}
SETTING_LIMITS = {
    "request_timeout_seconds": (1, 60), "retries": (0, 2), "max_source_requests": (1, 40),
    "max_task_requests": (1, MAX_ENTRIES * 40), "source_cache_ttl_seconds": (0, 604800),
    "source_max_results": (1, 5), "knowledge_ttl_seconds": (0, 2592000),
    "max_upload_bytes": (1024, MAX_BYTES), "max_entries": (1, MAX_ENTRIES),
}
MODEL_LIMITS = {"timeout_seconds": (1, 120), "max_retries": (0, 3), "max_output_tokens": (1, 8192)}
MODEL_DEFAULTS = {
    "enabled": False, "library": "mmsp", "protocol": "openai-chat", "model": "", "base_url": "",
    "api_key": "", "max_retries": 1, "timeout_seconds": 30, "max_output_tokens": 1024,
    "price_source": "mmsp_registry",
}
MODEL_WRITABLE = frozenset(MODEL_DEFAULTS) - {"library", "price_source"}
SETTING_GROUPS = {
    "runtime": {"entry_request_budget": "max_source_requests", "request_budget": "max_task_requests",
                "timeout_seconds": "request_timeout_seconds",
                "max_retries": "retries", "max_upload_bytes": "max_upload_bytes"},
    "cache": {"positive_ttl_seconds": "source_cache_ttl_seconds", "discovery_ttl_seconds": "knowledge_ttl_seconds"},
}


def public_limits():
    groups = {group: {key: {"min": SETTING_LIMITS[internal][0], "max": SETTING_LIMITS[internal][1]}
                      for key, internal in fields.items()} for group, fields in SETTING_GROUPS.items()}
    groups["runtime"]["concurrency"] = {"min": 1, "max": 1}
    groups["cache"]["negative_ttl_seconds"] = {"min": NEGATIVE_TTL_SECONDS, "max": NEGATIVE_TTL_SECONDS}
    groups["model"] = {key: {"min": low, "max": high} for key, (low, high) in MODEL_LIMITS.items()}
    return groups
