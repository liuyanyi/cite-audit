import pytest

from cite_audit.settings import Settings, redact, secrets_from
from cite_audit.storage import Store


@pytest.fixture
def settings(tmp_path):
    return Settings(Store(tmp_path / "settings.sqlite"))


def test_keys_write_only_omitted_preserved_and_empty_clears(settings):
    settings.update({"credentials": {"openalex": "source-secret"}, "model": {"api_key": "model-secret"}})
    result = settings.public()
    assert "source-secret" not in str(result) and "model-secret" not in str(result)
    assert result["model"]["has_api_key"] is True
    settings.update({"model": {"model": "test"}})
    assert settings.get()["model"]["api_key"] == "model-secret"
    settings.update({"model": {"api_key": ""}})
    assert settings.public()["model"]["has_api_key"] is False
    assert not any(p["requires_key"] for p in result["sources"])


@pytest.mark.parametrize("update", [
    {"model": {"enabled": "true"}}, {"model": {"timeout_seconds": False}},
    {"model": {"max_retries": 1.5}}, {"model": {"max_retries": 1000}},
    {"model": {"base_url": "http://localhost"}}, {"model": {"protocol": "unknown"}},
    {"model": {"api_key": "secret\r\nInjected: true"}}, {"credentials": {"acl": "unsupported"}},
    {"credentials": {"openalex": "secret\n"}}, {"max_source_requests": 1.2}, {"retries": True},
])
def test_invalid_config_not_saved(settings, update):
    before = settings.get()
    with pytest.raises(ValueError):
        settings.update(update)
    assert settings.get() == before


def test_redaction_is_recursive(settings):
    config = {"model": {"api_key": "private-value"}, "credentials": {"openalex": "source-value"}}
    result = redact({"api_key": "private-value", "message": "Bearer private-value api_key=source-value",
                     "nested": ["private-value"]}, secrets_from(config))
    assert "private-value" not in str(result) and "source-value" not in str(result)


def test_public_limits_roundtrip_and_parser_upload_boundary(settings):
    from cite_audit.bib import MAX_BYTES

    public = settings.public()
    assert public["limits"]["runtime"]["max_upload_bytes"]["max"] == MAX_BYTES
    assert public["cache"]["negative_ttl_seconds"] == 60
    writable = {key: value for key, value in public.items() if key != "limits"}
    writable["runtime"]["max_upload_bytes"] = MAX_BYTES
    saved = settings.update_public(writable)
    assert saved["runtime"]["max_upload_bytes"] == MAX_BYTES
    assert "api_key" not in saved["model"]
    with pytest.raises(ValueError):
        settings.update_public({"runtime": {"max_upload_bytes": MAX_BYTES + 1}})
    assert settings.public()["runtime"]["max_upload_bytes"] == MAX_BYTES


@pytest.mark.parametrize("body", [{"runtime": []}, {"cache": []}, {"model": []}, {"sources": [None]}])
def test_malformed_public_groups_do_not_write(settings, body):
    before = settings.get()
    with pytest.raises(ValueError):
        settings.update_public(body)
    assert settings.get() == before
