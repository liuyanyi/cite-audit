# Cite Audit remake: implementation API contract

This is the shared greenfield backend/frontend contract. JSON field names below are authoritative; example bibliography values are **synthetic demo data**, not online evidence. IDs are opaque strings; timestamps are UTC ISO-8601; unknown values are `null`, never fabricated zero. Backend may use dataclasses and SQLite JSON columns. No old application API compatibility is required.

## Transport and conventions

- Base `/api`; JSON except multipart upload and downloads. Lists return `{ "items": [], "total": 0, "page": 1, "page_size": 25 }`. Query parameters: `q`, `page`, `page_size`, `sort`, then route-specific filters. `page_size` maximum 100.
- Errors: `{ "error": { "code": "not_found", "message": "Task not found", "details": {} } }`. FastAPI validation errors may additionally use standard `detail` during initial implementation; client must handle both.
- Modes: `demo | online`; default **demo**, never silently fall back from online to demo. Demo knowledge is isolated from online knowledge and visibly labelled.
- Execution: `queued | running | completed | partial | failed | cancelled`.
- Identity: `confirmed | ambiguous | not_found | insufficient | source_failed`.
- Version: `current_version_confirmed | candidate | unknown`.
- Field judgment: `consistent | conflict | completion | normalization | unverifiable | not_applicable`.
- Recommendation: `same_version | upgrade_available | unavailable | ambiguous`.
- Proposal: `same_version_patch | whole_version_switch`.
- Field/identifier values are strings. `entrytype` is a field in integrated records and proposals, separate from citation key. Authors remain a single ordered BibTeX string, never a set union.

## Tasks

`POST /tasks`: multipart `file` (required .bib), `name` (optional), `mode` (`demo` default), `config` (optional JSON string of per-task runtime overrides). No parent/review parameter. Return `201` with a Task object. Every call creates a fresh ID, including identical bytes. Existing task outcomes are never inherited.

`GET /tasks` supports `q`, `execution_status`, `sort=created_desc|created_asc|updated_desc`.

`GET /tasks/{task_id}` returns Task:

```json
{
  "id": "task_example", "name": "论文参考文献", "filename": "references.bib", "mode": "demo",
  "original_sha256": "sha256-hex", "byte_size": 440, "encoding": "utf-8",
  "created_at": "2026-10-30T05:00:00Z", "updated_at": "2026-10-30T05:01:00Z",
  "execution_status": "completed", "error": null,
  "config": {"request_budget": 30, "timeout_seconds": 20, "max_retries": 2, "model_enabled": false},
  "summary": {
    "total": 2, "completed": 2, "running": 0, "queued": 0, "failed": 0, "cancelled": 0,
    "parse_errors": 0, "confirmed": 1, "conflicts": 0, "unresolved": 1,
    "upgrade_available": 1, "completion_available": 0, "problem_entries": 1
  },
  "warnings": [], "cost": {"amount": null, "currency": null, "known": false},
  "original_url": "/api/tasks/task_example/original", "report_url": "/api/tasks/task_example/report"
}
```

`completed/running/queued/failed/cancelled` count execution groups; `confirmed/conflicts/unresolved` and suggestions are result dimensions and must not be added into one pie. `problem_entries` is distinct-entry count, not sum of issue labels.

- `GET /tasks/{id}/original`: exact bytes, safe attachment filename; never reserialize.
- `GET /tasks/{id}/report`: `{ "task": Task, "entries": [EntryDetail], "calls": [CallRecord], "generated_at": timestamp }`; final reports freeze actual evidence/revisions.
- `POST /tasks/{id}/cancel`: return Task, retain completed results.
- `POST /tasks/{id}/retry`: create and return a fresh Task from immutable original bytes and configuration. This operation never overwrites old reports or inherits conclusions; UI explicitly says it creates a new task. Process-restart recovery resumes unfinished work internally.
- `DELETE /tasks/{id}`: return `{ "deleted": true, "knowledge_preserved": true }`; UI confirms task-only scope.

