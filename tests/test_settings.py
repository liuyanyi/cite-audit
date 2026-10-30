import pytest

from cite_audit.settings import Settings, redact, secrets_from
from cite_audit.storage import Store


@pytest.fixture
def settings(tmp_path):
    return Settings(Store(tmp_path / "settings.sqlite"))


def test_keys_write_only_omitted_preserved_and_empty_clears(settings):
    settings.update({"credentials": {"openalex": "source-secret"}, "model": {"api_key": "model-secret"}})
    result = settings.get(public=True)
    assert "source-secret" not in str(result) and "model-secret" not in str(result)
    assert result["model"]["api_key_configured"] is True
    settings.update({"model": {"model": "test"}})
    assert settings.get()["model"]["api_key"] == "model-secret"
    settings.update({"model": {"api_key": ""}})
    assert settings.get(public=True)["model"]["api_key_configured"] is False
    assert not any(p["requires_key"] for p in result["providers"])


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
