"""Independent uploads, a durable SQLite queue, and frozen task reports."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

from . import presenters
from .assessment import assess
from .bib import parse_bib, preview_export
from .knowledge import Knowledge, identifiers
from .settings import Settings, redact, secrets_from
from .storage import Store, dump, load, now, uid

TERMINAL = {"completed", "partial", "failed", "cancelled"}
UNKNOWN_COST = {"amount": None, "currency": None, "known": False, "price_snapshot": None}


def display_text(value):
    return value.encode("utf-8", "surrogateescape").decode("utf-8", "replace")


def diagnostic(item):
    return asdict(item) if is_dataclass(item) else dict(item)


class AuditService:
    def __init__(self, data_dir: Path, registry=None):
        self.store = Store(data_dir / "cite-audit.sqlite3")
        self.settings = Settings(self.store)
        self.knowledge = Knowledge(self.store)
        if registry is None:
            from .sources import SourceRegistry
            registry = SourceRegistry()
        self.registry = registry
        self.worker_task = None
        self.wake = asyncio.Event()
        self.stopping = False

    def create_task(self, raw: bytes, filename: str, name: str, mode: str, config: dict | None = None):
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
        allowed = {"request_budget", "timeout_seconds", "max_retries", "model_enabled"}
        if set(overrides) - allowed:
            raise ValueError("Unsupported task configuration field")
        task_config = {
            "request_budget": settings["max_task_requests"], "timeout_seconds": settings["request_timeout_seconds"],
            "max_retries": settings["retries"], "model_enabled": False,
            **overrides,
        }
        if not isinstance(task_config["request_budget"], int) or not 1 <= task_config["request_budget"] <= 5000:
            raise ValueError("request_budget must be between 1 and 5000")
        if not isinstance(task_config["timeout_seconds"], (float, int)) or not 1 <= task_config["timeout_seconds"] <= 60:
            raise ValueError("timeout_seconds must be between 1 and 60")
        if task_config["max_retries"] not in (0, 1, 2):
            raise ValueError("max_retries must be 0, 1 or 2")
        # Model-assisted auditing is not silently activated by uploads. The model
        # test endpoint is separately explicit about billing.
        if task_config["model_enabled"]:
            raise ValueError("Automatic model-assisted auditing is not enabled; explicit model connection test only")
        task_id, timestamp = uid("task"), now()
        digest = sha256(raw).hexdigest()
        with self.store.connect() as db:
            db.execute("INSERT OR IGNORE INTO uploads VALUES(?,?,?)", (digest, raw, timestamp))
            db.execute("INSERT INTO tasks(id,name,filename,upload_hash,mode,status,created_at,updated_at,config,diagnostics) "
                       "VALUES(?,?,?,?,?,?,?,?,?,?)",
                       (task_id, name.strip()[:200] or filename, filename, digest, mode, "queued", timestamp, timestamp,
                        dump(task_config), dump([diagnostic(d) for d in document.diagnostics])))
            for e in document.entries:
                diagnostics = [diagnostic(d) for d in e.diagnostics]
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

    def original(self, task_id):
        row = self.raw_task(task_id)
        return self.store.one("SELECT content FROM uploads WHERE hash=?", (row["upload_hash"],))["content"]

    def entry(self, task_id, entry_id, detail=True):
        row = self.store.one("SELECT * FROM entries WHERE task_id=? AND id=?", (task_id, entry_id))
        if not row:
            raise KeyError("Entry not found")
        fields, result = load(row["input_fields"]), load(row["result"], {})
        task = self.raw_task(task_id)
        item = {
            "id": row["id"], "task_id": task_id, "ordinal": row["ordinal"], "citation_key": row["citation_key"],
            "entry_type": row["entry_type"], "title": fields.get("title", "Untitled entry"),
            "author": fields.get("author", ""), "year": fields.get("year", ""), "execution_status": row["status"],
            "identity_state": result.get("identity_state", "insufficient"),
            "version_status": result.get("version_status", "unknown"),
            "recommendation_status": result.get("recommendation_status", "unavailable"),
            "has_conflict": result.get("has_conflict", False), "conflict_count": result.get("conflict_count", 0),
            "completion_count": result.get("completion_count", 0), "article_id": result.get("article_id"),
            "current_version_id": result.get("current_version_id"), "recommended_version_id": result.get("recommended_version_id"),
            "mode": task["mode"], "diagnostics": load(row["diagnostics"]),
        }
        if detail:
            item.update({"original_bib": row["raw_bib"], "fields": fields,
                         "field_assessments": [], "proposals": [], "sources": [], "candidates": [], "calls": [],
                         "warnings": [], "identity_reason": "Awaiting audit", "current_version": None,
                         "recommended_version": None, "version_relations": [],
                         "recommendation": {"status": "unavailable", "reason": "Awaiting audit", "differences": []}})
            if result:
                item.update(presenters.assessment(result))
        return item

    def entries(self, task_id, detail=False):
        self.raw_task(task_id)
        return [self.entry(task_id, row["id"], detail) for row in self.store.all(
            "SELECT id FROM entries WHERE task_id=? ORDER BY ordinal", (task_id,))]

    def task(self, task_id):
        row = self.raw_task(task_id)
        entries = self.entries(task_id)
        summary = {k: 0 for k in ("total", "completed", "running", "queued", "failed", "cancelled", "parse_errors",
                                  "confirmed", "conflicts", "unresolved", "upgrade_available", "completion_available", "problem_entries")}
        summary["total"] = len(entries)
        for e in entries:
            status = e["execution_status"]
            summary["completed" if status == "partial" else status] += 1
            parse_error = any(d.get("severity") == "error" for d in e["diagnostics"])
            summary["parse_errors"] += parse_error
            summary["confirmed"] += e["identity_state"] == "confirmed"
            summary["conflicts"] += e["has_conflict"]
            summary["unresolved"] += e["identity_state"] != "confirmed"
            summary["upgrade_available"] += e["recommendation_status"] == "upgrade_available"
            summary["completion_available"] += e["completion_count"] > 0
            summary["problem_entries"] += e["has_conflict"] or e["identity_state"] != "confirmed" or parse_error
        return {
            "id": task_id, "name": row["name"], "filename": row["filename"], "mode": row["mode"],
            "original_sha256": row["upload_hash"], "byte_size": len(self.original(task_id)), "encoding": "utf-8",
            "created_at": row["created_at"], "updated_at": row["updated_at"], "execution_status": row["status"],
            "error": row["error"], "config": load(row["config"]), "summary": summary,
            "warnings": load(row["diagnostics"]) + ([{"code": "demo", "message": "Demonstration fixtures; not online verification"}]
                                                     if row["mode"] == "demo" else []),
            "cost": deepcopy(UNKNOWN_COST), "original_url": f"/api/tasks/{task_id}/original",
            "report_url": f"/api/tasks/{task_id}/report",
        }

    def call(self, task_id, entry_id, item, mode):
        # A request may finish after cancellation/deletion. Its result belongs to
        # neither a frozen report nor a deleted task and must not revive either.
        if task_id:
            task = self.store.one("SELECT cancelled FROM tasks WHERE id=?", (task_id,))
            if not task or task["cancelled"]:
                return None
        config = self.settings.get()
        safe = redact(item, secrets_from(config))
        record = {"id": uid("call"), "task_id": task_id, "entry_id": entry_id,
                  "provider": safe.get("provider", "unknown"), "kind": safe.get("kind", "source"),
                  "operation": safe.get("operation", "lookup"), "status": {"mock": "success", "empty": "no_result", "error": "failed"}.get(
                      safe.get("status"), safe.get("status", "failed")),
                  "started_at": safe.get("started_at", now()), "duration_ms": safe.get("duration_ms", 0),
                  "attempt": safe.get("attempt", 1), "cache_hit": safe.get("cache_hit", False), "mode": mode,
                  "error_code": safe.get("error_code"), "message": safe.get("message", safe.get("error")),
                  "usage": safe.get("usage"), "cost": safe.get("cost") or deepcopy(UNKNOWN_COST)}
        # Request summaries are deliberately optional: never persist whole prompts/Bib by default.
        if safe.get("request_summary"):
            record["request_summary"] = safe["request_summary"]
        self.store.execute("INSERT INTO calls VALUES(?,?,?,?,?)",
                           (record["id"], task_id, entry_id, record["started_at"], dump(record)))
        return record

    def calls(self, task_id=None, entry_id=None):
        rows = self.store.all("SELECT data FROM calls ORDER BY created_at,id")
        items = [load(r["data"]) for r in rows]
        return [r for r in items if (not task_id or r["task_id"] == task_id) and (not entry_id or r["entry_id"] == entry_id)]

    def cancel(self, task_id):
        task = self.raw_task(task_id)
        if task["status"] not in TERMINAL:
            with self.store.connect() as db:
                db.execute("UPDATE tasks SET cancelled=1,status='cancelled',updated_at=? WHERE id=?", (now(), task_id))
                db.execute("UPDATE entries SET status='cancelled' WHERE task_id=? AND status IN ('queued','running')", (task_id,))
            self.freeze(task_id)
        return self.task(task_id)

    def retry(self, task_id):
        task = self.raw_task(task_id)
        return self.create_task(self.original(task_id), task["filename"], task["name"], task["mode"], load(task["config"]))

    def delete(self, task_id):
        task = self.raw_task(task_id)
        if task["status"] not in TERMINAL:
            raise ValueError("Cancel this running task before deleting it")
        self.store.execute("DELETE FROM tasks WHERE id=?", (task_id,))
        return {"deleted": True, "knowledge_preserved": True}

    def freeze(self, task_id):
        row = self.raw_task(task_id)
        if row["report"]:
            return load(row["report"])
        report = {"task": self.task(task_id), "entries": self.entries(task_id, True),
                  "calls": self.calls(task_id), "generated_at": now()}
        if row["status"] in TERMINAL:
            self.store.execute("UPDATE tasks SET report=? WHERE id=? AND report IS NULL", (dump(report), task_id))
        return report

    async def start(self):
        self.stopping = False
        # Completed entries/reports are never reprocessed. An interrupted entry is
        # retried from its original input, not from an inherited conclusion.
        with self.store.connect() as db:
            db.execute("UPDATE tasks SET status='queued',updated_at=? WHERE status='running' AND cancelled=0", (now(),))
            db.execute("UPDATE entries SET status='queued' WHERE status='running'")
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
                continue
            self.wake.clear()
            try:
                await asyncio.wait_for(self.wake.wait(), timeout=1)
            except TimeoutError:
                pass

    def is_cancelled(self, task_id):
        task = self.store.one("SELECT cancelled FROM tasks WHERE id=?", (task_id,))
        return self.stopping or not task or bool(task["cancelled"])

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

    async def run_task(self, task_id):
        if self.is_cancelled(task_id):
            return
        changed = self.store.execute("UPDATE tasks SET status='running',attempts=attempts+1,updated_at=? "
                                     "WHERE id=? AND status='queued'", (now(), task_id))
        if not changed:
            return
        task = self.raw_task(task_id)
        config = self.settings.get()
        overrides = load(task["config"])
        config.update({"request_timeout_seconds": overrides["timeout_seconds"], "retries": overrides["max_retries"]})
        for row in self.store.all("SELECT * FROM entries WHERE task_id=? AND status='queued' ORDER BY ordinal", (task_id,)):
            if self.is_cancelled(task_id):
                break
            self.store.execute("UPDATE entries SET status='running' WHERE id=?", (row["id"],))
            fields = load(row["input_fields"])
            entry_input = {"fields": fields, "entry_type": row["entry_type"], "citation_key": row["citation_key"],
                           "editable": bool(row["editable"])}
            async def audit(item, entry_id=row["id"]):
                return self.call(task_id, entry_id, item, task["mode"])
            try:
                parse_errors = [d for d in load(row["diagnostics"]) if d.get("severity") == "error"]
                if parse_errors:
                    result = assess(self.knowledge, entry_input, [], task["mode"], [])
                    result["identity_reason"] = "Parse errors prevent reliable audit; original entry retained"
                    result["warnings"].extend(d["message"] for d in parse_errors)
                    status = "failed"
                else:
                    existing = self.reusable_sources(fields, task["mode"], config["knowledge_ttl_seconds"])
                    if existing:
                        await audit({"provider": "knowledge", "operation": "reuse", "status": "success", "cache_hit": True})
                        sources = existing
                    else:
                        used = sum(not c["cache_hit"] and c["kind"] == "source" for c in self.calls(task_id))
                        remaining = overrides["request_budget"] - used
                        if remaining <= 0:
                            await audit({"provider": "budget", "operation": "lookup", "status": "budget_exhausted",
                                         "message": "Task request budget exhausted"})
                            records = []
                        else:
                            config["max_source_requests"] = min(config["max_source_requests"], remaining)
                            records = await self.registry.lookup(
                                {**fields, "entrytype": row["entry_type"]},
                                "mock" if task["mode"] == "demo" else "online", config,
                                audit=audit, cancelled=lambda: self.is_cancelled(task_id))
                        if self.is_cancelled(task_id):
                            break
                        sources = self.knowledge.ingest(records, "mock" if task["mode"] == "demo" else "online")
                    calls = self.calls(task_id, row["id"])
                    result = assess(self.knowledge, entry_input, sources, task["mode"], calls)
                    status = "partial" if any(c["status"] in {"failed", "rate_limited", "budget_exhausted", "partial"}
                                               for c in calls) else "completed"
                if self.is_cancelled(task_id):
                    break
                self.store.execute("UPDATE entries SET status=?,result=? WHERE id=? AND status='running'",
                                   (status, dump(result), row["id"]))
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - isolate untrusted providers and keep queue alive
                # Raw provider exception bodies can contain credentials. Persist only
                # stable safe errors and retain other entries, never promote a failure.
                await audit({"provider": "audit", "status": "failed", "error_code": "audit_failed",
                             "message": "Entry processing failed; original retained"})
                result = assess(self.knowledge, entry_input, [], task["mode"], self.calls(task_id, row["id"]))
                self.store.execute("UPDATE entries SET status='failed',result=? WHERE id=? AND status='running'",
                                   (dump(result), row["id"]))
            self.store.execute("UPDATE tasks SET updated_at=? WHERE id=?", (now(), task_id))
            await asyncio.sleep(0)
        if self.is_cancelled(task_id):
            return
        statuses = [r["status"] for r in self.store.all("SELECT status FROM entries WHERE task_id=?", (task_id,))]
        status = "failed" if all(s == "failed" for s in statuses) else "partial" if any(s != "completed" for s in statuses) else "completed"
        self.store.execute("UPDATE tasks SET status=?,updated_at=? WHERE id=?", (status, now(), task_id))
        self.annotate_duplicates(task_id)
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
                self.store.execute("UPDATE entries SET result=? WHERE id=?", (dump(result), row["id"]))

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
                    "changes": changes, "warnings": [], "blocked": [{**b, "entry_id": parser_map.get(b.get("entry_id"), b.get("entry_id"))} for b in preview.blocked],
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
