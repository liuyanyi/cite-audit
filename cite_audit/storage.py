"""Small transactional store. Knowledge and task decisions have separate tables."""
from __future__ import annotations

import json
import re
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path


from .entry_status import summary_expressions


def now() -> str:
    return datetime.now(UTC).isoformat()


def uid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def dump(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def load(value, default=None):
    return json.loads(value) if value else default


SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version(version INTEGER NOT NULL);
INSERT INTO schema_version SELECT 1 WHERE NOT EXISTS(SELECT 1 FROM schema_version);
CREATE TABLE IF NOT EXISTS uploads(
 hash TEXT PRIMARY KEY, content BLOB NOT NULL, created_at TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS immutable_upload BEFORE UPDATE ON uploads
 BEGIN SELECT RAISE(ABORT,'original uploads are immutable'); END;
CREATE TABLE IF NOT EXISTS tasks(
 id TEXT PRIMARY KEY, name TEXT NOT NULL, filename TEXT NOT NULL,
 upload_hash TEXT NOT NULL REFERENCES uploads(hash), mode TEXT NOT NULL,
 status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 config TEXT NOT NULL, diagnostics TEXT NOT NULL, cancelled INTEGER NOT NULL DEFAULT 0,
 error TEXT, report TEXT, attempts INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS entries(
 id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
 ordinal INTEGER NOT NULL, parser_id TEXT NOT NULL, citation_key TEXT NOT NULL,
 entry_type TEXT NOT NULL, input_fields TEXT NOT NULL, raw_bib TEXT NOT NULL,
 diagnostics TEXT NOT NULL, editable INTEGER NOT NULL, status TEXT NOT NULL,
 result TEXT, UNIQUE(task_id,ordinal)
);
CREATE TABLE IF NOT EXISTS task_reports(
 task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
 revision INTEGER NOT NULL, report TEXT NOT NULL,
 PRIMARY KEY(task_id,revision)
);
CREATE TRIGGER IF NOT EXISTS immutable_task_report BEFORE UPDATE ON task_reports
 BEGIN SELECT RAISE(ABORT,'historical reports are immutable'); END;
CREATE TABLE IF NOT EXISTS snapshots(
 id TEXT PRIMARY KEY, hash TEXT NOT NULL, content BLOB NOT NULL,
 fetched_at TEXT NOT NULL, provider TEXT NOT NULL, source_url TEXT NOT NULL,
 UNIQUE(hash,provider,source_url)
);
CREATE TRIGGER IF NOT EXISTS immutable_snapshot BEFORE UPDATE ON snapshots
 BEGIN SELECT RAISE(ABORT,'source snapshots are immutable'); END;
CREATE TABLE IF NOT EXISTS articles(
 id TEXT PRIMARY KEY, namespace TEXT NOT NULL, title TEXT NOT NULL,
 recommended_version_id TEXT, recommendation TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS versions(
 id TEXT PRIMARY KEY, article_id TEXT NOT NULL REFERENCES articles(id),
 namespace TEXT NOT NULL, version_type TEXT NOT NULL, identifiers TEXT NOT NULL,
 fields TEXT NOT NULL, selections TEXT NOT NULL, merge_revision INTEGER NOT NULL,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sources(
 id TEXT PRIMARY KEY, namespace TEXT NOT NULL, provider TEXT NOT NULL,
 record_id TEXT NOT NULL, snapshot_id TEXT NOT NULL REFERENCES snapshots(id),
 version_id TEXT REFERENCES versions(id), status TEXT NOT NULL,
 data TEXT NOT NULL, created_at TEXT NOT NULL,
 UNIQUE(namespace,provider,record_id,snapshot_id)
);
CREATE TRIGGER IF NOT EXISTS immutable_source_content BEFORE UPDATE OF data,snapshot_id ON sources
 BEGIN SELECT RAISE(ABORT,'source content is immutable'); END;
CREATE TABLE IF NOT EXISTS source_checks(
 source_id TEXT PRIMARY KEY REFERENCES sources(id), checked_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS relations(
 id TEXT PRIMARY KEY, from_version_id TEXT NOT NULL REFERENCES versions(id),
 to_version_id TEXT NOT NULL REFERENCES versions(id), relation TEXT NOT NULL,
 source_id TEXT NOT NULL REFERENCES sources(id), evidence_url TEXT NOT NULL,
 UNIQUE(from_version_id,to_version_id,relation,source_id)
);
CREATE TABLE IF NOT EXISTS calls(
 id TEXT PRIMARY KEY, task_id TEXT REFERENCES tasks(id) ON DELETE CASCADE,
 entry_id TEXT, created_at TEXT NOT NULL, data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS exports(
 id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
 created_at TEXT NOT NULL, content BLOB NOT NULL, sha256 TEXT NOT NULL,
 selections TEXT NOT NULL, changes TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings(id INTEGER PRIMARY KEY CHECK(id=1),data TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS task_queue ON tasks(status,created_at);
CREATE INDEX IF NOT EXISTS task_entries ON entries(task_id,ordinal);
CREATE INDEX IF NOT EXISTS version_article ON versions(article_id);
CREATE INDEX IF NOT EXISTS source_version ON sources(version_id);
CREATE INDEX IF NOT EXISTS task_calls ON calls(task_id,created_at,id);
CREATE TABLE IF NOT EXISTS task_events(
 seq INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL,
 kind TEXT NOT NULL, entry_id TEXT
);
CREATE INDEX IF NOT EXISTS task_event_cursor ON task_events(task_id,seq);
CREATE TRIGGER IF NOT EXISTS event_retention AFTER INSERT ON task_events
 BEGIN DELETE FROM task_events WHERE seq <= NEW.seq - 2000; END;
CREATE TRIGGER IF NOT EXISTS task_created AFTER INSERT ON tasks
 BEGIN INSERT INTO task_events(task_id,kind) VALUES(NEW.id,'task'); END;
CREATE TRIGGER IF NOT EXISTS task_changed AFTER UPDATE OF status,report ON tasks
 WHEN NEW.status IS NOT OLD.status OR NEW.report IS NOT OLD.report
 BEGIN INSERT INTO task_events(task_id,kind) VALUES(NEW.id,'task'); END;
CREATE TRIGGER IF NOT EXISTS task_deleted AFTER DELETE ON tasks
 BEGIN INSERT INTO task_events(task_id,kind) VALUES(OLD.id,'deleted'); END;
CREATE TRIGGER IF NOT EXISTS entry_created AFTER INSERT ON entries
 BEGIN INSERT INTO task_events(task_id,kind,entry_id) VALUES(NEW.task_id,'entry',NEW.id); END;
CREATE TRIGGER IF NOT EXISTS entry_changed AFTER UPDATE OF status,result ON entries
 WHEN NEW.status IS NOT OLD.status OR NEW.result IS NOT OLD.result
 BEGIN INSERT INTO task_events(task_id,kind,entry_id) VALUES(NEW.task_id,'entry',NEW.id); END;
CREATE TRIGGER IF NOT EXISTS call_created AFTER INSERT ON calls WHEN NEW.task_id IS NOT NULL
 BEGIN INSERT INTO task_events(task_id,kind,entry_id) VALUES(NEW.task_id,'call',NEW.entry_id); END;
"""


def install_task_counts(db):
    # Install and backfill together so an interrupted initialization is retryable.
    db.execute("BEGIN IMMEDIATE")
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='task_counts'").fetchone():
        return
    fields = summary_expressions()
    columns = ",".join(fields)
    definitions = ",".join(f"{key} INTEGER NOT NULL" for key in fields)
    db.execute(f"CREATE TABLE task_counts(task_id TEXT PRIMARY KEY REFERENCES tasks(id) ON DELETE CASCADE,{definitions})")
    projection = ",".join(f"COALESCE(({expr}),0) AS {key}" for key, expr in fields.items())
    db.execute(f"CREATE VIEW entry_counts AS SELECT id,task_id,{projection} FROM entries")
    sums = ",".join(f"SUM({key})" for key in fields)
    db.execute(f"INSERT INTO task_counts SELECT task_id,{sums} FROM entry_counts GROUP BY task_id")
    add = ",".join(f"{key}={key}+excluded.{key}" for key in fields)
    insert = (f"INSERT INTO task_counts SELECT task_id,{columns} FROM entry_counts WHERE id=NEW.id "
              f"ON CONFLICT(task_id) DO UPDATE SET {add};")
    subtract = (f"UPDATE task_counts SET ({columns})=(SELECT "
                + ",".join(f"task_counts.{key}-e.{key}" for key in fields)
                + " FROM entry_counts e WHERE e.id=OLD.id) WHERE task_id=OLD.task_id;")
    updates = "UPDATE OF task_id,status,result,diagnostics,entry_type,input_fields"
    for name, event, body in (("insert", "AFTER INSERT", insert), ("delete", "BEFORE DELETE", subtract),
                              ("before_update", f"BEFORE {updates}", subtract),
                              ("after_update", f"AFTER {updates}", insert)):
        db.execute(f"CREATE TRIGGER counts_{name} {event} ON entries BEGIN {body} END")


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.on_change = None
        with self.connect() as db:
            db.executescript(SCHEMA)
            install_task_counts(db)
        # This is a local single-user database, including server-side secrets.
        try:
            self.path.chmod(0o600)
        except OSError:
            pass

    @contextmanager
    def connect(self):
        changed = False
        with self._lock:
            db = sqlite3.connect(self.path, timeout=30)
            db.row_factory = sqlite3.Row
            db.create_function("casefold", 1, lambda value: (value or "").casefold(), deterministic=True)
            db.create_function("regexp", 2, lambda pattern, value: bool(re.search(pattern, value or "")),
                               deterministic=True)
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA busy_timeout=30000")
            try:
                with db:
                    yield db
                changed = db.total_changes > 0
            finally:
                db.close()
        # Writers may run in FastAPI's thread pool. Notify only after commit and
        # outside the store lock; reads and rolled-back writes never wake streams.
        if changed and self.on_change:
            self.on_change()

    def one(self, sql: str, args=()):
        with self.connect() as db:
            row = db.execute(sql, args).fetchone()
            return dict(row) if row else None

    def all(self, sql: str, args=()):
        with self.connect() as db:
            return [dict(row) for row in db.execute(sql, args)]

    def execute(self, sql: str, args=()):
        with self.connect() as db:
            return db.execute(sql, args).rowcount

    def page(self, table_where, args=(), *, order, page=1, page_size=25, columns="*"):
        """SQL fragments are internal constants; all user values are parameters."""
        page, page_size = max(1, page), min(100, max(1, page_size))
        with self.connect() as db:
            db.execute("BEGIN")
            total = db.execute(f"SELECT COUNT(*) FROM {table_where}", args).fetchone()[0]
            items = [dict(row) for row in db.execute(
                f"SELECT {columns} FROM {table_where} ORDER BY {order} LIMIT ? OFFSET ?",
                [*args, page_size, (page - 1) * page_size])]
        return {"items": items, "total": total, "page": page, "page_size": page_size}

    def snapshot(self, record: dict) -> str:
        raw = dump(record.get("raw_payload", record.get("fields", {}))).encode()
        digest = sha256(raw).hexdigest()
        with self.connect() as db:
            previous = db.execute("SELECT id FROM snapshots WHERE hash=? AND provider=? AND source_url=?",
                                  (digest, record["provider"], record.get("source_url", ""))).fetchone()
            if previous:
                return previous["id"]
            sid = uid("snap")
            db.execute("INSERT INTO snapshots VALUES(?,?,?,?,?,?)",
                       (sid, digest, raw, record.get("fetched_at", now()), record["provider"],
                        record.get("source_url", "")))
            return sid
