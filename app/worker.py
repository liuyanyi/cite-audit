"""Single-instance durable worker. Cancellation stops before the next request."""
from __future__ import annotations
import copy
from datetime import datetime, timezone
import hashlib
import json
import re
import threading
import time
import unicodedata

from .audit import assess, RULE_VERSION
from .locking import InstanceLock
from .bibtex import render_suggestion
from .providers import ProviderService
from .storage import PersistentCache, now


def canonical(value):
    return re.sub(r'[^\w]', '', unicodedata.normalize('NFKC', str(value)).casefold())

def doi(value):
    return re.sub(r'^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)', '', str(value).strip(), flags=re.I).lower()

def evidence_fresh(result):
    if result.get('provider_errors'): return False
    if result.get('expires_at'):
        try:
            if datetime.fromisoformat(result['expires_at'].replace('Z', '+00:00')) <= datetime.now(timezone.utc): return False
        except (TypeError, ValueError): return False
        if not result.get('evidence'): return True
    records = result.get('evidence', [])
    if not records: return False
    for record in records:
        if record.get('method') == 'manual': continue
        try:
            expires = datetime.fromisoformat(record['expires_at'].replace('Z', '+00:00'))
            if expires <= datetime.now(timezone.utc): return False
        except (KeyError, ValueError, TypeError): return False
    return True

