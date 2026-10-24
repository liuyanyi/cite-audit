PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS uploads (
 id TEXT PRIMARY KEY, filename TEXT NOT NULL, sha256 TEXT NOT NULL, content BLOB NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tasks (
 id TEXT PRIMARY KEY, upload_id TEXT NOT NULL REFERENCES uploads(id), filename TEXT NOT NULL,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL, status TEXT NOT NULL, mode TEXT NOT NULL,
 cnki_mode TEXT NOT NULL, parent_id TEXT, config_hash TEXT NOT NULL, parse_issues TEXT NOT NULL,
 options TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS entries (
 id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
 ordinal INTEGER NOT NULL, citation_key TEXT NOT NULL, fingerprint TEXT NOT NULL,
 snapshot TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued', result TEXT, error TEXT,
 reused INTEGER NOT NULL DEFAULT 0, started_at TEXT, completed_at TEXT
);
CREATE INDEX IF NOT EXISTS entries_task ON entries(task_id,ordinal);
CREATE INDEX IF NOT EXISTS entries_fingerprint ON entries(fingerprint);
CREATE TABLE IF NOT EXISTS query_cache (
 key TEXT PRIMARY KEY, value TEXT NOT NULL, created_at TEXT NOT NULL, expires_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS verification_cache (
 key TEXT PRIMARY KEY, result TEXT NOT NULL, created_at TEXT NOT NULL, expires_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS provider_calls (
 id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT REFERENCES tasks(id) ON DELETE CASCADE,
 entry_id TEXT, created_at TEXT NOT NULL, cache_hit INTEGER NOT NULL DEFAULT 0, payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS manual_records (
 id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, created_at TEXT NOT NULL, record TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS manual_fingerprint ON manual_records(fingerprint);
PRAGMA user_version = 1;
