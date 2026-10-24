"""SQLite state, immutable upload snapshots, caches and durable per-entry queue."""
from __future__ import annotations
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import time
import uuid


def now(): return datetime.now(timezone.utc).isoformat()
def uid(): return uuid.uuid4().hex
def encode(value): return json.dumps(value, ensure_ascii=False, sort_keys=True)

class Database:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.directory / 'cite-audit.sqlite3'
        with self.connect() as conn:
            conn.execute('PRAGMA journal_mode=WAL')
            schema = Path(__file__).resolve().parent / 'migrations' / '001_init.sql'
            conn.executescript(schema.read_text())
        self.path.chmod(0o600)

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA foreign_keys=ON')
        conn.execute('PRAGMA busy_timeout=30000')
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally: conn.close()

    def create_task(self, filename, content, parsed, mode, cnki_mode, config_hash, parent_id=None, options=None):
        task_id, upload_id, created = uid(), uid(), now()
        with self.connect() as conn:
            if parent_id and not conn.execute('SELECT 1 FROM tasks WHERE id=?', (parent_id,)).fetchone():
                raise ValueError('Parent task not found')
            conn.execute('INSERT INTO uploads VALUES(?,?,?,?,?)', (upload_id, filename, hashlib.sha256(content).hexdigest(), content, created))
            status = 'queued' if parsed['entries'] else 'completed'
            conn.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?,?,?,?)', (task_id, upload_id, filename, created, created, status, mode, cnki_mode, parent_id, config_hash, encode(parsed['issues']), encode(options or {})))
            for index, entry in enumerate(parsed['entries']):
                conn.execute('INSERT INTO entries(id,task_id,ordinal,citation_key,fingerprint,snapshot,status) VALUES(?,?,?,?,?,?,?)', (uid(), task_id, index, entry['citation_key'], entry['fingerprint'], encode(entry), 'queued'))
        return self.task(task_id)

    def _task_row(self, row, conn):
        if row is None: return None
        task = dict(row)
        task['parse_issues'] = json.loads(task['parse_issues'])
        task['options'] = json.loads(task['options'])
        entries = conn.execute('SELECT status,result,reused FROM entries WHERE task_id=?', (task['id'],)).fetchall()
        task['total'] = len(entries)
        task['completed'] = sum(e['status'] in ('completed', 'partial', 'failed', 'cancelled') for e in entries)
        task['failed'] = sum(e['status'] == 'failed' for e in entries)
        task['issues'] = sum(bool((json.loads(e['result']) if e['result'] else {}).get('issues')) or ((json.loads(e['result']) if e['result'] else {}).get('field_state') == 'conflict') for e in entries)
        calls = conn.execute('SELECT cache_hit,payload FROM provider_calls WHERE task_id=?', (task['id'],)).fetchall()
        task['cache_hits'] = sum(c['cache_hit'] for c in calls) + sum(e['reused'] for e in entries)
        task['api_calls'] = sum(bool(json.loads(c['payload']).get('external_call')) for c in calls)
        task['mock'] = task['mode'] == 'mock'
        return task

    def tasks(self):
        with self.connect() as conn:
            return [self._task_row(row, conn) for row in conn.execute('SELECT * FROM tasks ORDER BY created_at DESC').fetchall()]

    def task(self, task_id, details=False):
        with self.connect() as conn:
            row = conn.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone()
            result = self._task_row(row, conn)
            if result and details:
                result['entries'] = [self._entry_row(r) for r in conn.execute('SELECT * FROM entries WHERE task_id=? ORDER BY ordinal', (task_id,))]
            return result

    @staticmethod
    def _entry_row(row):
        if row is None: return None
        result = dict(row)
        snapshot = json.loads(result.pop('snapshot'))
        result.update(snapshot)
        result['result'] = json.loads(result['result']) if result['result'] else None
        return result

    def entry(self, entry_id):
        with self.connect() as conn:
            return self._entry_row(conn.execute('SELECT * FROM entries WHERE id=?', (entry_id,)).fetchone())

    def source(self, task_id):
        with self.connect() as conn:
            row = conn.execute('SELECT u.content,u.filename,u.sha256 FROM uploads u JOIN tasks t ON t.upload_id=u.id WHERE t.id=?', (task_id,)).fetchone()
            return dict(row) if row else None

    def recover(self):
        with self.connect() as conn:
            conn.execute("UPDATE entries SET status='cancelled' WHERE status='querying' AND task_id IN (SELECT id FROM tasks WHERE status='cancelled')")
            conn.execute("UPDATE entries SET status='queued',started_at=NULL WHERE status='querying'")
            conn.execute("UPDATE tasks SET status='queued',updated_at=? WHERE status='running'", (now(),))

    def claim(self):
        with self.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row = conn.execute("SELECT e.* FROM entries e JOIN tasks t ON t.id=e.task_id WHERE e.status='queued' AND t.status IN ('queued','running') ORDER BY t.created_at,e.ordinal LIMIT 1").fetchone()
            if not row: return None
            conn.execute("UPDATE entries SET status='querying',started_at=? WHERE id=? AND status='queued'", (now(), row['id']))
            conn.execute("UPDATE tasks SET status='running',updated_at=? WHERE id=?", (now(), row['task_id']))
            return self._entry_row(row)

    def finish(self, entry_id, result=None, error=None, reused=False):
        with self.connect() as conn:
            row = conn.execute('SELECT e.*,t.status AS task_status FROM entries e JOIN tasks t ON t.id=e.task_id WHERE e.id=?', (entry_id,)).fetchone()
            if row is None: return
            if row['task_status'] == 'cancelled': status = 'cancelled'
            elif error and not result: status = 'failed'
            elif result and result.get('provider_errors'): status = 'partial'
            else: status = 'completed'
            conn.execute('UPDATE entries SET status=?,result=?,error=?,reused=?,completed_at=? WHERE id=?', (status, encode(result) if result is not None else None, error, int(reused), now(), entry_id))
            counts = dict(conn.execute('SELECT status,count(*) FROM entries WHERE task_id=? GROUP BY status', (row['task_id'],)).fetchall())
            if not counts.get('queued') and not counts.get('querying') and row['task_status'] != 'cancelled':
                status = 'partial' if counts.get('failed') or counts.get('partial') else 'completed'
                conn.execute('UPDATE tasks SET status=?,updated_at=? WHERE id=?', (status, now(), row['task_id']))

    def cancel(self, task_id):
        with self.connect() as conn:
            conn.execute("UPDATE tasks SET status='cancelled',updated_at=? WHERE id=? AND status IN ('running','queued')", (now(), task_id))
            conn.execute("UPDATE entries SET status='cancelled',completed_at=? WHERE task_id=? AND status='queued'", (now(), task_id))
        return self.task(task_id, True)

    def retry(self, task_id):
        with self.connect() as conn:
            # An in-flight cancellation must settle before the same task can be resumed.
            if conn.execute("SELECT 1 FROM entries WHERE task_id=? AND status='querying'", (task_id,)).fetchone():
                raise ValueError('Wait for the in-flight request to finish before retrying')
            conn.execute("UPDATE entries SET status='queued',error=NULL WHERE task_id=? AND status IN ('failed','partial','cancelled')", (task_id,))
            if conn.execute("SELECT 1 FROM entries WHERE task_id=? AND status='queued'", (task_id,)).fetchone():
                conn.execute("UPDATE tasks SET status='queued',updated_at=? WHERE id=?", (now(), task_id))
        return self.task(task_id, True)

    def has_inflight(self):
        with self.connect() as conn:
            return bool(conn.execute("SELECT 1 FROM entries WHERE status='querying' LIMIT 1").fetchone())

    def add_call(self, task_id, entry_id, payload):
        with self.connect() as conn:
            conn.execute('INSERT INTO provider_calls(task_id,entry_id,created_at,cache_hit,payload) VALUES(?,?,?,?,?)', (task_id, entry_id, now(), int(bool(payload.get('cache_hit'))), encode(payload)))

    def calls(self, task_id, entry_id=None):
        with self.connect() as conn:
            sql = 'SELECT payload FROM provider_calls WHERE task_id=?'
            args = [task_id]
            if entry_id: sql += ' AND entry_id=?'; args.append(entry_id)
            return [json.loads(r[0]) for r in conn.execute(sql + ' ORDER BY id', args)]

    def previous_entries(self, entry, parent_id=None):
        with self.connect() as conn:
            if parent_id:
                rows = conn.execute('SELECT * FROM entries WHERE task_id=? AND result IS NOT NULL', (parent_id,)).fetchall()
            else:
                rows = conn.execute("SELECT e.* FROM entries e JOIN tasks t ON t.id=e.task_id WHERE e.task_id != ? AND e.result IS NOT NULL AND t.mode=(SELECT mode FROM tasks WHERE id=?) ORDER BY t.created_at DESC LIMIT 2000", (entry['task_id'], entry['task_id'])).fetchall()
            return [self._entry_row(row) for row in rows]

    def delete_task(self, task_id):
        with self.connect() as conn:
            row = conn.execute('SELECT upload_id,status FROM tasks WHERE id=?', (task_id,)).fetchone()
            if not row: return False
            if row['status'] in ('queued', 'running') or conn.execute("SELECT 1 FROM entries WHERE task_id=? AND status='querying'", (task_id,)).fetchone():
                raise ValueError('Cancel the task and wait for current requests to finish before deleting')
            conn.execute('DELETE FROM tasks WHERE id=?', (task_id,))
            conn.execute('DELETE FROM uploads WHERE id=?', (row['upload_id'],))
            return True

    def add_manual(self, fingerprint, record):
        with self.connect() as conn:
            conn.execute('INSERT OR REPLACE INTO manual_records VALUES(?,?,?,?)', (record['id'], fingerprint, now(), encode(record)))

    def active_manual_ids(self):
        with self.connect() as conn:
            return {row[0] for row in conn.execute('SELECT id FROM manual_records')}

    def manual(self, fingerprint):
        with self.connect() as conn:
            return [json.loads(r[0]) for r in conn.execute('SELECT record FROM manual_records WHERE fingerprint=?', (fingerprint,))]

class PersistentCache:
    def __init__(self, db, table='query_cache'):
        if table not in ('query_cache', 'verification_cache'): raise ValueError('Invalid cache table')
        self.db, self.table = db, table
        self.column = 'value' if table == 'query_cache' else 'result'

    def get(self, key):
        with self.db.connect() as conn:
            row = conn.execute(f'SELECT {self.column},expires_at FROM {self.table} WHERE key=?', (key,)).fetchone()
            return json.loads(row[0]) if row and row[1] > time.time() else None

    def put(self, key, value, ttl):
        with self.db.connect() as conn:
            conn.execute(f'INSERT OR REPLACE INTO {self.table}(key,{self.column},created_at,expires_at) VALUES(?,?,?,?)', (key, encode(value), now(), time.time() + ttl))

    def clear(self):
        with self.db.connect() as conn:
            count = conn.execute(f'SELECT count(*) FROM {self.table}').fetchone()[0]
            conn.execute(f'DELETE FROM {self.table}')
            return count
