# Cite Audit remake: implementation API contract

This is the shared greenfield backend/frontend contract. JSON field names below are authoritative; example bibliography values are **synthetic demo data**, not online evidence. IDs are opaque strings; timestamps are UTC ISO-8601; unknown values are `null`, never fabricated zero. Backend may use dataclasses and SQLite JSON columns. No old application API compatibility is required.

## Transport and conventions

- Base `/api`; JSON except multipart upload and downloads. Lists return `{ "items": [], "total": 0, "page": 1, "page_size": 25 }`. Query parameters: `q`, `page`, `page_size`, `sort`, then route-specific filters. `page_size` maximum 100.
- Errors: `{ "error": { "code": "not_found", "message": "Task not found", "details": {} } }`. FastAPI validation errors may additionally use standard `detail` during initial implementation; client must handle both.
- Modes: `demo | online`; default **demo**, never silently fall back from online to demo. Demo knowledge is isolated from online knowledge and visibly labelled.
- Execution: `queued | running | completed | partial | failed | cancelled`.
- A finished assessment is `completed`, including an inconclusive result, field conflicts, source coverage limits, rate limits or exhausted request budgets. These remain visible in identity/field results and call audits. `partial` is reserved for a task with entries that did not finish assessment (such as parsing or internal processing failures); cancellation and total failure retain their own states. Previously frozen reports are not rewritten.
- Identity: `confirmed | ambiguous | not_found | insufficient | source_failed`.
- Version: `current_version_confirmed | candidate | unknown`.
- Field judgment: `consistent | conflict | completion | normalization | unverifiable | not_applicable`.
- Recommendation: `same_version | upgrade_available | unavailable | ambiguous`.
- Proposal: `same_version_patch | whole_version_switch`.
- Field/identifier values are strings. `entrytype` is a field in integrated records and proposals, separate from citation key. Authors remain a single ordered BibTeX string, never a set union.

## Tasks

Crossref work records retain validated `fields.issn` values. After a confirmed publication's journal name is judged conflicting, online tasks query `/journals/{issn}` under the remaining request budget. Task-local `journal_checks` retain returned title/ISSNs, queried ISSN, equivalence decision and immutable snapshot ID. Returned ISSN must include the queried value; both names must match the returned title or a publisher-attested alias. Input ISSN contradictions block equivalence. Unavailable records never clear a conflict. The CMC alias is supported by https://www.techscience.com/journal/cmc; arbitrary acronym prefixes are not removed. Journal records do not create article/version identities or shared task conclusions. Crossref text entities are decoded in extracted text, while response snapshots remain unchanged.

### Live changes (SSE)

`GET /events?task_id={id}&after={cursor}` streams `text/event-stream`. Omit `task_id` for the task list,
including active tasks outside its current filter/page. Unknown task IDs return 404. Cursors are nonnegative
signed 64-bit integers; `Last-Event-ID` takes priority over `after` on native EventSource reconnection.

Each named event includes an SSE `id` (the durable SQLite change sequence):

- `sync`: `{ "reset": true, "task_ids": ["task_id"] }`. Re-read visible REST resources. Always sent on connection;
  `reset=false` means the supplied cursor can also be replayed. A missing, expired or future cursor requires resync.
- `change`: `{ "items": [{ "task_id": "task_id", "changed": ["task", "entry", "call", "deleted"], "entry_ids": ["entry_id"] }] }`.
  `changed` contains only affected categories; one frame can coalesce multiple commits. Invalidate the current
  filtered entry page, and fetch calls only when their tab is visible. Do not infer factual evidence from these notices.
- `done`: `{ "task_id": "task_id" }` (null for all tasks). Perform a final REST synchronization and **close EventSource**.
  The server then ends the stream. A terminal execution status alone is insufficient: the final report must be frozen.

Comments provide a 15-second idle heartbeat. Responses disable caching and proxy buffering. Change notices are written
in the same transaction as task/entry/call changes; only committed writes wake subscribers. The most recent 2000 notices
are retained; expired cursors fall back to a full REST resync, including after process restart. Notices contain opaque
IDs and categories, never Bib fields, prompts, source responses or secrets. Disconnecting cancels only the subscription.

Task responses add `finalized: boolean`, true only when their report has been frozen. Existing historical report bytes
are not rewritten and can omit this field. Task list pages additionally return `active_tasks`, counting all tasks still
executing/finalizing regardless of current pagination or filters. Entry/call filtering and pagination run in SQLite;
task summaries aggregate stored results without materializing every full entry response.

