CREATE TABLE IF NOT EXISTS source_snapshots (
 id TEXT PRIMARY KEY, source_key TEXT NOT NULL, provider TEXT NOT NULL,
 mode TEXT NOT NULL, version TEXT NOT NULL, payload TEXT NOT NULL,
 first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, last_checked_at TEXT,
 expires_at TEXT, cache_state TEXT NOT NULL DEFAULT 'fresh', revalidation_error TEXT NOT NULL DEFAULT 'null'
);
CREATE INDEX IF NOT EXISTS source_snapshots_source ON source_snapshots(source_key);
CREATE TABLE IF NOT EXISTS source_response_snapshots (
 id TEXT PRIMARY KEY, request_key TEXT NOT NULL, payload TEXT NOT NULL,
 first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, observation TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS library_items (
 id TEXT PRIMARY KEY, identity_key TEXT NOT NULL UNIQUE,
 status TEXT NOT NULL, mode TEXT NOT NULL, version TEXT NOT NULL,
 fields TEXT NOT NULL, identifiers TEXT NOT NULL, citation_keys TEXT NOT NULL,
 first_seen TEXT NOT NULL, last_seen TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS library_sources (
 item_id TEXT NOT NULL REFERENCES library_items(id) ON DELETE CASCADE,
 snapshot_id TEXT NOT NULL REFERENCES source_snapshots(id),
 PRIMARY KEY(item_id,snapshot_id)
);
CREATE TABLE IF NOT EXISTS library_decisions (
 id INTEGER PRIMARY KEY AUTOINCREMENT, item_id TEXT NOT NULL REFERENCES library_items(id) ON DELETE CASCADE,
 task_id TEXT, entry_id TEXT, created_at TEXT NOT NULL, payload TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS library_decisions_entry ON library_decisions(item_id,task_id,entry_id);
CREATE TABLE IF NOT EXISTS library_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
