"""Independent uploads, a durable SQLite queue, and frozen task reports."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import asdict
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

from . import presenters
from .assessment import assess, current_match
from .bib import parse_bib, preview_export
from .entry_status import CLASSIFICATION_SQL, GROUPS, SUPPORTED_SQL, classification, summary_expressions, verification_supported
from .fields import required_corrections
from .events import TaskEvents
from .journals import issns, journal_matches
from .knowledge import Knowledge, field_normalize, identifiers
from .matching import author_match, normalize, plain_title
from .policy import PROVIDERS, SETTING_LIMITS
from .settings import DEFAULTS, Settings, redact, secrets_from
from .storage import Store, dump, load, now, uid

TERMINAL = {"completed", "partial", "failed", "cancelled"}
UNKNOWN_COST = {"amount": None, "currency": None, "known": False, "price_snapshot": None}


def display_text(value):
    return value.encode("utf-8", "surrogateescape").decode("utf-8", "replace")


class BudgetConfirmationRequired(ValueError):
    def __init__(self, entry_count, entry_request_budget, request_budget):
        super().__init__("理论请求上限超过任务总上限，请选择继续或调整预算。")
        self.details = {"entry_count": entry_count, "entry_request_budget": entry_request_budget,
                        "request_budget": request_budget, "maximum_requests": entry_count * entry_request_budget}


class AuditService:
    def __init__(self, data_dir: Path, registry=None):
        self.store = Store(data_dir / "cite-audit.sqlite3")
        self.events = TaskEvents(self.store)
        self.settings = Settings(self.store)
        self.knowledge = Knowledge(self.store)
        from .sources import SourceRegistry
        if registry is None:
            registry = SourceRegistry()
        if isinstance(registry, SourceRegistry):
            from .cnki import BrowserSession
            registry.cnki_browser = BrowserSession(self.store)
        self.registry = registry
        self.worker_task = None
        self.wake = asyncio.Event()
        self.stopping = False

    def create_task(self, raw: bytes, filename: str, name: str, mode: str, config: dict | None = None,
                    *, confirm_budget: bool = False):
        settings = self.settings.get()
        if mode not in {"demo", "online"}:
            raise ValueError("mode must be demo or online")
        if len(raw) > settings["max_upload_bytes"]:
            raise ValueError("Bib upload exceeds the configured size limit")
        if not filename.casefold().endswith(".bib"):
            raise ValueError("Upload a .bib file")
        try:
            document = parse_bib(raw)
        except (UnicodeError, ValueError) as exc:
            raise ValueError(f"Cannot safely parse this UTF-8 Bib: {exc}") from exc
        if len(document.entries) > settings["max_entries"]:
            raise ValueError("Bib exceeds configured entry limit")
        if not document.entries:
            raise ValueError("No Bib entries found")
        overrides = config or {}
        allowed = {"entry_request_budget", "request_budget", "timeout_seconds", "max_retries", "model_enabled", "force_refresh"}
        if set(overrides) - allowed:
            raise ValueError("Unsupported task configuration field")
        task_config = {
            "entry_request_budget": settings["max_source_requests"],
            "request_budget": settings["max_task_requests"], "timeout_seconds": settings["request_timeout_seconds"],
            "max_retries": settings["retries"], "model_enabled": False,
            **overrides,
        }
        if type(task_config.get("force_refresh", False)) is not bool:
            raise ValueError("force_refresh must be a boolean")
        for key, internal in {"entry_request_budget": "max_source_requests", "request_budget": "max_task_requests",
                              "timeout_seconds": "request_timeout_seconds",
                              "max_retries": "retries"}.items():
            low, high = SETTING_LIMITS[internal]
            value = task_config[key]
            if (type(value) not in (int, float) or not low <= value <= high
                    or (key != "timeout_seconds" and int(value) != value)):
                raise ValueError(f"{key} must be between {low} and {high}")
        # Model-assisted auditing is not silently activated by uploads. The model
        # test endpoint is separately explicit about billing.
        if task_config["model_enabled"]:
            raise ValueError("Automatic model-assisted auditing is not enabled; explicit model connection test only")
        if type(confirm_budget) is not bool:
            raise ValueError("confirm_budget must be a boolean")
        if len(document.entries) * task_config["entry_request_budget"] > task_config["request_budget"] and not confirm_budget:
            raise BudgetConfirmationRequired(len(document.entries), task_config["entry_request_budget"], task_config["request_budget"])
        task_id, timestamp = uid("task"), now()
        digest = sha256(raw).hexdigest()
        with self.store.connect() as db:
            db.execute("INSERT OR IGNORE INTO uploads VALUES(?,?,?)", (digest, raw, timestamp))
            db.execute("INSERT INTO tasks(id,name,filename,upload_hash,mode,status,created_at,updated_at,config,diagnostics) "
                       "VALUES(?,?,?,?,?,?,?,?,?,?)",
                       (task_id, name.strip()[:200] or filename, filename, digest, mode, "queued", timestamp, timestamp,
                        dump(task_config), dump([asdict(d) for d in document.diagnostics])))
            for e in document.entries:
                diagnostics = [asdict(d) for d in e.diagnostics]
                db.execute("INSERT INTO entries VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                           (uid("entry"), task_id, e.index, e.entry_id, e.key, e.entry_type, dump({k: display_text(v) for k, v in e.fields.items()}), display_text(e.raw),
                            dump(diagnostics), int(e.editable), "queued", None))
        self.wake.set()
        return self.task(task_id)

    def raw_task(self, task_id):
        row = self.store.one("SELECT * FROM tasks WHERE id=?", (task_id,))
        if not row:
            raise KeyError("Task not found")
        return row

    def report_revision(self, task_id):
        return self.store.one("SELECT COALESCE(MAX(revision),0)+1 AS revision FROM task_reports WHERE task_id=?",
                              (task_id,))["revision"]

    def report_history(self, task_id):
        with self.store.connect() as db:
            db.execute("BEGIN")
            task = db.execute("SELECT report FROM tasks WHERE id=?", (task_id,)).fetchone()
            if not task:
                raise KeyError("Task not found")
            revisions = [row["revision"] for row in db.execute(
                "SELECT revision FROM task_reports WHERE task_id=? ORDER BY revision DESC", (task_id,))]
            if task["report"]:
                revisions.insert(0, (revisions[0] if revisions else 0) + 1)
        return {"items": [{"revision": revision, "report_url": f"/api/tasks/{task_id}/reports/{revision}"}
                          for revision in revisions]}

    def historical_report(self, task_id, revision):
        with self.store.connect() as db:
            db.execute("BEGIN")
            task = db.execute("SELECT report FROM tasks WHERE id=?", (task_id,)).fetchone()
            if not task:
                raise KeyError("Task not found")
            row = db.execute("SELECT report FROM task_reports WHERE task_id=? AND revision=?", (task_id, revision)).fetchone()
            if row:
                return load(row["report"])
            current = db.execute("SELECT COALESCE(MAX(revision),0)+1 FROM task_reports WHERE task_id=?",
                                 (task_id,)).fetchone()[0]
            if task["report"] and revision == current:
                return load(task["report"])
        raise KeyError("Report revision not found")

    def original(self, task_id):
        row = self.raw_task(task_id)
        return self.store.one("SELECT content FROM uploads WHERE hash=?", (row["upload_hash"],))["content"]

    def entry(self, task_id, entry_id, detail=True):
        row = self.store.one("SELECT * FROM entries WHERE task_id=? AND id=?", (task_id, entry_id))
        if not row:
            raise KeyError("Entry not found")
        return self._entry(row, self.raw_task(task_id)["mode"], detail)

    def _entry(self, row, mode, detail):
        fields, result = load(row["input_fields"]), load(row["result"], {})
        item = {
            "id": row["id"], "task_id": row["task_id"], "ordinal": row["ordinal"], "citation_key": row["citation_key"],
            "entry_type": row["entry_type"], "title": fields.get("title", "Untitled entry"),
            "journal": fields.get("journal", ""), "booktitle": fields.get("booktitle", ""),
            "author": fields.get("author", ""), "year": fields.get("year", ""), "execution_status": row["status"],
            "identity_state": result.get("identity_state", "insufficient"),
            "version_status": result.get("version_status", "unknown"),
            "recommendation_status": result.get("recommendation_status", "unavailable"),
            "required_corrections": required_corrections(result),
            "has_conflict": result.get("has_conflict", False), "conflict_count": result.get("conflict_count", 0),
            "completion_count": result.get("completion_count", 0), "article_id": result.get("article_id"),
            "current_version_id": result.get("current_version_id"), "recommended_version_id": result.get("recommended_version_id"),
            "mode": mode, "diagnostics": load(row["diagnostics"]),
            "verification_supported": verification_supported(row["entry_type"], fields, result),
        }
        item["classification"] = classification(item, result.get("calls", []))
        if detail:
            item.update({"original_bib": row["raw_bib"], "fields": fields,
                         "field_assessments": [], "proposals": [], "sources": [], "candidates": [], "calls": [],
                         "warnings": [], "identity_reason": "Awaiting audit", "current_version": None,
                         "recommended_version": None, "version_relations": [],
                         "evidence_revision": {"merge_revision": None, "recommendation_revision": None,
                                               "rule_version": None},
                         "recommendation": {"status": "unavailable", "reason": "Awaiting audit", "differences": []}})
            if result:
                item.update(presenters.assessment(result))
                if item["identity_reason"] == "Source access failed or budget exhausted; existence is not determined":
                    item["identity_reason"] = ("请求预算耗尽，尚未确认文献身份" if item["classification"] == "budget_exhausted"
                                               else "来源访问失败，尚未确认文献身份")
        return item

    def entries(self, task_id, detail=False):
        task = self.raw_task(task_id)
        return [self._entry(row, task["mode"], detail) for row in self.store.all(
            "SELECT * FROM entries WHERE task_id=? ORDER BY ordinal", (task_id,))]

    def task_page(self, q="", execution_status="", sort="created_desc", page=1, page_size=25):
        where, args = "tasks WHERE instr(casefold(name || ' ' || filename),?)>0", [q.casefold()]
        if execution_status:
            where += " AND status=?"
            args.append(execution_status)
        order = {"created_asc": "created_at,id", "updated_desc": "updated_at DESC,id"}.get(sort, "created_at DESC,id")
        result = self.store.page(where, args, order=order, page=page, page_size=page_size, columns="id")
        result["items"] = [self.task(row["id"]) for row in result["items"]]
        result["active_tasks"] = self.store.one(
            "SELECT COUNT(*) AS count FROM tasks WHERE status IN ('queued','running') OR report IS NULL")["count"]
        return result

    def entry_page(self, task_id, q="", identity_state="", recommendation_status="", has_conflict=False,
                   sort="ordinal", page=1, page_size=25, classification="", needs_correction=False, execution_status=""):
        task = self.raw_task(task_id)
        where = ("entries WHERE task_id=? AND instr(casefold(citation_key || ' ' || "
                 "COALESCE(json_extract(input_fields,'$.title'),'Untitled entry') || ' ' || "
                 "COALESCE(json_extract(input_fields,'$.author'),'')),?)>0")
        args = [task_id, q.casefold()]
        if execution_status:
            statuses = execution_status.split(",")
            if not set(statuses) <= {"queued", "running", "completed", "partial", "failed", "cancelled"}:
                raise ValueError("Unknown entry execution status")
            where += " AND status IN (" + ",".join("?" for _ in statuses) + ")"
            args.extend(statuses)
        if identity_state == "unsupported":
            where += f" AND NOT {SUPPORTED_SQL}"
        elif identity_state:
            where += f" AND {SUPPORTED_SQL} AND COALESCE(json_extract(result,'$.identity_state'),'insufficient')"
            where += "=?"
            args.append(identity_state)
        if recommendation_status:
            where += " AND COALESCE(json_extract(result,'$.recommendation_status'),'unavailable')=?"
            args.append(recommendation_status)
        if has_conflict:
            where += " AND json_extract(result,'$.has_conflict')=1"
        if needs_correction:
            where += " AND needs_correction(result)"
        if classification:
            if classification not in GROUPS:
                raise ValueError("Unknown entry classification")
            where += f" AND ({CLASSIFICATION_SQL})=?"
            args.append(classification)
        order = {"title": "COALESCE(json_extract(input_fields,'$.title'),'Untitled entry'),ordinal",
                 "year": "COALESCE(json_extract(input_fields,'$.year'),''),ordinal"}.get(sort, "ordinal")
        result = self.store.page(where, args, order=order, page=page, page_size=page_size)
        result["items"] = [self._entry(row, task["mode"], False) for row in result["items"]]
        return result

    def call_page(self, task_id="", entry_id="", provider="", status="", page=1, page_size=25):
        conditions, args = ["1=1"], []
        for key, value in (("task_id", task_id), ("entry_id", entry_id), ("provider", provider), ("status", status)):
            if value:
                field = key if key in {"task_id", "entry_id"} else f"json_extract(data,'$.{key}')"
                conditions.append(f"{field}=?")
                args.append(value)
        result = self.store.page("calls WHERE " + " AND ".join(conditions), args, order="created_at DESC,id DESC",
                                 page=page, page_size=page_size, columns="json_remove(data,'$.response_summary') AS data")
        result["items"] = [load(row["data"]) for row in result["items"]]
        return result

    def call_detail(self, call_id):
        row = self.store.one("SELECT data FROM calls WHERE id=?", (call_id,))
        if not row:
            raise KeyError("Call record not found")
        return load(row["data"])

    def task(self, task_id):
        row = self.store.one(
            "SELECT t.id,t.name,t.filename,t.mode,t.upload_hash,t.created_at,t.updated_at,t.status,t.error,"
            "t.config,t.diagnostics,t.report IS NOT NULL AS finalized,length(u.content) AS byte_size,"
            "(SELECT COALESCE(MAX(revision),0)+1 FROM task_reports WHERE task_id=t.id) AS revision,c.* "
            "FROM tasks t JOIN uploads u ON u.hash=t.upload_hash LEFT JOIN task_counts c ON c.task_id=t.id "
            "WHERE t.id=?", (task_id,))
        if not row:
            raise KeyError("Task not found")
        summary = {key: row[key] or 0 for key in summary_expressions()}
        return {
            "id": task_id, "name": row["name"], "filename": row["filename"], "mode": row["mode"],
            "original_sha256": row["upload_hash"], "byte_size": row["byte_size"], "encoding": "utf-8",
            "created_at": row["created_at"], "updated_at": row["updated_at"], "execution_status": row["status"],
            "finalized": bool(row["finalized"]),
            "report_revision": row["revision"],
            "error": row["error"], "config": load(row["config"]), "summary": summary,
            "warnings": [d["message"] for d in load(row["diagnostics"])] + (
                ["Demonstration fixtures; not online verification"] if row["mode"] == "demo" else []),
            "cost": deepcopy(UNKNOWN_COST), "original_url": f"/api/tasks/{task_id}/original",
            "report_url": f"/api/tasks/{task_id}/report",
        }

    def call(self, task_id, entry_id, item, mode, *, revision=None):
        # A request may finish after cancellation/deletion. Its result belongs to
        # neither a frozen report nor a deleted task and must not revive either.
        config = self.settings.get()
        safe = redact(item, secrets_from(config))
        record = {"id": uid("call"), "task_id": task_id, "entry_id": entry_id,
                  "report_revision": revision,
                  "provider": safe.get("provider", "unknown"), "kind": safe.get("kind", "source"),
                  "operation": safe.get("operation", "lookup"), "status": {
                      "mock": "success", "empty": "no_result", "error": "failed", "unsupported": "partial",
                      "redirect": "success"}.get(
                      safe.get("status"), safe.get("status", "failed")),
                  "started_at": safe.get("started_at", now()), "duration_ms": safe.get("duration_ms", 0),
                  "attempt": safe.get("attempt", 1), "cache_hit": safe.get("cache_hit", False), "mode": mode,
                  "request_count": safe.get("request_count", 0),
                  "error_code": "coverage_unavailable" if safe.get("status") == "unsupported" else safe.get("error_code"),
                  "message": safe.get("message", safe.get("error")),
                  "usage": safe.get("usage"), "cost": safe.get("cost") or deepcopy(UNKNOWN_COST)}
        # Request summaries are deliberately optional: never persist whole prompts/Bib by default.
        if safe.get("request_summary"):
            record["request_summary"] = safe["request_summary"]
        for key in ("http_status", "fetched_at", "record_count", "response_summary"):
            if safe.get(key) is not None:
                record[key] = safe[key]
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if task_id:
                task = db.execute("SELECT cancelled FROM tasks WHERE id=?", (task_id,)).fetchone()
                current = db.execute("SELECT COALESCE(MAX(revision),0)+1 FROM task_reports WHERE task_id=?",
                                     (task_id,)).fetchone()[0]
                if not task or task["cancelled"] or (revision is not None and revision != current):
                    return None
                record["report_revision"] = current
            db.execute("INSERT INTO calls VALUES(?,?,?,?,?)",
                       (record["id"], task_id, entry_id, record["started_at"], dump(record)))
        return record

    def calls(self, task_id=None, entry_id=None, *, revision=None):
        conditions, args = [], []
        for key, value in (("task_id", task_id), ("entry_id", entry_id)):
            if value:
                conditions.append(f"{key}=?")
                args.append(value)
        if revision is not None:
            conditions.append("COALESCE(json_extract(data,'$.report_revision'),1)=?")
            args.append(revision)
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        return [load(r["data"]) for r in self.store.all(
            "SELECT json_remove(data,'$.response_summary') AS data FROM calls" + where + " ORDER BY created_at,id", args)]

    def cancel(self, task_id):
        task = self.raw_task(task_id)
        if task["status"] not in TERMINAL:
            with self.store.connect() as db:
                db.execute("UPDATE tasks SET cancelled=1,status='cancelled',updated_at=? WHERE id=?", (now(), task_id))
                db.execute("UPDATE entries SET status='cancelled' WHERE task_id=? AND status IN ('queued','running')", (task_id,))
            self.freeze(task_id)
        return self.task(task_id)

    def retry(self, task_id, **selection):
        return self._retry(task_id, **selection)

    def retry_entry(self, task_id, entry_id):
        return self._retry(task_id, entry_id)

    def retry_unverifiable(self, task_id):
        return self._retry(task_id, categories=["unverifiable"])

    def _retry(self, task_id, entry_id=None, *, categories=None, entry_ids=None, revision=None, budgets=None):
        # Archive and enqueue atomically so duplicate clicks cannot start another
        # revision or erase the frozen report. Original inputs and IDs stay put.
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            task = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if not task:
                raise KeyError("Task not found")
            if entry_id and not db.execute("SELECT 1 FROM entries WHERE task_id=? AND id=?",
                                           (task_id, entry_id)).fetchone():
                raise KeyError("Entry not found")
            if task["status"] not in TERMINAL or not task["report"]:
                raise ValueError("请等待当前任务核查结束并生成报告后再重新核查")
            current_revision = db.execute("SELECT COALESCE(MAX(revision),0)+1 FROM task_reports WHERE task_id=?",
                                          (task_id,)).fetchone()[0]
            if revision is not None and current_revision != revision:
                raise ValueError("报告已更新，请重新打开对话框选择条目")
            revision = current_revision
            db.execute("INSERT INTO task_reports VALUES(?,?,?)", (task_id, revision, task["report"]))
            where = "task_id=?"
            params = [task_id]
            if entry_id:
                where += " AND id=?"
                params.append(entry_id)
            if categories is not None:
                if not categories or set(categories) - set(GROUPS) or entry_ids is not None:
                    raise ValueError("请选择有效的核查结果分组")
                where += f" AND ({CLASSIFICATION_SQL}) IN (SELECT value FROM json_each(?))"
                params.append(dump(categories))
            if entry_ids is not None:
                where += " AND id IN (SELECT value FROM json_each(?))"
                params.append(dump(entry_ids))
                if not entry_ids or db.execute(f"SELECT count(*) FROM entries WHERE {where}", params).fetchone()[0] != len(set(entry_ids)):
                    raise ValueError("所选条目为空或不属于当前任务")
            queued = db.execute(f"UPDATE entries SET status='queued',result=NULL WHERE {where}", params)
            if not queued.rowcount:
                raise ValueError("当前选择没有可重新核查的条目")
            config = {**load(task["config"]), "force_refresh": True}
            for key, limit in (("entry_request_budget", "max_source_requests"), ("request_budget", "max_task_requests")):
                if budgets and key in budgets:
                    value = budgets[key]
                    low, high = SETTING_LIMITS[limit]
                    if type(value) is not int or not low <= value <= high:
                        raise ValueError(f"{key} 必须在 {low} 到 {high} 之间")
                    config[key] = value
            db.execute("UPDATE tasks SET status='queued',cancelled=0,error=NULL,report=NULL,config=?,updated_at=? WHERE id=?",
                       (dump(config), now(), task_id))
        self.wake.set()
        return self.task(task_id)

    def delete(self, task_id):
        task = self.raw_task(task_id)
        if task["status"] not in TERMINAL:
            raise ValueError("Cancel this running task before deleting it")
        with self.store.connect() as db:
            db.execute("DELETE FROM tasks WHERE id=?", (task_id,))
            db.execute("DELETE FROM uploads WHERE hash=? AND NOT EXISTS "
                       "(SELECT 1 FROM tasks WHERE upload_hash=uploads.hash)", (task["upload_hash"],))
        return {"deleted": True, "knowledge_preserved": True}

    def freeze(self, task_id):
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self.raw_task(task_id)
            if row["report"]:
                return load(row["report"])
            report = {"task": self.task(task_id), "entries": self.entries(task_id, True),
                      "calls": self.calls(task_id), "generated_at": now()}
            if row["status"] in TERMINAL:
                report["task"]["finalized"] = True
                db.execute("UPDATE tasks SET report=? WHERE id=? AND report IS NULL", (dump(report), task_id))
        return report

    async def start(self):
        self.stopping = False
        # Completed entries/reports are never reprocessed. An interrupted entry is
        # retried from its original input, not from an inherited conclusion.
        with self.store.connect() as db:
            db.execute("UPDATE tasks SET status='queued',updated_at=? WHERE status='running' AND cancelled=0", (now(),))
            db.execute("UPDATE entries SET status='queued' WHERE status='running'")
        for row in self.store.all("SELECT id FROM tasks WHERE status IN ('completed','partial','failed','cancelled') "
                                  "AND report IS NULL"):
            self.freeze(row["id"])
        self.worker_task = asyncio.create_task(self._worker(), name="durable-bib-audit")
        self.wake.set()

    async def stop(self):
        self.stopping = True
        self.wake.set()
        if self.worker_task:
            self.worker_task.cancel()
            try:
                await self.worker_task
            except asyncio.CancelledError:
                pass

    async def _worker(self):
        while not self.stopping:
            row = self.store.one("SELECT id FROM tasks WHERE status='queued' AND cancelled=0 ORDER BY created_at LIMIT 1")
            if row:
                try:
                    await self.run_task(row["id"])
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001 - isolate untrusted providers and keep queue alive
                    # One corrupt task cannot stop the durable queue.
                    self.store.execute("UPDATE tasks SET status='failed',error=?,updated_at=? "
                                       "WHERE id=? AND status NOT IN ('completed','partial','cancelled')",
                                       ("Task processing failed; original retained", now(), row["id"]))
                    self.freeze(row["id"])
                continue
            self.wake.clear()
            try:
                await asyncio.wait_for(self.wake.wait(), timeout=1)
            except TimeoutError:
                pass

    def is_cancelled(self, task_id, revision=None):
        task = self.store.one("SELECT cancelled FROM tasks WHERE id=?", (task_id,))
        return (self.stopping or not task or bool(task["cancelled"])
                or (revision is not None and revision != self.report_revision(task_id)))

    def local_crossref_source(self, fields):
        from .sources import normalize_doi
        doi = normalize_doi(fields.get("doi", ""))
        if not doi:
            return None
        row = self.store.one(
            "SELECT id FROM sources WHERE namespace='online' AND provider='crossref' "
            "AND record_id=? AND status='confirmed' ORDER BY created_at DESC LIMIT 1", (doi,))
        if row:
            source = self.knowledge.source(row["id"])
            if (source.get("source_type") == "registry" and source.get("status") == "verified"
                    and source["fields"].get("entrytype") in {"article", "inproceedings", "incollection"}
                    and current_match(fields, source)[0]):
                return source
        return None

    def conference_index(self, enabled):
        providers = [p for p in ("acl", "pmlr", "iclr", "neurips", "colm") if enabled.get(p, True)]
        index = {}
        for row in self.store.all(
            "SELECT id,provider,record_id,version_id,json_extract(data,'$.fields') AS fields FROM sources "
            "WHERE namespace='online' AND status='confirmed' AND json_extract(data,'$.source_type')='official' "
            "AND provider IN (SELECT value FROM json_each(?)) ORDER BY created_at DESC", (dump(providers),)):
            fields = load(row["fields"])
            title = normalize(plain_title(fields.get("title", "")))
            if title:
                row["fields"] = fields
                index.setdefault(title, []).append(row)
        return index

    def conference_hits(self, index, fields):
        if fields.get("eprint") or fields.get("journal"):
            return []
        hits = {}
        for row in index.get(normalize(plain_title(fields.get("title", ""))), []):
            other = row["fields"]
            if any(fields.get(k) and other.get(k) and field_normalize(k, fields[k]) != field_normalize(k, other[k])
                   for k in ("doi", "booktitle", "year", "url")):
                continue
            same_id = any(fields.get(k) and field_normalize(k, fields[k]) == field_normalize(k, other.get(k))
                          for k in ("doi", "url"))
            same_venue = all(fields.get(k) and field_normalize(k, fields[k]) == field_normalize(k, other.get(k))
                             for k in ("booktitle", "year"))
            if (same_id or same_venue) and author_match(fields.get("author", ""), other.get("author", "")) == "full":
                hits.setdefault((row["provider"], row["record_id"]), row)
        if len({row["version_id"] for row in hits.values()}) != 1:
            return []
        return [self.knowledge.source(row["id"]) for row in hits.values()]

    def reusable_sources(self, fields, mode, ttl):
        wanted = identifiers({"fields": fields})
        if not wanted:
            return []
        namespace = "mock" if mode == "demo" else "online"
        for row in self.store.all("SELECT id,identifiers FROM versions WHERE namespace=?", (namespace,)):
            ids = load(row["identifiers"])
            if not any(ids.get(k) == v for k, v in wanted.items()):
                continue
            version = self.knowledge.version(row["id"])
            if not current_match(fields, version)[0]:
                continue
            if (fields.get("journal") and not issns(version["fields"].get("issn", ""))
                    and field_normalize("journal", fields["journal"]) !=
                    field_normalize("journal", version["fields"].get("journal", ""))):
                continue  # Refresh older evidence before the ISSN-based second check.
            if any(k not in version["fields"] for k in fields if k in {
                    "title", "author", "year", "journal", "booktitle", "volume", "number", "pages"}):
                continue
            # Discover formal versions again on expiry; do not claim a preprint hit
            # proves publication discovery complete indefinitely.
            age = (datetime.now(UTC) - datetime.fromisoformat(version["updated_at"])).total_seconds()
            if age > ttl:
                continue
            article = self.knowledge.article(version["article_id"])
            sources = [s for v in article["versions"] for s in v["sources"]]
            # A recent complementary source does not renew old field evidence.
            active = {(s["provider"], s["record_id"]): s for s in sources}
            if any(s.get("stale") for s in active.values()):
                continue
            return sources
        return []

    def remaining_requests(self, task_id, entry_id, revision, limits):
        used = self.store.one(
            "SELECT COALESCE(SUM(json_extract(data,'$.request_count')),0) AS task_used, "
            "COALESCE(SUM(CASE WHEN entry_id=? THEN json_extract(data,'$.request_count') ELSE 0 END),0) AS entry_used "
            "FROM calls WHERE task_id=? AND COALESCE(json_extract(data,'$.report_revision'),1)=? "
            "AND json_extract(data,'$.kind')='source'", (entry_id, task_id, revision))
        task_remaining = limits["request_budget"] - used["task_used"]
        entry_remaining = limits.get("entry_request_budget", DEFAULTS["max_source_requests"]) - used["entry_used"]
        return max(0, min(task_remaining, entry_remaining))

    async def run_task(self, task_id):
        if self.is_cancelled(task_id):
            return
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            changed = db.execute("UPDATE tasks SET status='running',attempts=attempts+1,updated_at=? "
                                 "WHERE id=? AND status='queued' AND cancelled=0", (now(), task_id)).rowcount
            if not changed:
                return
            task = dict(db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone())
            revision = db.execute("SELECT COALESCE(MAX(revision),0)+1 FROM task_reports WHERE task_id=?",
                                  (task_id,)).fetchone()[0]
        config = self.settings.get()
        overrides = load(task["config"])
        config.update({"request_timeout_seconds": overrides["timeout_seconds"], "retries": overrides["max_retries"],
                       "force_refresh": overrides.get("force_refresh", False)})
        rows = self.store.all("SELECT * FROM entries WHERE task_id=? AND status='queued' ORDER BY ordinal", (task_id,))
        conference_index = (self.conference_index(config.get("source_enabled", {}))
                            if task["mode"] == "online" and not config.get("force_refresh") else {})
        local_arxiv = {}
        if task["mode"] == "online" and config.get("source_enabled", {}).get("arxiv", True):
            from .sources import arxiv_identifier
            ids = []
            for row in rows:
                if row["entry_type"] == "patent" or any(d.get("severity") == "error" for d in load(row["diagnostics"])):
                    continue
                fields = load(row["input_fields"])
                if fields.get("edition", "").upper().startswith("CN"):
                    continue
                if identifier := arxiv_identifier(fields):
                    ids.append(identifier)
            ids = list(dict.fromkeys(ids))
            if not config.get("force_refresh"):
                for identifier in ids:
                    source = self.knowledge.local_arxiv_source(identifier, config["knowledge_ttl_seconds"])
                    if source:
                        local_arxiv[identifier] = source
            config["arxiv_batch_ids"] = [key for key in ids if key not in local_arxiv]
            config["arxiv_batch_results"] = {}
        from .policy import cnki_search_title, cnki_title_eligible
        cnki_by_entry = {}
        if (task["mode"] == "online" and config.get("source_enabled", {}).get("cnki")
                and hasattr(self.registry, "cnki_task")):
            titles = {}
            for row in rows:
                fields = load(row["input_fields"])
                title = cnki_search_title(fields.get("title", ""))
                if (cnki_title_eligible(title) and row["entry_type"] != "patent" and not fields.get("edition", "").upper().startswith("CN")
                        and not any(d.get("severity") == "error" for d in load(row["diagnostics"]))):
                    titles.setdefault(title, []).append(row["id"])

            async def batch_audit(entry_id, item):
                return self.call(task_id, entry_id, item, "online", revision=revision)

            if titles:
                batches = await self.registry.cnki_task(
                    titles, config, batch_audit,
                    lambda entry: self.remaining_requests(task_id, entry, revision, overrides),
                    lambda: self.is_cancelled(task_id, revision))
                for title, entries in titles.items():
                    for entry in entries:
                        cnki_by_entry[entry] = batches.get(title, [])
                        if entry != entries[0]:
                            await batch_audit(entry, {"provider": "cnki", "operation": "复用同题名任务检索",
                                                     "status": "success" if title in batches else "partial",
                                                     "record_count": len(cnki_by_entry[entry])})
            config["source_enabled"] = {**config["source_enabled"], "cnki": False}
        for row in rows:
            if self.is_cancelled(task_id, revision):
                break
            self.store.execute("UPDATE entries SET status='running' WHERE id=?", (row["id"],))
            fields = load(row["input_fields"])
            entry_input = {"fields": fields, "entry_type": row["entry_type"], "citation_key": row["citation_key"],
                           "editable": bool(row["editable"])}
            async def audit(item, entry_id=row["id"]):
                return self.call(task_id, entry_id, item, task["mode"], revision=revision)
            try:
                parse_errors = [d for d in load(row["diagnostics"]) if d.get("severity") == "error"]
                if parse_errors:
                    result = assess(self.knowledge, entry_input, [], task["mode"], [])
                    result["identity_reason"] = "Parse errors prevent reliable audit; original entry retained"
                    result["warnings"].extend(d["message"] for d in parse_errors)
                    status = "failed"
                else:
                    existing = [] if config.get("force_refresh") else self.reusable_sources(fields, task["mode"], config["knowledge_ttl_seconds"])
                    local_source = local_arxiv.get(arxiv_identifier(fields)) if local_arxiv else None
                    local_sources = self.conference_hits(conference_index, fields) if conference_index else []
                    if local_source:
                        local_sources.append(local_source)
                    if (task["mode"] == "online" and not config.get("force_refresh")
                            and config.get("source_enabled", {}).get("crossref", True)):
                        crossref = self.local_crossref_source(fields)
                        if crossref:
                            local_sources.append(crossref)
                    existing = list({s["id"]: s for s in [*existing, *local_sources]}.values())
                    for local_source in local_sources:
                        await audit({"provider": local_source["provider"], "operation": "lookup", "status": "success",
                                     "cache_hit": True, "fetched_at": local_source["fetched_at"],
                                     "message": f"复用本地官方快照 {local_source['record_id']}",
                                     "response_summary": {"snapshot_id": local_source["snapshot_id"]}})
                    if existing:
                        await audit({"provider": "knowledge", "operation": "reuse", "status": "success", "cache_hit": True,
                                     "message": "复用已有版本证据。" + ("继续在线发现其他记录。" if task["mode"] == "online" else "")})
                    sources = existing
                    if not existing or task["mode"] == "online":
                        remaining = self.remaining_requests(task_id, row["id"], revision, overrides)
                        if remaining <= 0:
                            await audit({"provider": "budget", "operation": "lookup", "status": "budget_exhausted",
                                         "message": "Entry or task request budget exhausted"})
                        lookup_config = {**config, "max_source_requests": max(0, remaining)}
                        if remaining <= 0 and config.get("arxiv_batch_results"):
                            lookup_config["source_enabled"] = {p: p == "arxiv" for p in PROVIDERS}
                        if local_sources:
                            lookup_config["source_enabled"] = {**lookup_config.get("source_enabled", {}),
                                                               **{s["provider"]: False for s in local_sources}}
                        records = cnki_by_entry.get(row["id"], [])
                        lookup_config["task_cnki_records"] = records
                        if remaining > 0 or (config.get("arxiv_batch_results")
                                             and arxiv_identifier(fields) in config["arxiv_batch_results"]):
                            records = await self.registry.lookup(
                                {**fields, "entrytype": row["entry_type"]},
                                "mock" if task["mode"] == "demo" else "online", lookup_config,
                                audit=audit, cancelled=lambda: self.is_cancelled(task_id, revision))
                        if self.is_cancelled(task_id, revision):
                            break
                        ingestion = asyncio.create_task(asyncio.to_thread(
                            self.knowledge.ingest, records, "mock" if task["mode"] == "demo" else "online"))
                        try:
                            discovered = await asyncio.shield(ingestion)
                            sources = list({s["id"]: s for s in [*existing, *discovered]}.values())
                        except asyncio.CancelledError:
                            # Finish this batch before the worker shuts down.
                            await ingestion
                            raise
                        if self.is_cancelled(task_id, revision):
                            break
                    calls = self.calls(task_id, row["id"], revision=revision)
                    result = assess(self.knowledge, entry_input, sources, task["mode"], calls)
                    journal = next((a for a in result["field_assessments"]
                                    if a["field"] == "journal" and a["judgment"] == "conflict"), None)
                    if (journal and task["mode"] == "online"
                            and config.get("source_enabled", {}).get("crossref", True)):
                        current = result.get("current_version") or {}
                        journal_ids = issns(current.get("fields", {}).get("issn", ""))
                        remaining = self.remaining_requests(task_id, row["id"], revision, overrides)
                        checks = []
                        if journal_ids and remaining > 0:
                            records = await self.registry.journal_records(
                                journal_ids, {**config, "max_source_requests": remaining},
                                audit=audit, cancelled=lambda: self.is_cancelled(task_id, revision))
                            if self.is_cancelled(task_id, revision):
                                break
                            for record in records:
                                snapshot_id = self.store.snapshot(record)
                                check = {k: v for k, v in record.items() if k != "raw_payload"}
                                input_ids = issns(fields.get("issn", ""))
                                check.update(snapshot_id=snapshot_id, equivalent=bool(
                                    record["valid"]
                                    and (not fields.get("issn") or bool(set(input_ids) & set(record["issns"])))
                                    and journal_matches(journal["original_value"], record["title"], record["issns"])
                                    and journal_matches(journal["reference_value"], record["title"], record["issns"])))
                                checks.append(check)
                        calls = self.calls(task_id, row["id"], revision=revision)
                        result = assess(self.knowledge, entry_input, sources, task["mode"], calls, checks)
                        if not checks:
                            result["warnings"].append("期刊名称不一致；ISSN 缺失、请求预算不足或期刊记录不可用，二次核查未完成")
                    # Completion describes finishing the assessment, not source
                    # availability or whether the publication could be verified.
                    # Source limitations remain in the result and call audit.
                    status = "completed"
                if self.is_cancelled(task_id, revision):
                    break
                self.store.execute("UPDATE entries SET status=?,result=? WHERE id=? AND status='running'",
                                   (status, dump(result), row["id"]))
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - isolate untrusted providers and keep queue alive
                # Raw provider exception bodies can contain credentials. Persist only
                # stable safe errors and retain other entries, never promote a failure.
                if self.is_cancelled(task_id, revision):
                    break
                await audit({"provider": "audit", "status": "failed", "error_code": "audit_failed",
                             "message": "Entry processing failed; original retained"})
                result = assess(self.knowledge, entry_input, [], task["mode"], self.calls(task_id, row["id"], revision=revision))
                self.store.execute("UPDATE entries SET status='failed',result=? WHERE id=? AND status='running'",
                                   (dump(result), row["id"]))
            self.store.execute("UPDATE tasks SET updated_at=? WHERE id=?", (now(), task_id))
            await asyncio.sleep(0)
        if self.is_cancelled(task_id, revision):
            return
        statuses = [r["status"] for r in self.store.all("SELECT status FROM entries WHERE task_id=?", (task_id,))]
        status = "failed" if all(s == "failed" for s in statuses) else "partial" if any(s != "completed" for s in statuses) else "completed"
        self.annotate_duplicates(task_id)
        self.store.execute("UPDATE tasks SET status=?,updated_at=? WHERE id=? AND cancelled=0 AND status='running'",
                           (status, now(), task_id))
        self.freeze(task_id)

    def annotate_duplicates(self, task_id):
        """Cross-entry warnings are task-local and never merge user entries."""
        rows = self.store.all("SELECT id,result,input_fields FROM entries WHERE task_id=?", (task_id,))
        versions, articles, input_ids = {}, {}, {}
        for row in rows:
            result = load(row["result"], {})
            if result.get("current_version_id"):
                versions.setdefault(result["current_version_id"], []).append(row["id"])
            if result.get("article_id"):
                articles.setdefault(result["article_id"], []).append((row["id"], result["current_version_id"]))
            for kind, value in identifiers({"fields": load(row["input_fields"])}).items():
                input_ids.setdefault((kind, value), []).append(row["id"])
        for row in rows:
            result = load(row["result"], {})
            warnings = result.setdefault("warnings", [])
            if len(versions.get(result.get("current_version_id"), [])) > 1:
                warnings.append("Multiple entries cite the same confirmed publication version; none were removed")
            related = articles.get(result.get("article_id"), [])
            if len({version for _, version in related}) > 1:
                warnings.append("This file cites multiple versions of the same article; review potential duplication")
            if any(row["id"] in group and len(group) > 1 for group in input_ids.values()):
                warnings.append("An identifier is repeated across input entries; each entry was assessed independently")
            if row["result"]:
                result["warnings"] = list(dict.fromkeys(warnings))
                self.store.execute("UPDATE entries SET result=? WHERE id=? AND EXISTS "
                                   "(SELECT 1 FROM tasks WHERE id=? AND cancelled=0 AND report IS NULL)",
                                   (dump(result), row["id"], task_id))

    def export_preview(self, task_id, selections):
        task = self.raw_task(task_id)
        if task["status"] not in TERMINAL:
            raise ValueError("Wait for task completion or cancel before export")
        doc = parse_bib(self.original(task_id))
        edits = []
        seen = set()
        for selection in selections:
            entry_id = selection.get("entry_id")
            if entry_id in seen:
                raise ValueError("Select at most one proposal per entry")
            seen.add(entry_id)
            row = self.store.one("SELECT * FROM entries WHERE task_id=? AND id=?", (task_id, entry_id))
            if not row:
                raise ValueError("Export selection does not belong to this task")
            result = load(row["result"], {})
            proposal = next((p for p in result.get("proposals", []) if p["id"] == selection.get("proposal_id")), None)
            if not proposal or not proposal["safe"]:
                raise ValueError("Proposal is absent or not safe to export")
            requested_fields = selection.get("fields")
            if proposal["proposal_type"] == "whole_version_switch" and requested_fields is not None:
                raise ValueError("A whole-version switch is atomic; field subsets are forbidden")
            values = dict(proposal["fields"])
            if requested_fields is not None:
                if not isinstance(requested_fields, list) or any(f not in values for f in requested_fields):
                    raise ValueError("Field subset must reference frozen proposal fields")
                for assessment in result.get("field_assessments", []):
                    aliases = set(assessment.get("input_fields", {})) & values.keys()
                    selected = aliases & set(requested_fields)
                    if selected and selected != aliases:
                        raise ValueError("Aliases of one semantic field must be selected together")
                values = {f: values[f] for f in requested_fields}
            entry_type = values.pop("entrytype", None)
            edits.append({"entry_id": row["parser_id"], "proposal_type": proposal["proposal_type"], "fields": values,
                          "entry_type": entry_type, "version_id": proposal["target_version_id"]})
        preview = preview_export(doc, edits)
        field_changes = deepcopy(preview.changes)
        parser_map = {r["parser_id"]: r["id"] for r in self.store.all("SELECT id,parser_id FROM entries WHERE task_id=?", (task_id,))}
        grouped = {}
        updated = {e.entry_id: e for e in parse_bib(preview.content).entries}
        originals = {e.entry_id: e for e in doc.entries}
        for change in field_changes:
            parser_id = change["entry_id"]
            group = grouped.setdefault(parser_id, {
                "entry_id": parser_map[parser_id], "citation_key": change["key"],
                "proposal_type": change["proposal_type"], "fields": [], "field_changes": [],
                "before": display_text(originals[parser_id].raw), "after": display_text(updated[parser_id].raw),
            })
            group["fields"].append(change["field"])
            group["field_changes"].append({k: v for k, v in change.items() if k not in {"entry_id", "key"}})
        changes = list(grouped.values())
        filename = Path(task["filename"]).stem + ".corrected.bib"
        response = {"filename": filename, "bibtex": preview.content.decode("utf-8-sig", errors="replace"),
                    "original_sha256": task["upload_hash"], "output_sha256": preview.sha256,
                    "changes": changes,
                    "warnings": ["此导出保留未修改的原始字段，其中可能包含未核查或未确认的信息；导出不代表所有字段已核验。"],
                    "blocked": [{**b, "entry_id": parser_map.get(b.get("entry_id"), b.get("entry_id"))} for b in preview.blocked],
                    "unchanged_entries": len(doc.entries) - len(changes), "changed_entries": len(changes)}
        return response, preview.content

    def create_export(self, task_id, selections, confirmed, expected_hash):
        if confirmed is not True:
            raise ValueError("Explicit confirmation is required to create a corrected file")
        preview, content = self.export_preview(task_id, selections)
        if preview["blocked"]:
            raise ValueError("Resolve blocked edits before creating an export")
        if preview["output_sha256"] != expected_hash:
            raise ValueError("Preview hash mismatch; review a fresh preview before confirming")
        export_id, timestamp = uid("export"), now()
        self.store.execute("INSERT INTO exports VALUES(?,?,?,?,?,?,?)",
                           (export_id, task_id, timestamp, content, preview["output_sha256"], dump(selections), dump(preview)))
        return {"id": export_id, "filename": preview["filename"], "original_sha256": preview["original_sha256"],
                "output_sha256": preview["output_sha256"], "created_at": timestamp,
                "download_url": f"/api/tasks/{task_id}/exports/{export_id}/download",
                "report_url": f"/api/tasks/{task_id}/exports/{export_id}/report"}