The UI opens one SSE connection per visible task page while work remains. It coalesces notices for 100 ms, closes when
hidden, and reconnects with its last cursor when visible. Task list, task detail and citation detail use this transport;
an already finished citation still observes its parent task for final cross-entry warnings. A persistent SSE error
permits a delayed safety read while EventSource retries; healthy streams and terminal pages have no periodic polling.
Returning online or to a visible page also refreshes REST data. Slow reads finish before any queued refresh starts.

Entry responses include `verification_supported`: false for patents, recognized Chinese patent numbers, or an explicit whole-entry coverage limitation. The UI shows these as gray **不支持核验** and the `identity_state=unsupported` filter selects them separately from insufficient evidence. A single provider's discovery limitation does not mark the whole entry unsupported. Frozen identity conclusions are not rewritten.

`POST /tasks/{task_id}/entries/{entry_id}/retry` requeues only the selected entry **within the same task**.
`POST /tasks/{task_id}/retry` requeues every entry within that task. Both return `200` with the same Task ID;
neither creates a task or entry. The parent task must be terminal with a frozen report; overlapping or duplicate
retries return 400, and an entry belonging to another task returns 404. Upload bytes, entry IDs, ordinals, macro
context, mode and runtime limits are retained. Selected results/proposals are cleared before enqueueing, while
unselected entry results and existing exports remain intact. Re-audits force fresh source requests rather than
knowledge/response cache reuse. Request budgets and result call records are scoped to the new report revision;
the complete task call audit remains available. Cancelled requests from older revisions cannot write into a retry.

Task responses include `report_revision` (starting at 1). Before requeueing, the exact frozen report is archived
atomically, then the current report is cleared and the revision advances. `GET /tasks/{id}/report` returns the
current revision (a live snapshot until frozen); `GET /tasks/{id}/reports` lists frozen revisions as
`{"items":[{"revision":1,"report_url":"/api/tasks/{id}/reports/1"}]}`. `GET /tasks/{id}/reports/{revision}`
returns that immutable report, or 404 for an unfrozen/unknown revision. Historical reports keep their original
snapshots, merge/rule revisions and calls. The UI keeps the current route and filters during retry, reconnects to
task events, and provides revision downloads in the task's original-file tab.

The task table groups terminal entries using `classification`: `version_confirmed` (**版本已确认**),
`not_found` (**未找到匹配**), or `unverifiable` (**无法核验**). Queued/running entries have `classification=null`
and show their execution state without a premature result type. Unsupported entries, failed/cancelled entries,
ambiguous identities and insufficient evidence are unverifiable. A version is confirmed only when
both identity and the current publication version are confirmed; field conflicts do not remove that classification.
The existing red **未找到匹配** display groups `identity_state=not_found` and `source_failed`; the type column,
type filter and not-found metric use that same grouping. The raw identity state, detailed reason and call audit
continue to distinguish source failure from an actual negative search; the display does not claim nonexistence.

`GET /tasks/{id}/entries?classification=version_confirmed|not_found|unverifiable` filters the whole task in SQLite
before pagination, and can combine with search, sorting and `has_conflict`. Unknown nonempty values return 400.
The lower-level `identity_state=not_found` filter retains its existing grouping of not-found and source-failed
records. The UI's type filter uses `classification`; frozen historical reports are not rewritten.

The UI exposes the three types as toggle buttons beside the independent field-conflict button. At most one type
is selected; pressing it again removes the type filter. No selected type means all types, and an active conflict
filter combines with the selected type using AND. Filter changes reset the page while retaining other URL options.

Task summaries additionally expose `version_confirmed`, `not_found`, `unverifiable` and `unprocessed` counts.
The task page's five metrics are **总条目 / 已处理 / 未找到匹配 / 有字段冲突的 / 未处理的**. Processed entries
retain the existing completed-or-partial execution count; unprocessed is the remainder (queued, running, failed or
cancelled). Thus `completed + unprocessed = total`; conflict counts overlap result types and must not be added to
them. The not-found metric covers the whole task, regardless of the visible filters or page.

`POST /tasks`: multipart `file` (required .bib), `name` (optional), `mode` (`demo` default), `config` (optional JSON string of per-task runtime overrides). No parent/review parameter. Return `201` with a Task object. Every call creates a fresh ID, including identical bytes. Existing task outcomes are never inherited.

Runtime settings and task overrides expose `entry_request_budget` (1–40, default 18) and
`request_budget` (task total, 1–400000, default 180). Both are frozen in each new task's configuration.
All source stages for an entry, including journal verification, share its allowance. Requests also consume
the task allowance; retries, redirects and failed connection attempts count. Cache/knowledge reuse, demo
responses and summary events consume no requests. Entry exhaustion does not consume the next entry's allowance.
Cancellation/recovery retains spent requests in the same report revision; an explicit re-audit starts new allowances.

