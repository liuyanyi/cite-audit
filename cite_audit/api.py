"""HTTP boundary for the new local single-user Cite Audit application."""
from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated
from urllib.parse import quote, urlparse

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import __version__, presenters
from .policy import PROVIDERS
from .service import AuditService, BudgetConfirmationRequired
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


def download(content: bytes, filename: str, content_type="application/x-bibtex"):
    # RFC 5987 handles Unicode filenames; CR/LF/path components never enter headers.
    safe = Path(filename.replace("\\", "/")).name.replace("\r", "").replace("\n", "")
    return Response(content, media_type=content_type,
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(safe)}",
                             "X-Content-Type-Options": "nosniff"})


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

    @app.exception_handler(BudgetConfirmationRequired)
    async def budget_confirmation(_request, exc):
        return JSONResponse({"error": {"code": "budget_confirmation_required", "message": str(exc),
                                       "details": exc.details}}, 409)

    @app.get("/api/health")
    def health():
        return {"status": "ok", "version": __version__, "single_user_local_only": True}

    @app.post("/api/tasks", status_code=201)
    async def upload(file: Annotated[UploadFile, File()], name: str = Form(""), mode: str = Form("demo"),
                     config: str = Form("{}"), confirm_budget: bool = Form(False)):
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
        return service.create_task(raw, filename, name, mode, options, confirm_budget=confirm_budget)

    @app.get("/api/tasks")
    def tasks(q: str = "", execution_status: str = "", sort: str = "created_desc", page: int = 1, page_size: int = 25):
        return service.task_page(q, execution_status, sort, page, page_size)

    @app.get("/api/events")
    async def events(request: Request, task_id: str = "", after: int | None = None):
        if task_id:
            service.raw_task(task_id)
        raw_cursor = request.headers.get("last-event-id")
        try:
            cursor = int(raw_cursor) if raw_cursor is not None else after
        except ValueError:
            raise ValueError("Invalid event cursor") from None
        if cursor is not None and not 0 <= cursor <= 9223372036854775807:
            raise ValueError("Invalid event cursor")
        return StreamingResponse(service.events.stream(cursor, task_id or None), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.get("/api/tasks/{task_id}")
    def task(task_id: str):
        return service.task(task_id)

    @app.get("/api/tasks/{task_id}/entries")
    def entries(task_id: str, q: str = "", identity_state: str = "", recommendation_status: str = "",
                has_conflict: bool = False, sort: str = "ordinal", page: int = 1, page_size: int = 25,
                classification: str = ""):
        return service.entry_page(task_id, q, identity_state, recommendation_status, has_conflict, sort, page, page_size,
                                  classification)

    @app.get("/api/tasks/{task_id}/entries/{entry_id}")
    def entry(task_id: str, entry_id: str):
        return service.entry(task_id, entry_id)

    @app.post("/api/tasks/{task_id}/entries/{entry_id}/retry")
    def retry_entry(task_id: str, entry_id: str):
        return service.retry_entry(task_id, entry_id)

    @app.get("/api/tasks/{task_id}/original")
    def original(task_id: str):
        return download(service.original(task_id), service.raw_task(task_id)["filename"])

    @app.get("/api/tasks/{task_id}/report")
    def report(task_id: str):
        return service.freeze(task_id)

    @app.get("/api/tasks/{task_id}/reports")
    def report_history(task_id: str):
        return service.report_history(task_id)

    @app.get("/api/tasks/{task_id}/reports/{revision}")
    def historical_report(task_id: str, revision: int):
        return service.historical_report(task_id, revision)

    @app.post("/api/tasks/{task_id}/cancel")
    def cancel(task_id: str):
        return service.cancel(task_id)

    @app.post("/api/tasks/{task_id}/retry")
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
        result = service.knowledge.articles(q, mode, page, page_size)
        result["items"] = [presenters.article(item, detail=False) for item in result["items"]]
        return result

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
        return service.settings.public()

    @app.put("/api/settings")
    def put_settings(body: dict):
        return service.settings.update_public(body)

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
        config["force_refresh"] = True
        calls = []
        async def audit(item):
            calls.append(service.call(None, None, item, "online"))
        # Test an official record within this provider's coverage, not whether
        # every provider can discover the same paper (BERT is not in PMLR).
        fields = ({"url": "https://proceedings.iclr.cc/paper_files/paper/2025/hash/"
                          "5503a7c69d48a2f86fc00b3dc09de686-Abstract-Conference.html"} if provider == "iclr" else
                  {"pmlr": "v139/radford21a"} if provider == "pmlr" else
                  {"url": "https://openreview.net/forum?id=v8L0pN6EOi"} if provider == "openreview" else
                  {"url": "https://openreview.net/forum?id=FPVCb0WMuN"} if provider == "tmlr" else
                  {"doi": "10.18653/v1/N19-1423", "eprint": "1810.04805",
                   "title": "BERT: Pre-training of Deep Bidirectional Transformers for Language Understanding"})
        records = await service.registry.lookup(fields, "online", config, audit=audit, cancelled=lambda: False)
        statuses = {call["status"] for call in calls}
        if records:
            status, message = "available", "可访问，已取得测试记录。"
        elif "rate_limited" in statuses:
            status, message = "rate_limited", "来源限流，请稍后重试。"
        elif "failed" in statuses:
            status, message = "unavailable", "连接或响应处理失败，请查看调用记录。"
        elif (statuses & {"partial", "budget_exhausted", "cancelled"}
              or not any(call["request_count"] or call["cache_hit"] for call in calls)):
            status, message = "inconclusive", "测试未完成，无法判断连接状态，请查看调用记录。"
        else:
            status, message = "no_result", "可访问，但测试样例未命中；不代表文献不存在。"
        return {"provider": provider, "status": status, "message": message, "calls": calls,
                "records_received": len(records), "knowledge_written": False}

    @app.get("/api/calls")
    def calls(task_id: str = "", entry_id: str = "", provider: str = "", status: str = "", page: int = 1, page_size: int = 25):
        return service.call_page(task_id, entry_id, provider, status, page, page_size)

    @app.get("/api/calls/{call_id}")
    def call_detail(call_id: str):
        return service.call_detail(call_id)

    @app.delete("/api/query-cache")
    def clear_cache():
        count = service.registry.clear_cache()
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