## Entries

`GET /tasks/{id}/entries` list filters: `q` searches key/title/author; `identity_state`, `recommendation_status`, `has_conflict=true`, `sort=ordinal|title|year`. Each item is EntrySummary:

```json
{
  "id": "entry_example", "task_id": "task_example", "ordinal": 0,
  "citation_key": "example2024", "entry_type": "misc",
  "title": "A Synthetic Example", "author": "Example, Ada", "year": "2024",
  "execution_status": "completed", "identity_state": "confirmed",
  "version_status": "current_version_confirmed", "recommendation_status": "upgrade_available",
  "has_conflict": false, "conflict_count": 0, "completion_count": 0,
  "article_id": "article_example", "current_version_id": "version_preprint",
  "recommended_version_id": "version_conference", "mode": "demo", "diagnostics": []
}
```

`GET /tasks/{id}/entries/{entry_id}` returns all summary fields plus:

```json
{
  "original_bib": "@misc{example2024, title={A Synthetic Example}, year={2024}}",
  "fields": {"title": "A Synthetic Example", "year": "2024"},
  "diagnostics": [{"code": "duplicate_key", "message": "Duplicate citation key", "line": 3, "column": 1}],
  "identity_reason": "Fixture with explicit preprint/publication relationship; not online verified.",
  "field_assessments": [{
    "field": "year", "original_value": "2024", "reference_value": "2024",
    "judgment": "consistent", "reason": "Matches current preprint version",
    "evidence": [{"source_content_id": "source_preprint", "snapshot_id": "snapshot_demo_1", "field": "year", "locator": "fields.year"}]
  }],
  "recommendation": {
    "status": "upgrade_available", "version_id": "version_conference",
    "reason": "Explicit fixture relationship to a formal publication", "revision": 1,
    "differences": [{"field": "year", "original_value": "2024", "reference_value": "2025", "judgment": "version_change", "evidence": []}]
  },
  "current_version": null, "recommended_version": null,
  "version_relations": [{"id": "relation_demo", "from_version_id": "version_preprint", "to_version_id": "version_conference", "relation_type": "published_as", "status": "confirmed", "reason": "Explicit source relationship", "evidence": []}],
  "proposals": [{
    "id": "proposal_example", "proposal_type": "whole_version_switch", "target_version_id": "version_conference",
    "fields": {"entrytype": "inproceedings", "title": "A Synthetic Example", "author": "Example, Ada", "year": "2025", "booktitle": "Synthetic Proceedings"},
    "remove_fields": ["eprint", "archiveprefix", "primaryclass", "doi", "url"],
    "bibtex": "@inproceedings{example2024, ...}", "reason": "Switch as one coherent version",
    "evidence": [], "safe": true, "blocked_reason": null
  }],
  "sources": [], "candidates": [], "calls": [], "warnings": [],
  "evidence_revision": {"merge_revision": 1, "recommendation_revision": 1, "rule_version": "1"}
}
```

`current_version` / `recommended_version` are Version objects below when present, else null. `sources` contains SourceContent objects actually used, not arbitrary latest records. `candidates` entries: `{ "title": string, "provider": string, "record_id": string, "url": string|null, "reason": string, "status": "unresolved" }`. `calls` contains CallRecords. Diagnostics arrays are empty for clean entries. `ordinal` is zero-based.

Recommendation comparison `judgment` is `unchanged | version_change | target_unverified`; never display it as a current-version error. Current-version evidence may be confirmed while an upgrade remains advisory.

## Knowledge and evidence

`GET /articles` (q, mode, pagination) and `GET /articles/{id}`. Article summary/detail:

