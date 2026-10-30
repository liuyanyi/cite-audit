"""HTTP boundary for the new local single-user Cite Audit application."""
from __future__ import annotations

import inspect
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated
from urllib.parse import quote, urlparse

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import __version__, presenters
from .service import AuditService
from .settings import PROVIDERS
from .storage import load


class ExportChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entry_id: str
    proposal_id: str
    fields: list[str] | None = None


class PreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    selections: list[ExportChoice] = Field(default_factory=list, max_length=10000)


class ExportRequest(PreviewRequest):
    confirmed: bool = False
    expected_output_sha256: str


def paginate(items, page=1, page_size=25):
    page, page_size = max(1, page), min(100, max(1, page_size))
    return {"items": items[(page - 1) * page_size:page * page_size], "total": len(items),
            "page": page, "page_size": page_size}


def download(content: bytes, filename: str, content_type="application/x-bibtex"):
    # RFC 5987 handles Unicode filenames; CR/LF/path components never enter headers.
    safe = Path(filename.replace("\\", "/")).name.replace("\r", "").replace("\n", "")
    return Response(content, media_type=content_type,
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(safe)}",
                             "X-Content-Type-Options": "nosniff"})


def public_settings(service):
    raw = service.settings.get(public=True)
    model = dict(raw["model"])
    model["has_api_key"] = model.pop("api_key_configured", False)
    model.setdefault("library", "mmsp")
    model.setdefault("price_source", "mmsp_registry")
    try:
        from .model import price_snapshot
        model["price"] = price_snapshot(service.settings.get()["model"])
    except (ImportError, ValueError, KeyError):
        model["price"] = None
    return {
        "sources": [{"id": p["id"], "name": p["name"], "enabled": p["enabled"], "implemented": True,
                     "requires_key": False, "supports_api_key": p["id"] in {"openalex", "semantic_scholar"}, "has_api_key": p["credential_configured"],
                     "status": "not_tested", "capabilities": ["lookup", "search"]}
                    for p in raw["providers"]],
        "model": model,
        "runtime": {"request_budget": raw["max_task_requests"], "timeout_seconds": raw["request_timeout_seconds"],
                    "max_retries": raw["retries"], "concurrency": 1, "max_upload_bytes": raw["max_upload_bytes"]},
        "cache": {"positive_ttl_seconds": raw["source_cache_ttl_seconds"], "negative_ttl_seconds": 60,
                  "discovery_ttl_seconds": raw["knowledge_ttl_seconds"]},
        "audit": {"enabled": True, "retain_prompt_text": False, "redact_secrets": True},
    }


def update_settings(service, body):
    allowed = {"sources", "model", "runtime", "cache", "audit"}
    if set(body) - allowed:
        raise ValueError("Unknown settings group")
    mapped = {}
    if "sources" in body:
        if not isinstance(body["sources"], list):
            raise ValueError("sources must be a list")
        mapped["source_enabled"], mapped["credentials"] = {}, {}
        for item in body["sources"]:
            if item.get("id") not in PROVIDERS:
                raise ValueError("Unknown source provider")
            if "enabled" in item:
                mapped["source_enabled"][item["id"]] = item["enabled"]
            if "api_key" in item:
                if item["id"] not in {"openalex", "semantic_scholar"}:
                    raise ValueError("This source does not support configured credentials")
                if not isinstance(item["api_key"], str):
                    raise ValueError("API keys must be strings")
                mapped["credentials"][item["id"]] = item["api_key"]
    if "model" in body:
        writable = {"enabled", "protocol", "model", "base_url", "api_key", "timeout_seconds", "max_retries", "max_output_tokens"}
        mapped["model"] = {k: v for k, v in body["model"].items() if k in writable}
        if mapped["model"].get("protocol", "openai-chat") not in {"openai-chat", "openai-responses", "ant-messages"}:
            raise ValueError("Unsupported model protocol")
        if "api_key" in mapped["model"] and not isinstance(mapped["model"]["api_key"], str):
            raise ValueError("Model API key must be a string")
    for group, translations in {
        "runtime": {"request_budget": "max_task_requests", "timeout_seconds": "request_timeout_seconds",
                    "max_retries": "retries", "max_upload_bytes": "max_upload_bytes"},
        "cache": {"positive_ttl_seconds": "source_cache_ttl_seconds", "discovery_ttl_seconds": "knowledge_ttl_seconds"},
    }.items():
        for key, value in body.get(group, {}).items():
            if key in translations:
                mapped[translations[key]] = value
            elif (group, key, value) not in {("runtime", "concurrency", 1), ("cache", "negative_ttl_seconds", 60)}:
                raise ValueError(f"Unsupported configurable setting: {group}.{key}")
    if "audit" in body and body["audit"] != {"enabled": True, "retain_prompt_text": False, "redact_secrets": True}:
        raise ValueError("Audit logging and secret redaction cannot be disabled")
    service.settings.update(mapped)
    return public_settings(service)


