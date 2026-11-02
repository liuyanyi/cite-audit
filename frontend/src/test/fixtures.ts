import type { Settings } from '../types';

export const settingsFixture: Settings = {
  limits: {
    runtime: { entry_request_budget: { min: 1, max: 40 }, request_budget: { min: 1, max: 400000 }, timeout_seconds: { min: 1, max: 60 }, max_retries: { min: 0, max: 2 }, concurrency: { min: 1, max: 1 }, max_upload_bytes: { min: 1024, max: 8388608 } },
    cache: { positive_ttl_seconds: { min: 0, max: 604800 }, negative_ttl_seconds: { min: 60, max: 60 }, discovery_ttl_seconds: { min: 0, max: 2592000 } },
    model: { timeout_seconds: { min: 1, max: 120 }, max_retries: { min: 0, max: 3 }, max_output_tokens: { min: 1, max: 8192 } },
  },
  sources: [{ id: 'semantic_scholar', name: 'Semantic Scholar', enabled: true, implemented: true, supports_api_key: true, requires_key: false, has_api_key: true, status: 'not_tested', capabilities: ['lookup', 'search'] }],
  model: { enabled: true, library: 'mmsp', protocol: 'openai-chat', model: 'sample-model', base_url: 'https://api.example.org', has_api_key: true, timeout_seconds: 30, max_retries: 1, max_output_tokens: 1024, price_source: 'mmsp_registry', price: null },
  runtime: { entry_request_budget: 18, request_budget: 100, timeout_seconds: 12, max_retries: 1, concurrency: 1, max_upload_bytes: 5000000 },
  cache: { positive_ttl_seconds: 3600, negative_ttl_seconds: 60, discovery_ttl_seconds: 86400 },
  audit: { enabled: true, retain_prompt_text: false, redact_secrets: true },
};