```json
{
  "id": "article_example", "title": "A Synthetic Example", "author": "Example, Ada", "mode": "demo",
  "recommended_version_id": "version_conference", "recommendation_reason": "Confirmed formal version",
  "recommendation_revision": 1, "version_count": 2, "source_count": 3,
  "updated_at": "2026-10-30T05:00:00Z", "versions": [], "version_relations": []
}
```

List may omit `versions` and `version_relations`; detail always includes both. Recommended version must belong to this Article. No endpoint lets task decisions change recommendation.

`GET /versions/{id}` returns Version:

```json
{
  "id": "version_conference", "article_id": "article_example", "version_type": "conference",
  "status": "confirmed", "mode": "demo", "merge_revision": 1,
  "identifiers": {"doi": "10.0000/synthetic"},
  "integrated_fields": {"entrytype": "inproceedings", "title": "A Synthetic Example", "author": "Example, Ada", "year": "2025"},
  "field_selections": [{"field": "year", "value": "2025", "status": "selected", "reason": "Direct source field", "evidence": [{"source_content_id": "source_demo", "snapshot_id": "snapshot_demo_2", "field": "year", "locator": "fields.year"}], "alternatives": []}],
  "conflicts": [], "sources": [], "source_count": 1,
  "updated_at": "2026-10-30T05:00:00Z"
}
```

`version_type`: `preprint | conference | journal | other`; preserve more specific platform/venue in fields. Conflict shape: `{ "field": "pages", "values": [{"value": "1--9", "evidence": []}], "reason": "Unresolved source disagreement" }`.

`GET /sources/{id}` returns SourceContent:

```json
{
  "id": "source_demo", "provider": "fixture", "record_id": "demo:conference:1", "object_type": "publication",
  "version_id": "version_conference", "attribution_status": "confirmed", "source_type": "demo",
  "mode": "demo", "url": null, "fetched_at": "2026-10-30T05:00:00Z", "checked_at": "2026-10-30T05:00:00Z",
  "status": "demo", "snapshot_id": "snapshot_demo_2", "snapshot_sha256": "sha256-hex",
  "fields": {"title": "A Synthetic Example", "year": "2025"}, "field_coverage": ["title", "year"],
  "extraction_method": "fixture", "snapshot_url": "/api/snapshots/snapshot_demo_2", "warnings": []
}
```

Source `status`: `available | stale | inaccessible | failed | demo | manual_unverified`. Source type: `official | registry | preprint | aggregator | manual | demo`. `GET /snapshots/{id}` returns `{ "id", "sha256", "fetched_at", "content_type", "body", "mode" }` with raw content rendered as inert text; no executing remote HTML.

## Export: pure preview, explicit creation

`POST /tasks/{id}/export-preview` body:

```json
{"selections": [{"entry_id": "entry_example", "proposal_id": "proposal_example", "fields": null}]}
```

`fields=null` means all proposal fields. A same-version patch may name a field subset. A whole-version switch is atomic and must reject a non-null subset. Empty selections returns byte-identical original as a new preview. Server uses its frozen proposal values, not client-supplied Bib fields.

Preview response:

```json
{
  "filename": "references.corrected.bib", "bibtex": "@inproceedings{...}",
  "original_sha256": "original-hash", "output_sha256": "output-hash",
  "changes": [{"entry_id": "entry_example", "citation_key": "example2024", "proposal_type": "whole_version_switch", "fields": ["entrytype", "year"], "before": "@misc{...}", "after": "@inproceedings{...}"}],
  "warnings": [], "blocked": [], "unchanged_entries": 0, "changed_entries": 1
}
```

`POST /tasks/{id}/exports` body: same selections plus `"confirmed": true, "expected_output_sha256": "output-hash"`. Recompute and reject mismatch. Return `{ "id": "export_id", "filename": "references.corrected.bib", "original_sha256": "...", "output_sha256": "...", "download_url": "/api/tasks/task_id/exports/export_id/download", "report_url": "/api/tasks/task_id/exports/export_id/report", "created_at": "..." }`. Download endpoint returns new file bytes; report endpoint returns change manifest. Never modify original, shared fields or recommendation.

