"""Local-only HTTP interface for the citation workbench."""
from __future__ import annotations
from contextlib import asynccontextmanager
import copy
import json
import os
from pathlib import Path
from urllib.parse import quote, urlsplit
from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .bibtex import BibTeXError, parse_bibtex
from .config import Config, load_env
from .manual import manual_record
from .library import Library
from .providers import ProviderService
from .storage import Database, PersistentCache
from .worker import Worker, evidence_fresh

MAX_UPLOAD = 2 * 1024 * 1024
MAX_ENTRIES = 1000


def create_app(data_dir=None, start_worker=True):
    load_env(Path.cwd() / '.env')
    directory = Path(data_dir or os.getenv('CITE_AUDIT_DATA_DIR', str(Path.home() / '.cite-audit'))).expanduser()
    db, config = Database(directory), Config(directory)
    library = Library(db)
    library.backfill()
    worker = Worker(db, config)

    @asynccontextmanager
    async def lifespan(app):
        if start_worker: worker.start()
        yield
        if start_worker: worker.stop()

    app = FastAPI(title='cite-audit', version='0.1.0', lifespan=lifespan, docs_url='/api/docs', redoc_url=None)
    app.state.db, app.state.config, app.state.worker = db, config, worker
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=['127.0.0.1', 'localhost', '[::1]', 'testserver'])

    @app.middleware('http')
    async def local_security(request: Request, call_next):
        if request.method not in ('GET', 'HEAD', 'OPTIONS'):
            origin = request.headers.get('origin')
            if origin:
                parsed = urlsplit(origin)
                if parsed.scheme not in ('http', 'https') or parsed.netloc != request.headers.get('host') or parsed.path or parsed.query or parsed.fragment:
                    return JSONResponse({'detail':'Cross-origin writes are disabled'}, status_code=403)
            if request.headers.get('sec-fetch-site') == 'cross-site':
                return JSONResponse({'detail':'Cross-site writes are disabled'}, status_code=403)
            try:
                if int(request.headers.get('content-length', '0')) > MAX_UPLOAD + 100_000:
                    return JSONResponse({'detail':'Request exceeds upload limit'}, status_code=413)
            except ValueError:
                return JSONResponse({'detail':'Invalid content length'}, status_code=400)
            chunks, size = [], 0
            async for chunk in request.stream():
                size += len(chunk)
                if size > MAX_UPLOAD + 100_000:
                    return JSONResponse({'detail':'Request exceeds upload limit'}, status_code=413)
                chunks.append(chunk)
            request._body = b''.join(chunks)
        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; form-action 'self'; base-uri 'none'"
        response.headers['Cache-Control'] = 'no-store'
        return response

    async def json_object(request):
        try: body = await request.json()
        except (ValueError, UnicodeDecodeError): raise HTTPException(422, 'Request body must be valid JSON')
        if not isinstance(body, dict): raise HTTPException(422, 'Request body must be an object')
        return body

    def require_task(task_id, details=False):
        task = db.task(task_id, details)
        if not task: raise HTTPException(404, 'Task not found')
        return task

    def require_entry(entry_id):
        entry = db.entry(entry_id)
        if not entry: raise HTTPException(404, 'Entry not found')
        return entry

    def spawn_subset(task, entries, options):
        source = db.source(task['id'])
        snapshots = [{k:v for k,v in e.items() if k not in ('id','task_id','ordinal','status','result','error','reused','started_at','completed_at')} for e in entries]
        model_id = task.get('options', {}).get('model_id')
        try:
            options = {**options, 'model_id': config.get(model_id)['llm']['model_config_id']}
            fingerprint = config.fingerprint(task['mode'], model_id)
        except ValueError as exc: raise HTTPException(422, str(exc))
        new = db.create_task(source['filename'], source['content'], {'entries':snapshots,'issues':[]}, task['mode'], task['cnki_mode'], fingerprint, task['id'], options)
        worker.wake()
        return config.redact(new)

    @app.get('/api/health')
    def health(): return {'ok':True,'version':'0.1.0','mode':config.get()['mode'],'local_only':True}

    @app.get('/api/tasks')
    def tasks(): return config.redact({'tasks':db.tasks()})

    @app.post('/api/tasks', status_code=201)
    async def upload(file: UploadFile = File(...), mode: str = Form('mock'), cnki_mode: str = Form('off'), parent_id: str | None = Form(None), model_id: str | None = Form(None)):
        if mode not in ('mock', 'live'): raise HTTPException(422, 'Mode must be mock or live')
        if cnki_mode not in ('off', 'chinese', 'all'): raise HTTPException(422, 'CNKI mode must be off, chinese or all')
        filename = Path((file.filename or 'references.bib').replace('\\', '/')).name[:200]
        if not filename.lower().endswith('.bib'): raise HTTPException(422, 'Upload a .bib file')
        content = await file.read(MAX_UPLOAD + 1)
        if len(content) > MAX_UPLOAD: raise HTTPException(413, 'BibTeX file exceeds 2 MiB')
        try: text = content.decode('utf-8-sig')
        except UnicodeDecodeError: raise HTTPException(422, 'Save BibTeX as UTF-8 and upload again; original bytes were not changed')
        try: parsed = parse_bibtex(text)
        except BibTeXError as exc: raise HTTPException(413, str(exc))
        if len(parsed['entries']) > MAX_ENTRIES: raise HTTPException(413, 'Maximum 1000 entries per upload')
        if not parsed['entries'] and not parsed['issues']: parsed['issues'].append('No bibliographic entries found')
        try:
            selected = config.get(model_id or None)['llm']['model_config_id']
            task = db.create_task(filename, content, parsed, mode, cnki_mode, config.fingerprint(mode, selected), parent_id or None, {'model_id': selected})
        except ValueError as exc: raise HTTPException(422, str(exc))
        worker.wake()
        return config.redact(task)

    @app.get('/api/tasks/{task_id}')
    def task_detail(task_id: str): return config.redact(require_task(task_id, True))

    @app.get('/api/entries/{entry_id}')
    def entry_detail(entry_id: str): return config.redact(require_entry(entry_id))

    @app.post('/api/tasks/{task_id}/cancel')
    def cancel(task_id: str):
        require_task(task_id)
        return config.redact(db.cancel(task_id))

    @app.post('/api/tasks/{task_id}/retry')
    def retry(task_id: str):
        task = require_task(task_id, True)
        if any(e['status'] == 'querying' for e in task['entries']): raise HTTPException(409, 'Wait for the in-flight request to finish before retrying')
        try: current_hash = config.fingerprint(task['mode'], task.get('options', {}).get('model_id'))
        except ValueError as exc: raise HTTPException(422, str(exc))
        if task['config_hash'] != current_hash:
            entries = [e for e in task['entries'] if e['status'] in ('failed','partial','cancelled')]
            if entries: return spawn_subset(task, entries, {})
        try: result = db.retry(task_id)
        except ValueError as exc: raise HTTPException(409, str(exc))
        worker.wake()
        return config.redact(result)

    @app.get('/api/library')
    def list_library(q: str = Query('', max_length=500), mode: str = 'all', status: str = 'all',
                     limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0)):
        if mode not in ('all', 'live', 'mock') or status not in ('all', 'candidate', 'confirmed'):
            raise HTTPException(422, 'Invalid library filter')
        return config.redact(library.list(q, mode, status, limit, offset))

    @app.get('/api/library/{item_id}')
    def library_detail(item_id: str):
        item = library.detail(item_id)
        if not item:
            raise HTTPException(404, 'Library item not found')
        return config.redact(item)

    @app.post('/api/tasks/{task_id}/refresh-stale')
    def refresh_stale(task_id: str):
        task = require_task(task_id, True)
        entries = [entry for entry in task['entries'] if entry.get('result') and (entry['result'].get('provider_errors') or not evidence_fresh(entry['result']))]
        if not entries: return {'id':task_id, 'unchanged':True, 'message':'No expired results'}
        return spawn_subset(task, entries, {'revalidate':True})

    @app.post('/api/entries/{entry_id}/refresh')
    async def refresh(entry_id: str, request: Request):
        entry = require_entry(entry_id)
        body = await json_object(request)
        sources = body.get('sources', ['all'])
        allowed = {'all','crossref','arxiv','semantic_scholar','deepseek','cnki','llm','doi','datacite','acl'}
        if not isinstance(sources, list) or not sources or any(not isinstance(s, str) or s not in allowed for s in sources): raise HTTPException(422, 'Invalid refresh sources')
        task = require_task(entry['task_id'])
        options = {'force':True, 'sources':sources}
        if 'all' not in sources:
            options['seed_records'] = [r for r in (entry.get('result') or {}).get('evidence', []) if r.get('provider') not in sources]
        return spawn_subset(task, [entry], options)

    @app.get('/api/tasks/{task_id}/source')
    def download_source(task_id: str):
        require_task(task_id)
        source = db.source(task_id)
        return Response(source['content'], media_type='application/x-bibtex', headers={'Content-Disposition':"attachment; filename*=UTF-8''" + quote(source['filename']), 'X-Content-SHA256':source['sha256']})

    @app.get('/api/tasks/{task_id}/report')
    def download_report(task_id: str):
        task = require_task(task_id, True)
        task['provider_calls'] = db.calls(task_id)
        task['audit'] = db.audit(task_id=task_id, limit=1000)
        return Response(json.dumps(config.redact(task), ensure_ascii=False, indent=2), media_type='application/json', headers={'Content-Disposition':f'attachment; filename="cite-audit-{task_id[:8]}.json"'})

    @app.delete('/api/tasks/{task_id}')
    def delete_task(task_id: str):
        require_task(task_id)
        try: db.delete_task(task_id)
        except ValueError as exc: raise HTTPException(409, str(exc))
        return {'deleted':True, 'message':'Task and its original upload removed. Shared caches, manual imports and audit/cost history remain.'}

    @app.get('/api/settings')
    def settings(): return config.public()

    @app.put('/api/settings')
    async def update_settings(request: Request):
        if db.has_inflight() or any(t['status'] in ('queued','running') for t in db.tasks()): raise HTTPException(409, 'Pause changes until active tasks finish or are cancelled')
        try: return config.update(await json_object(request))
        except ValueError as exc: raise HTTPException(422, str(exc))

    @app.post('/api/settings/test/{provider}')
    def connection_test(provider: str):
        if provider not in ('llm','deepseek','semantic_scholar','crossref','arxiv'): raise HTTPException(422, 'Unknown provider')
        settings = config.get()
        # Connection tests are an explicit live request, never a mock success.
        settings['mode'] = 'live'
        service = ProviderService(settings, PersistentCache(db), call_hook=lambda call: db.add_audit_call(config.redact({**call, 'scope': 'connection_test'})))
        try: return config.redact(service.test_connection(provider))
        except Exception as exc: return {'ok':False,'status':'error','message':f'Connection test failed ({type(exc).__name__})'}
        finally: service.close()

    @app.post('/api/settings/test/model/{model_id}')
    def model_connection_test(model_id: str):
        try: settings = config.get(model_id)
        except ValueError as exc: raise HTTPException(422, str(exc))
        settings['mode'] = 'live'
        service = ProviderService(settings, PersistentCache(db),
                                  call_hook=lambda call: db.add_audit_call(config.redact({**call, 'scope': 'connection_test'})))
        try: return config.redact(service.test_connection('llm'))
        except Exception as exc: return {'ok': False, 'status': 'error', 'message': f'Connection test failed ({type(exc).__name__})'}
        finally: service.close()

    @app.get('/api/models/catalog')
    def model_catalog():
        from .llm import catalog
        return catalog()

    @app.get('/api/audit')
    def audit(task_id: str | None = None, entry_id: str | None = None, provider_id: str | None = None,
              limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0)):
        return config.redact(db.audit(task_id, entry_id, provider_id, limit, offset))

    @app.post('/api/cache/clear')
    def clear_cache():
        if db.has_inflight() or any(t['status'] in ('queued','running') for t in db.tasks()): raise HTTPException(409, 'Wait for active tasks or cancel them before clearing caches')
        count = PersistentCache(db).clear() + PersistentCache(db, 'verification_cache').clear()
        return {'deleted':count,'message':'Query and verification caches cleared; historical evidence snapshots and manual records preserved'}

    @app.delete('/api/manual-records')
    def clear_manual():
        with db.connect() as conn:
            count = conn.execute('SELECT count(*) FROM manual_records').fetchone()[0]
            conn.execute('DELETE FROM manual_records')
        PersistentCache(db, 'verification_cache').clear()
        return {'deleted':count,'message':'Manual records removed from future checks. Existing task evidence snapshots remain until their task is deleted.'}

    @app.post('/api/entries/{entry_id}/manual', status_code=201)
    async def import_manual(entry_id: str, request: Request):
        entry = require_entry(entry_id)
        body = await json_object(request)
        try: record = manual_record(body.get('text',''), body.get('source_url',''), body.get('format','text'))
        except (ValueError, TypeError) as exc: raise HTTPException(422, str(exc))
        db.add_manual(entry['fingerprint'], record)
        task = require_task(entry['task_id'])
        records = (entry.get('result') or {}).get('evidence', [])
        return spawn_subset(task, [entry], {'manual_only':True,'seed_records':records})

    from .bib_export import register_export_routes
    register_export_routes(app, db, config)

    static = Path(__file__).parent / 'static'
    app.mount('/static', StaticFiles(directory=static), name='static')

    @app.get('/examples/demo.bib')
    def demo_file():
        sample = Path(__file__).parent / 'static' / 'demo.bib'
        return FileResponse(sample, media_type='application/x-bibtex', filename='demo.bib')

    @app.get('/')
    def index(): return FileResponse(static / 'index.html')

    return app