def create_app(data_dir: str | Path | None = None, start_worker: bool = True, registry=None) -> FastAPI:
    root = Path(data_dir or os.getenv("CITE_AUDIT_DATA_DIR", ".cite-audit-data"))
    service = AuditService(root, registry=registry)

    @asynccontextmanager
    async def lifespan(app):
        if start_worker:
            await service.start()
        yield
        if start_worker:
            await service.stop()

    app = FastAPI(title="Cite Audit", version=__version__, lifespan=lifespan,
                  description="Local single-user evidence-first bibliography auditor. Demo is not online verification.")
    app.state.service = service
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=os.getenv(
        "CITE_AUDIT_ALLOWED_HOSTS", "localhost,127.0.0.1,[::1],testserver").split(","))

    @app.middleware("http")
    async def local_origin_guard(request: Request, call_next):
        origin = request.headers.get("origin")
        if origin and request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin_host = urlparse(origin).hostname
            if origin_host not in {"localhost", "127.0.0.1", "::1", request.url.hostname}:
                return JSONResponse({"error": {"code": "forbidden_origin", "message": "Untrusted request origin", "details": {}}}, 403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @app.exception_handler(KeyError)
    async def not_found(_request, exc):
        return JSONResponse({"error": {"code": "not_found", "message": str(exc).strip("'"), "details": {}}}, 404)

    @app.exception_handler(ValueError)
    async def invalid(_request, exc):
        return JSONResponse({"error": {"code": "invalid_request", "message": str(exc), "details": {}}}, 400)

    @app.get("/api/health")
    def health():
        return {"status": "ok", "version": __version__, "single_user_local_only": True}

    @app.post("/api/tasks", status_code=201)
    async def upload(file: Annotated[UploadFile, File()], name: str = Form(""), mode: str = Form("demo"), config: str = Form("{}")):
        limit = service.settings.get()["max_upload_bytes"]
        raw = await file.read(limit + 1)
        if len(raw) > limit:
            return JSONResponse({"error": {"code": "upload_too_large", "message": "Bib exceeds upload limit", "details": {}}}, 413)
        try:
            options = json.loads(config)
        except json.JSONDecodeError as exc:
            raise ValueError("config must be a JSON object") from exc
        if not isinstance(options, dict):
            raise ValueError("config must be a JSON object")  # noqa: TRY004 - HTTP validation boundary
        filename = Path((file.filename or "references.bib").replace("\\", "/")).name
        return service.create_task(raw, filename, name, mode, options)

    @app.get("/api/tasks")
    def tasks(q: str = "", execution_status: str = "", sort: str = "created_desc", page: int = 1, page_size: int = 25):
        items = [service.task(r["id"]) for r in service.store.all("SELECT id FROM tasks")]
        items = [i for i in items if q.casefold() in (i["name"] + " " + i["filename"]).casefold()
                 and (not execution_status or i["execution_status"] == execution_status)]
        items.sort(key=lambda i: i["updated_at"] if sort == "updated_desc" else i["created_at"], reverse=sort != "created_asc")
        return paginate(items, page, page_size)

    @app.get("/api/tasks/{task_id}")
    def task(task_id: str):
        return service.task(task_id)

    @app.get("/api/tasks/{task_id}/entries")
    def entries(task_id: str, q: str = "", identity_state: str = "", recommendation_status: str = "",
                has_conflict: bool = False, sort: str = "ordinal", page: int = 1, page_size: int = 25):
        items = service.entries(task_id)
        items = [i for i in items if q.casefold() in (i["citation_key"] + " " + i["title"] + " " + i["author"]).casefold()
                 and (not identity_state or i["identity_state"] == identity_state)
                 and (not recommendation_status or i["recommendation_status"] == recommendation_status)
                 and (not has_conflict or i["has_conflict"])]
        items.sort(key=lambda i: i.get(sort, i["ordinal"]) if sort in {"title", "year"} else i["ordinal"])
        return paginate(items, page, page_size)

    @app.get("/api/tasks/{task_id}/entries/{entry_id}")
    def entry(task_id: str, entry_id: str):
        return service.entry(task_id, entry_id)

    @app.get("/api/tasks/{task_id}/original")
    def original(task_id: str):
        return download(service.original(task_id), service.raw_task(task_id)["filename"])

    @app.get("/api/tasks/{task_id}/report")
    def report(task_id: str):
        return service.freeze(task_id)

    @app.post("/api/tasks/{task_id}/cancel")
    def cancel(task_id: str):
        return service.cancel(task_id)

    @app.post("/api/tasks/{task_id}/retry", status_code=201)
    def retry(task_id: str):
        return service.retry(task_id)

    @app.delete("/api/tasks/{task_id}")
    def delete(task_id: str):
        return service.delete(task_id)

    @app.post("/api/tasks/{task_id}/export-preview")
    def export_preview(task_id: str, body: PreviewRequest):
        return service.export_preview(task_id, [s.model_dump() for s in body.selections])[0]

    @app.post("/api/tasks/{task_id}/exports", status_code=201)
    def create_export(task_id: str, body: ExportRequest):
        return service.create_export(task_id, [s.model_dump() for s in body.selections], body.confirmed, body.expected_output_sha256)

    def export_row(task_id, export_id):
        row = service.store.one("SELECT * FROM exports WHERE id=? AND task_id=?", (export_id, task_id))
        if not row:
            raise KeyError("Export not found")
        return row

    @app.get("/api/tasks/{task_id}/exports/{export_id}/download")
    def export_download(task_id: str, export_id: str):
        row = export_row(task_id, export_id)
        return download(row["content"], load(row["changes"])["filename"])

    @app.get("/api/tasks/{task_id}/exports/{export_id}/report")
    def export_report(task_id: str, export_id: str):
        return load(export_row(task_id, export_id)["changes"])

    @app.get("/api/articles")
    def articles(q: str = "", mode: str = "", page: int = 1, page_size: int = 25):
        items = []
        for row in service.store.all("SELECT id FROM articles ORDER BY created_at DESC"):
            art = service.knowledge.article(row["id"])
            searchable = " ".join([art["title"], art["author"], *[str(v["identifiers"]) for v in art["versions"]]])
            if q.casefold() not in searchable.casefold():
                continue
            item = presenters.article(art, detail=False)
            if not mode or item["mode"] == mode:
                items.append(item)
        return paginate(items, page, page_size)

    @app.get("/api/articles/{article_id}")
    def article(article_id: str):
        item = service.knowledge.article(article_id)
        if not item:
            raise KeyError("Article not found")
        return presenters.article(item)

    @app.get("/api/versions/{version_id}")
    def version(version_id: str):
        item = service.knowledge.version(version_id)
        if not item:
            raise KeyError("Version not found")
        return presenters.version(item)

    @app.get("/api/sources/{source_id}")
    def source(source_id: str):
        item = service.knowledge.source(source_id)
        if not item:
            raise KeyError("Source not found")
        return presenters.source(item)

    @app.get("/api/snapshots/{snapshot_id}")
    def snapshot(snapshot_id: str):
        row = service.store.one("SELECT * FROM snapshots WHERE id=?", (snapshot_id,))
        if not row:
            raise KeyError("Snapshot not found")
        src = service.store.one("SELECT namespace FROM sources WHERE snapshot_id=? LIMIT 1", (snapshot_id,))
        return {"id": row["id"], "sha256": row["hash"], "fetched_at": row["fetched_at"],
                "content_type": "application/json", "body": row["content"].decode("utf-8", errors="replace"),
                "mode": "demo" if src and src["namespace"] == "mock" else "online"}

    @app.get("/api/settings")
    def settings():
        return public_settings(service)

    @app.put("/api/settings")
    def put_settings(body: dict):
        return update_settings(service, body)

    @app.get("/api/models/catalog")
    def model_catalog():
        from .model import model_catalog
        return {"items": model_catalog(), "source": "mmsp_static_registry", "network_requested": False}

    @app.post("/api/settings/model/test")
    async def test_model(body: dict):
        if body.get("confirmed") is not True:
            raise ValueError("Explicit confirmation required: model tests can incur charges")
        from .model import call_model
        config = dict(service.settings.get()["model"])
        config["enabled"] = True
        def on_call(item):
            service.call(None, None, {**item, "kind": "model", "provider": item.get("provider", "mmsp")}, "online")
        return await call_model(config, "Reply with exactly: OK", on_call=on_call)

    @app.post("/api/settings/sources/{provider}/test")
    async def test_source(provider: str):
        if provider not in PROVIDERS:
            raise KeyError("Source provider not found")
        config = service.settings.get()
        config["source_enabled"] = {p: p == provider for p in PROVIDERS}
        config["max_source_requests"] = 2
        config["retries"] = 0
        calls = []
        async def audit(item):
            calls.append(service.call(None, None, item, "online"))
        records = await service.registry.lookup({"doi": "10.18653/v1/N19-1423", "eprint": "1810.04805",
                                                 "title": "BERT: Pre-training of Deep Bidirectional Transformers for Language Understanding"},
                                                "online", config, audit=audit, cancelled=lambda: False)
        return {"provider": provider, "status": "available" if records else "unavailable", "calls": calls,
                "records_received": len(records), "knowledge_written": False}

    @app.get("/api/calls")
    def calls(task_id: str = "", entry_id: str = "", provider: str = "", status: str = "", page: int = 1, page_size: int = 25):
        items = [c for c in service.calls(task_id, entry_id) if (not provider or c["provider"] == provider)
                 and (not status or c["status"] == status)]
        return paginate(list(reversed(items)), page, page_size)

    @app.delete("/api/query-cache")
    async def clear_cache():
        count = service.registry.clear_cache()
        if inspect.isawaitable(count):
            count = await count
        return {"cleared": True, "count": count, "knowledge_preserved": True, "history_preserved": True}

    static = Path(__file__).parent / "static"
    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path == "api" or path.startswith("api/"):
            raise KeyError("API route not found")
        target = (static / path).resolve()
        if static.resolve() in target.parents and target.is_file():
            return FileResponse(target)
        index = static / "index.html"
        if index.is_file():
            return FileResponse(index)
        return JSONResponse({"message": "Frontend assets not built. Run npm run build in frontend; API documentation: /docs"}, 404)

    return app


app = create_app()
