CREATE TABLE IF NOT EXISTS audit_calls (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 source_call_id INTEGER UNIQUE,
 task_id TEXT,
 entry_id TEXT,
 provider_id TEXT,
 created_at TEXT NOT NULL,
 payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS audit_task ON audit_calls(task_id,id);
CREATE INDEX IF NOT EXISTS audit_provider ON audit_calls(provider_id,id);
INSERT OR IGNORE INTO audit_calls(source_call_id,task_id,entry_id,provider_id,created_at,payload)
 SELECT id,task_id,entry_id,json_extract(payload,'$.provider_id'),created_at,payload FROM provider_calls;
PRAGMA user_version = 2;