Before any upload/task/entry is written or queued, the server parses the Bib and compares its actual entry count
times `entry_request_budget` with `request_budget`. If greater, return `409` with
`error.code=budget_confirmation_required` and `error.details={entry_count,entry_request_budget,request_budget,maximum_requests}`.
This is a theoretical maximum, not estimated consumption. The UI offers **调整预算** (returns to the selected
file and opens request settings) or **按当前预算继续** (resubmits the same file and explicit limits with multipart
`confirm_budget=true`). The total cap remains enforced after confirmation. Equality needs no confirmation.

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
    "upgrade_available": 1, "completion_available": 0, "problem_entries": 1,
    "version_confirmed": 1, "not_found": 0, "unverifiable": 1, "unprocessed": 0
  },
  "warnings": [], "cost": {"amount": null, "currency": null, "known": false},
  "original_url": "/api/tasks/task_example/original", "report_url": "/api/tasks/task_example/report"
}
```

`completed/running/queued/failed/cancelled` count execution groups; `confirmed/conflicts/unresolved` and suggestions are result dimensions and must not be added into one pie. `problem_entries` is distinct-entry count, not sum of issue labels.

- `GET /tasks/{id}/original`: exact bytes, safe attachment filename; never reserialize.
- `GET /tasks/{id}/report`: `{ "task": Task, "entries": [EntryDetail], "calls": [CallRecord], "generated_at": timestamp }`; final reports freeze actual evidence/revisions.
- `POST /tasks/{id}/cancel`: return Task, retain completed results.
- `POST /tasks/{id}/retry`: requeue entries within the same finalized Task, preserving task/entry IDs, immutable original bytes, prior report revisions and existing exports. With no body, rerun all entries. An explicit selection supplies `revision` (the current report revision), `scope=all|categories|entries`, and `categories` or `entry_ids` for the respective scope; online reruns may supply `entry_request_budget` and `request_budget`. Only selected results are reset. Stale revisions and duplicate requests cannot overwrite a newer round. Process-restart recovery resumes unfinished work internally.
- `POST /tasks/{id}/entries/{entry_id}/retry`: recheck this entry within the same finalized Task; other entries and archived reports remain unchanged. Report revision downloads are listed by `GET /tasks/{id}/reports`.
- `DELETE /tasks/{id}`: return `{ "deleted": true, "knowledge_preserved": true }`; UI confirms task-only scope. The upload is deleted only when no remaining task references its hash. Shared knowledge and snapshots are preserved.

## Entries

Identity rule `publication-identity-v2`: `ambiguous` means multiple publication versions pass identity matching. Relevant but unconfirmed records yield `insufficient`; rejected search noise does not create ambiguity. Failed/limited source access remains distinct from an unsupported source capability (`error_code=coverage_unavailable`).

Entry details additionally expose `excluded_candidates` separately from `candidates`. Candidate records carry `source_content_id`, `snapshot_id`, `identifiers`, `author_match` and `title_similarity`; grouped candidates retain each originating record in `records`. These are task-local comparisons, not shared factual evidence. Title similarity is a retrieval filter, not an identity confidence score. Historical frozen reports may omit these new optional fields.

`GET /tasks/{id}/entries` list filters: `q` searches key/title/author; `classification`, `identity_state`, `recommendation_status`, `has_conflict=true`, `sort=ordinal|title|year`. Each item is EntrySummary:

```json
{
  "id": "entry_example", "task_id": "task_example", "ordinal": 0,
  "citation_key": "example2024", "entry_type": "misc",
  "title": "A Synthetic Example", "author": "Example, Ada", "year": "2024",
  "execution_status": "completed", "identity_state": "confirmed", "classification": "version_confirmed",
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

Knowledge search matches literal, Unicode case-insensitive substrings in the article title, authors from any of its publication versions, or identifier values. Mode filtering, total counts and stable pagination run in SQLite; summaries count versions and sources without loading source content, snapshots or settings. Search does not call external providers. Full version evidence is loaded through the article detail endpoint.

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

`GET /settings` and `PUT /settings`; PUT accepts the writable groups `sources`, `model`, `runtime`, `cache`, `audit`. Read also returns read-only `limits`, grouped by `runtime`, `cache`, `model`, with `{ "min": number, "max": number }` for each numeric field. Clients use these limits for inputs and never submit the `limits` group. Provider/model API keys are **write-only** fields; omitted key preserves the stored value, empty string clears it. Never return masked key text that the client might accidentally resubmit as a key.

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
  "runtime": {"request_budget": 30, "timeout_seconds": 20, "max_retries": 2, "concurrency": 1, "max_upload_bytes": 5242880},
  "cache": {"positive_ttl_seconds": 604800, "negative_ttl_seconds": 60, "discovery_ttl_seconds": 86400},
  "audit": {"enabled": true, "retain_prompt_text": false, "redact_secrets": true}
}
```

Model protocols: `openai-chat | openai-responses | ant-messages`. Model calls use the real MMSP SDK, not a bespoke HTTP client carrying the label. Application controls retry; underlying SDK retries are disabled. Models disabled by default; no billed setup tests. MMSP catalog supplies static list-price snapshots; actual reported usage times supported rates produces estimated cost. Unknown model/rate/usage yields unknown cost, not zero. Catalog price is not a provider invoice or negotiated gateway price.

`GET /models/catalog` may expose sanitized MMSP static catalog entries with model/client/base URL/currency/rates; no network required. `POST /settings/sources/{id}/test` explicitly performs a real source request, bypassing query-cache reads and using a record within that provider's coverage (PMLR: `v139/radford21a`). It returns `provider`, `status`, `message`, `calls`, `records_received`, and `knowledge_written=false`. Test statuses are `available` (parsed record received), `no_result` (request completed without a record), `rate_limited`, `unavailable` (connection or response processing failed), and `inconclusive` (unsupported discovery, budget exhaustion, cancellation, or no request attempted). A missing record never alone establishes a connection failure or that a paper does not exist. Tests write audit records but do not write knowledge or change saved settings; settings reads retain `not_tested`. `POST /settings/model/test` requires `{ "confirmed": true }` because it can bill. Never assume configured means connected.

`GET /calls` accepts `task_id`, `entry_id`, `provider`, `status`, pagination; entry details may embed relevant records. CallRecord:

```json
{
  "id": "call_example", "task_id": "task_example", "entry_id": "entry_example", "provider": "crossref", "kind": "source",
  "operation": "lookup", "status": "success", "started_at": "2026-10-30T05:00:00Z", "duration_ms": 123,
  "attempt": 1, "request_count": 1, "cache_hit": false, "mode": "online", "error_code": null, "message": null,
  "usage": null, "cost": {"amount": null, "currency": null, "known": false, "price_snapshot": null}
}
```

Call `status`: `success | no_result | failed | rate_limited | budget_exhausted | cancelled | partial`. `attempt` is one-based. Source `request_count=1` marks a request attempt, including a DNS/connection failure; retries and redirects count individually. Cache hits, fixtures, knowledge reuse and result summaries have `request_count=0`. Task request budgets sum source request attempts, never summary events. Exhaustion is an incomplete outcome, not successful completion. Model usage supports `input_tokens`, `output_tokens`, `cached_input_tokens`, `reasoning_tokens`; absent remains null. Record each application attempt and a final outcome; raw exception text, authorization headers and API keys are never exposed. Different currencies are not added together.

`GET /calls/{id}` reads the immutable saved call details (404 for an unknown/deleted call). Optional `request_summary` contains the actual request URL or bounded lookup hints. Source HTTP calls retain `http_status`, `fetched_at` and `response_summary` (`url`, `http_status`, `fetched_at`, original `byte_size`, redacted plain-text `body`, `truncated`). JSON responses are redacted structurally before serialization; previews are redacted before the 32,000-character limit. Response headers are not retained. Cache-hit details retain the reused response's original retrieval time. Result/fixture events instead retain `record_count` and extracted `records` with provider, record ID, URL, fields and identifiers; these remain candidates, not confirmed matches or shared factual evidence. Missing historical details are not reconstructed from newer knowledge. Model prompt retention remains disabled.

Call lists and task/report call summaries omit `response_summary`; the UI fetches it only when opening a row's details dialog. Existing frozen reports are not rewritten. Source failures without a received response have no response preview.

`DELETE /query-cache` returns `{ "cleared": true, "knowledge_preserved": true, "history_preserved": true }`. Settings UI must clearly separate cache clearing, task deletion, and any future knowledge deletion.

## Shared invariants

1. Facts are source-backed; synthetic/manual/unavailable evidence is labelled, not online verified.
2. Same-version source differences do not create fake versions; unresolved conflicts remain visible.
3. Current-version verification and recommendation differences never share error semantics.
4. Recommendations target a confirmed existing Version of the same Article or are null.
5. Original bytes and final historical evidence/results are immutable.
6. User export selection belongs only to its task and cannot change shared facts.