## Settings, MMSP and call audit

`GET /settings` and `PUT /settings`; PUT accepts the same writable groups. Read returns sanitized effective configuration. Provider/model API keys are **write-only** fields; omitted key preserves the stored value, empty string clears it. Never return masked key text that the client might accidentally resubmit as a key.

```json
{
  "sources": [
    {"id": "crossref", "name": "Crossref", "enabled": true, "implemented": true, "requires_key": false, "has_api_key": false, "status": "not_tested", "capabilities": ["lookup", "search"]},
    {"id": "semantic_scholar", "name": "Semantic Scholar", "enabled": false, "implemented": true, "requires_key": false, "has_api_key": false, "status": "not_tested", "capabilities": ["lookup", "search"]}
  ],
  "model": {
    "enabled": false, "library": "mmsp", "protocol": "openai-chat", "model": "", "base_url": "",
    "has_api_key": false, "timeout_seconds": 30, "max_retries": 2, "max_output_tokens": 1024,
    "price_source": "mmsp_registry", "price": null
  },
  "runtime": {"request_budget": 30, "timeout_seconds": 20, "max_retries": 2, "concurrency": 3, "max_upload_bytes": 5242880},
  "cache": {"positive_ttl_seconds": 604800, "negative_ttl_seconds": 3600, "discovery_ttl_seconds": 86400},
  "audit": {"enabled": true, "retain_prompt_text": false, "redact_secrets": true}
}
```

Model protocols: `openai-chat | openai-responses | ant-messages`. Model calls use the real MMSP SDK, not a bespoke HTTP client carrying the label. Application controls retry; underlying SDK retries are disabled. Models disabled by default; no billed setup tests. MMSP catalog supplies static list-price snapshots; actual reported usage times supported rates produces estimated cost. Unknown model/rate/usage yields unknown cost, not zero. Catalog price is not a provider invoice or negotiated gateway price.

`GET /models/catalog` may expose sanitized MMSP static catalog entries with model/client/base URL/currency/rates; no network required. `POST /settings/sources/{id}/test` explicitly performs a real source request; `POST /settings/model/test` requires `{ "confirmed": true }` because it can bill. Source statuses include `not_tested | available | unavailable | needs_key | not_implemented`, never assume configured means connected.

`GET /calls` accepts `task_id`, `entry_id`, `provider`, `status`, pagination; entry details may embed relevant records. CallRecord:

```json
{
  "id": "call_example", "task_id": "task_example", "entry_id": "entry_example", "provider": "crossref", "kind": "source",
  "operation": "lookup", "status": "success", "started_at": "2026-10-30T05:00:00Z", "duration_ms": 123,
  "attempt": 1, "cache_hit": false, "mode": "online", "error_code": null, "message": null,
  "usage": null, "cost": {"amount": null, "currency": null, "known": false, "price_snapshot": null}
}
```

Call `status`: `success | no_result | failed | rate_limited | budget_exhausted | cancelled | partial`. Model usage supports `input_tokens`, `output_tokens`, `cached_input_tokens`, `reasoning_tokens`; absent remains null. Record each application attempt and a final outcome; raw exception text, authorization headers and API keys are never exposed. Different currencies are not added together.

`DELETE /query-cache` returns `{ "cleared": true, "knowledge_preserved": true, "history_preserved": true }`. Settings UI must clearly separate cache clearing, task deletion, and any future knowledge deletion.

## Shared invariants

1. Facts are source-backed; synthetic/manual/unavailable evidence is labelled, not online verified.
2. Same-version source differences do not create fake versions; unresolved conflicts remain visible.
3. Current-version verification and recommendation differences never share error semantics.
4. Recommendations target a confirmed existing Version of the same Article or are null.
5. Original bytes and final historical evidence/results are immutable.
6. User export selection belongs only to its task and cannot change shared facts.
