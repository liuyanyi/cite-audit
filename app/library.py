"""Durable source snapshots and conservative, version-separated bibliographic identities."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def _hash(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _now():
    return datetime.now(timezone.utc).isoformat()


def _doi(value):
    return re.sub(r'^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)', '', str(value or '').strip(), flags=re.I).lower()


class Library:
    def __init__(self, db):
        self.db = db

    def archive_response(self, key, envelope):
        # Retrieval timestamps are observations, not a change to source content.
        payload = {name: envelope.get(name) for name in ('data', 'http_status')}
        identity = _hash([key, payload])
        timestamp = _now()
        with self.db.connect() as conn:
            observation = {name: value for name, value in envelope.items() if name not in ('data', 'http_status')}
            conn.execute('INSERT INTO source_response_snapshots VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET last_seen=excluded.last_seen,observation=excluded.observation',
                         (identity, key, _json(payload), timestamp, timestamp, _json(observation)))

    def response(self, key):
        """Read through the permanent source library when the request cache misses."""
        with self.db.connect() as conn:
            row = conn.execute('SELECT payload,observation FROM source_response_snapshots WHERE request_key=? ORDER BY last_seen DESC LIMIT 1', (key,)).fetchone()
        if not row:
            return None
        envelope = {**json.loads(row['payload']), **json.loads(row['observation'])}
        try:
            fresh = datetime.fromisoformat(envelope['expires_at'].replace('Z', '+00:00')) > datetime.now(timezone.utc)
        except (KeyError, TypeError, ValueError):
            fresh = False
        return {'value': envelope, 'state': 'fresh' if fresh else 'stale'}

    def invalidate_responses(self):
        """Explicit cache clearing keeps source contents but forces next-use confirmation."""
        with self.db.connect() as conn:
            conn.execute("UPDATE source_snapshots SET expires_at='1970-01-01T00:00:00+00:00',cache_state='stale'")
            for row in conn.execute('SELECT id,observation FROM source_response_snapshots').fetchall():
                observation = json.loads(row['observation'])
                observation['expires_at'] = '1970-01-01T00:00:00+00:00'
                conn.execute('UPDATE source_response_snapshots SET observation=? WHERE id=?', (_json(observation), row['id']))

    def archive_record(self, record, mode='live'):
        if not record.get('id') or not isinstance(record.get('fields'), dict):
            return None
        mode = 'mock' if mode == 'mock' or record.get('method') == 'mock' else 'live'
        version = str(record.get('version') or 'unknown')
        source_key = _hash([mode, record.get('provider'), record.get('source_id', record['id']), version])
        snapshot_id = _hash([mode, record['id']])
        timestamp = _now()
        # Keep the immutable original payload and separately update observation metadata.
        with self.db.connect() as conn:
            conn.execute('INSERT INTO source_snapshots VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET last_seen=excluded.last_seen,last_checked_at=excluded.last_checked_at,expires_at=excluded.expires_at,cache_state=excluded.cache_state,revalidation_error=excluded.revalidation_error',
                         (snapshot_id, source_key, record.get('provider', ''), mode, version, _json(record), timestamp, timestamp,
                          record.get('last_checked_at', record.get('retrieved_at')), record.get('expires_at'), record.get('cache_state', 'fresh'), _json(record.get('revalidation_error'))))
            # Each newly fetched candidate starts unconfirmed. Old snapshots of a
            # confirmed record remain attached; new content is not auto-promoted.
            identity_key = 'candidate:' + source_key
            item_id = _hash(identity_key)
            existing = conn.execute('SELECT item_id FROM library_sources WHERE snapshot_id=?', (snapshot_id,)).fetchone()
            if not existing:
                conn.execute('INSERT INTO library_items VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(identity_key) DO UPDATE SET fields=excluded.fields,last_seen=excluded.last_seen',
                             (item_id, identity_key, 'candidate', mode, version, _json(record['fields']), '{}', '[]', timestamp, timestamp))
                conn.execute('INSERT OR IGNORE INTO library_sources VALUES(?,?)', (item_id, snapshot_id))
        return snapshot_id

    def ingest(self, entry, result, task):
        records = result.get('evidence', [])
        snapshots = {record['id']: self.archive_record(record, task['mode']) for record in records}
        with self.db.connect() as conn:
            for snapshot_id in snapshots.values():
                if not snapshot_id:
                    continue
                for row in conn.execute('SELECT i.id,i.citation_keys FROM library_items i JOIN library_sources s ON s.item_id=i.id WHERE s.snapshot_id=?', (snapshot_id,)).fetchall():
                    keys = set(json.loads(row['citation_keys']))
                    if entry.get('citation_key'):
                        keys.add(entry['citation_key'])
                    conn.execute('UPDATE library_items SET citation_keys=?,last_seen=? WHERE id=?', (_json(sorted(keys)), _now(), row['id']))
        eligible = result.get('identity_state') == 'unique' and not result.get('provider_errors') and task['mode'] == 'live'
        source_ids = set()
        fields = dict(entry.get('fields', {}))
        for item in result.get('assessments', []):
            if item.get('judgment') in ('consistent', 'correction', 'completion', 'normalization'):
                source_ids.update(item.get('evidence_ids', []))
                if item.get('suggested') is not None:
                    fields[item['field']] = item['suggested']
        selected = [record for record in records if record['id'] in source_ids and record.get('cache_state') != 'stale' and record.get('method') != 'manual']
        identifiers = {}
        is_preprint = bool(entry.get('fields', {}).get('eprint')) or any(record.get('version') == 'preprint' for record in selected)
        version = 'preprint' if is_preprint else 'published'
        if eligible and selected:
            if is_preprint:
                eprint = str(entry.get('fields', {}).get('eprint') or '').strip().lower()
                if not eprint:
                    found = {str(record.get('fields', {}).get('eprint', '')).lower() for record in selected if record.get('fields', {}).get('eprint')}
                    if len(found) == 1:
                        eprint = found.pop()
                if eprint and any(str(record.get('fields', {}).get('eprint', '')).lower() == eprint for record in selected):
                    identifiers['arxiv'] = eprint  # Includes explicit vN; never merges versions.
            elif result.get('doi_state') == 'verified' and _doi(fields.get('doi')):
                identifiers['doi'] = _doi(fields['doi'])
            else:
                acl = [record for record in selected if record.get('provider') == 'acl']
                if acl:
                    identifiers['acl'] = str(acl[0].get('source_id') or '')
        if not identifiers:
            return
        identity_key = _json([task['mode'], version, identifiers])
        item_id, timestamp = _hash(identity_key), _now()
        with self.db.connect() as conn:
            previous = conn.execute('SELECT citation_keys FROM library_items WHERE id=?', (item_id,)).fetchone()
            keys = set(json.loads(previous[0])) if previous else set()
            if entry.get('citation_key'):
                keys.add(entry['citation_key'])
            conn.execute('INSERT INTO library_items VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(identity_key) DO UPDATE SET fields=excluded.fields,citation_keys=excluded.citation_keys,last_seen=excluded.last_seen',
                         (item_id, identity_key, 'confirmed', task['mode'], version, _json(fields), _json(identifiers), _json(sorted(keys)), timestamp, timestamp))
            for record in selected:
                snapshot_id = snapshots[record['id']]
                if not snapshot_id:
                    continue
                conn.execute('DELETE FROM library_sources WHERE snapshot_id=? AND item_id IN (SELECT id FROM library_items WHERE status=\'candidate\')', (snapshot_id,))
                conn.execute('INSERT OR IGNORE INTO library_sources VALUES(?,?)', (item_id, snapshot_id))
            conn.execute("DELETE FROM library_items WHERE status='candidate' AND NOT EXISTS (SELECT 1 FROM library_sources WHERE item_id=library_items.id)")
            decision = {'identity_state': result['identity_state'], 'assessments': result.get('assessments', []), 'source_ids': sorted(source_ids)}
            conn.execute('INSERT OR IGNORE INTO library_decisions(item_id,task_id,entry_id,created_at,payload) VALUES(?,?,?,?,?)',
                         (item_id, task.get('id'), entry.get('id'), timestamp, _json(decision)))

    def backfill(self):
        """Archive existing task evidence once when upgrading an older data directory."""
        with self.db.connect() as conn:
            if conn.execute("SELECT 1 FROM library_meta WHERE key='task_backfill'").fetchone():
                return
            rows = conn.execute("SELECT e.id,e.task_id,e.snapshot,e.result,t.mode FROM entries e JOIN tasks t ON t.id=e.task_id WHERE e.result IS NOT NULL").fetchall()
        for row in rows:
            entry = {**json.loads(row['snapshot']), 'id': row['id']}
            self.ingest(entry, json.loads(row['result']), {'id': row['task_id'], 'mode': row['mode']})
        with self.db.connect() as conn:
            conn.execute("INSERT OR IGNORE INTO library_meta VALUES('task_backfill','1')")

    @staticmethod
    def _item(row):
        item = dict(row)
        for key in ('fields', 'identifiers', 'citation_keys'):
            item[key] = json.loads(item[key])
        return item

    def list(self, q='', mode='all', status='all', limit=100, offset=0):
        clauses, args = [], []
        for name, value in [('mode', mode), ('status', status)]:
            if value != 'all':
                clauses.append(name + '=?'); args.append(value)
        if q.strip():
            literal = '%' + q.strip().lower().replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
            clauses.append("(" + " OR ".join("EXISTS (SELECT 1 FROM json_each(library_items." + name + ") WHERE lower(CAST(value AS TEXT)) LIKE ? ESCAPE '\\')" for name in ('fields', 'citation_keys', 'identifiers')) + ")")
            args.extend([literal] * 3)
        where = ' WHERE ' + ' AND '.join(clauses) if clauses else ''
        with self.db.connect() as conn:
            total = conn.execute('SELECT count(*) FROM library_items' + where, args).fetchone()[0]
            rows = conn.execute('SELECT *, (SELECT count(*) FROM library_sources WHERE item_id=library_items.id) AS snapshot_count FROM library_items' + where + ' ORDER BY last_seen DESC,id LIMIT ? OFFSET ?', [*args, limit, offset]).fetchall()
        return {'items': [self._item(row) for row in rows], 'total': total}

    def detail(self, item_id):
        with self.db.connect() as conn:
            row = conn.execute('SELECT * FROM library_items WHERE id=?', (item_id,)).fetchone()
            if not row:
                return None
            item = self._item(row)
            snapshots = []
            for row in conn.execute('SELECT s.* FROM source_snapshots s JOIN library_sources l ON l.snapshot_id=s.id WHERE l.item_id=? ORDER BY s.last_seen DESC', (item_id,)):
                record = json.loads(row['payload'])
                record.update({name: row[name] for name in ('last_seen', 'last_checked_at', 'expires_at', 'cache_state')})
                record['revalidation_error'] = json.loads(row['revalidation_error'])
                try:
                    if datetime.fromisoformat(str(record['expires_at']).replace('Z', '+00:00')) <= datetime.now(timezone.utc):
                        record['cache_state'] = 'stale'
                except (TypeError, ValueError):
                    record['cache_state'] = 'stale'
                record['snapshot_id'] = row['id']
                snapshots.append(record)
            decisions = []
            for row in conn.execute('SELECT * FROM library_decisions WHERE item_id=? ORDER BY id DESC LIMIT 1000', (item_id,)):
                decisions.append({**json.loads(row['payload']), **{key: row[key] for key in ('task_id', 'entry_id', 'created_at')}})
        return {**item, 'snapshot_count': len(snapshots), 'snapshots': snapshots, 'decisions': decisions}