class Worker:
    def __init__(self, db, config):
        self.db, self.config = db, config
        self.cache = PersistentCache(db)
        self.verifications = PersistentCache(db, 'verification_cache')
        self.stop_event = threading.Event()
        self.wake_event = threading.Event()
        self.thread = None
        self.lock_file = None

    def start(self):
        # Multiple uvicorn workers would violate the single-instance rate limiter.
        self.lock_file = InstanceLock(self.db.directory / 'worker.lock').acquire()
        self.db.recover()
        self.thread = threading.Thread(target=self.loop, name='citation-worker', daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        self.wake_event.set()
        if self.thread: self.thread.join(timeout=150)
        if self.lock_file and (not self.thread or not self.thread.is_alive()): self.lock_file.release()

    def wake(self): self.wake_event.set()

    def loop(self):
        while not self.stop_event.is_set():
            entry = self.db.claim()
            if entry:
                self.run_entry(entry)
            else:
                self.wake_event.wait(1)
                self.wake_event.clear()

    def run_next(self):
        entry = self.db.claim()
        if entry: self.run_entry(entry)
        return bool(entry)

    def _previous(self, entry, task):
        for prior in self.db.previous_entries(entry, task.get('parent_id')):
            source_task = self.db.task(prior['task_id'])
            if source_task['mode'] != task['mode'] or source_task['config_hash'] != task['config_hash']: continue
            result = copy.deepcopy(prior.get('result') or {})
            active_manual = self.db.active_manual_ids()
            old_evidence = result.get('evidence', [])
            result['evidence'] = [r for r in old_evidence if r.get('method') != 'manual' or r['id'] in active_manual]
            if old_evidence and not result['evidence']: continue
            prior['result'] = result
            if result.get('provider_errors') or not evidence_fresh(result): continue
            a, b = entry['fields'], prior['fields']
            if not a.get('title') or not b.get('title'): continue
            same_input_identity = (canonical(a.get('title')) == canonical(b.get('title'))
                                   and canonical(a.get('author', '')) == canonical(b.get('author', '')))
            # Reuse existing evidence after filling/correcting a title or author if
            # deterministic assessment establishes the new identity uniquely.
            if not same_input_identity:
                reassessment = assess(entry, result.get('evidence', []), [])
                if reassessment.get('identity_state') != 'unique': continue
            if doi(a.get('doi', '')) != doi(b.get('doi', '')):
                confirmed = {doi(r.get('fields', {}).get('doi', '')) for r in result.get('evidence', [])}
                if a.get('doi') and doi(a['doi']) not in confirmed: continue
            # Changing an arXiv version or work identifier needs new identifier evidence.
            if canonical(a.get('eprint', '')) != canonical(b.get('eprint', '')): continue
            return prior, source_task
        return None, None

    def run_entry(self, entry):
        task = self.db.task(entry['task_id'])
        try:
            model_id = task.get('options', {}).get('model_id')
            fingerprint = self.config.fingerprint(task['mode'], model_id)
            settings = self.config.get(model_id)
        except ValueError:
            self.db.finish(entry['id'], error='The selected model is no longer configured. Retry after selecting an available model.')
            return
        if task['config_hash'] != fingerprint:
            self.db.finish(entry['id'], error='Provider configuration changed since this task was created. Retry to create a new task with the current configuration.')
            return
        settings['mode'] = task['mode']
        options = task.get('options', {})
        result_key = hashlib.sha256(json.dumps([entry['fingerprint'], task['config_hash'], task['cnki_mode']], sort_keys=True).encode()).hexdigest()
        force = bool(options.get('force'))
        try:
            records, errors, model_suggestions, reused = [], [], [], False
            cached = None if force or options.get('revalidate') else self.verifications.get(result_key)
            prior, prior_task = (None, None) if force or options.get('revalidate') else self._previous(entry, task)
            sources = options.get('sources')
            if options.get('seed_records'):
                records = options['seed_records']
                reused = bool(options.get('manual_only'))
            elif cached:
                records = cached.get('evidence', [])
                model_suggestions = cached.get('model_suggestions', [])
                reused = True
            elif prior:
                records = prior['result'].get('evidence', [])
                model_suggestions = prior['result'].get('model_suggestions', [])
                reused = True
                if task['cnki_mode'] != 'off' and prior_task['cnki_mode'] != task['cnki_mode']:
                    sources = ['cnki']
                    reused = False  # Existing sources are reused; only the new branch is queried.
            manual = self.db.manual(entry['fingerprint'])
            if options.get('manual_only'): reused = True
            if not reused:
                def budget():
                    current = self.db.task(task['id'])
                    if self.stop_event.is_set() or not current or current['status'] == 'cancelled': return False
                    calls = self.db.calls(task['id'])
                    entry_calls = self.db.calls(task['id'], entry['id'])
                    return (sum(bool(c.get('external_call')) for c in calls) < settings['budget']['per_task']
                            and sum(bool(c.get('external_call')) for c in entry_calls) < settings['budget']['per_entry'])
                def hook(call): self.db.add_call(task['id'], entry['id'], self.config.redact(call))
                service = ProviderService(settings, self.cache, call_hook=hook)
                try:
                    collected = service.collect(entry, cnki_mode=task['cnki_mode'], force=force, budget=budget, sources=sources)
                finally:
                    service.close()
                records += collected.get('records', [])
                errors = collected.get('errors', [])
                model_suggestions = collected.get('model_suggestions', [])
            active_manual = self.db.active_manual_ids()
            records = [r for r in records if r.get('method') != 'manual' or r['id'] in active_manual]
            by_id = {r['id']: r for r in records + manual}
            model_suggestions = [s for s in model_suggestions if all(ref in by_id for ref in s.get('evidence_ids', []))]
            records = list(by_id.values())
            fresh_records = [record for record in records if record.get('cache_state') != 'stale']
            model_suggestions = [item for item in model_suggestions if all(ref in {record['id'] for record in fresh_records} for ref in item.get('evidence_ids', []))]
            result = assess(entry, fresh_records, errors)
            result['evidence'] = records
            if len(fresh_records) != len(records):
                result['issues'].append('Historical source evidence was retained after failed revalidation and is not used for new corrections.')
                if not result.get('accepted_changes') and not result.get('suggested_entry_type'):
                    result['suggested_bibtex'] = None
            result['provider_errors'] = errors
            result['freshness_state'] = 'stale' if any(record.get('cache_state') == 'stale' for record in records) else 'fresh'
            result['model_suggestions'] = model_suggestions
            result['calls'] = self.db.calls(task['id'], entry['id'])
            result['mode'] = task['mode']
            result['mock'] = task['mode'] == 'mock'
            result['cache_source'] = 'verification' if cached else 'previous_evidence' if prior else 'manual' if options.get('manual_only') else 'providers'
            result['checked_at'] = now()
            result['rules_version'] = RULE_VERSION
            result['manual_records'] = sum(r.get('method') == 'manual' for r in records)
            # A result is no fresher than *any* supporting request, including
            # negative publication/DOI searches. Reuploading must not extend TTLs.
            expiries = []
            for item in records + result['calls'] + ([cached] if cached else []) + ([prior['result']] if prior else []):
                if not item.get('expires_at'): continue
                try: expiries.append(datetime.fromisoformat(item['expires_at'].replace('Z', '+00:00')).timestamp())
                except (KeyError, ValueError, TypeError): pass
            negative = (result.get('identity_state') in ('not_found', 'insufficient', 'multiple')
                        or result.get('doi_state') in ('not_found', 'missing', 'unverified')
                        or result.get('publication_state') in ('not_found', 'unclear'))
            lifetime = settings['cache']['negative_days' if negative else 'metadata_days'] * 86400
            expiries.append(time.time() + lifetime)
            expires = min(expiries) if not errors else time.time()
            result['expires_at'] = datetime.fromtimestamp(expires, timezone.utc).isoformat()
            result = self.config.redact(result)
            from .library import Library
            Library(self.db).ingest(entry, result, task)
            self.db.finish(entry['id'], result, reused=reused)
            current_task = self.db.task(task['id'])
            ttl = expires - time.time()
            if ttl > 0 and not errors and current_task and current_task['status'] != 'cancelled' and not options.get('manual_only'):
                self.verifications.put(result_key, result, ttl)
        except Exception as exc:
            # Never surface raw transport exception or upstream response bodies.
            self.db.finish(entry['id'], error=f'Entry processing failed ({type(exc).__name__}). Retry after reviewing provider configuration.')
