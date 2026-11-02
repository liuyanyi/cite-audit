"""Replay an existing achievement upload in an isolated API-backed test store.

The original database is opened read-only. No historical report, source or Bib
is overwritten. --online explicitly performs fresh source retrieval (no LLM).
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sqlite3
import sys
import tempfile
from collections import Counter
from copy import deepcopy
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cite_audit.api import create_app
from cite_audit.matching import normalize


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Path(".cite-audit-data/cite-audit.sqlite3"))
    parser.add_argument("--output", type=Path, default=Path(".cache/achievement-validation"))
    parser.add_argument("--online", action="store_true")
    args = parser.parse_args()
    original = sqlite3.connect(args.database.resolve().as_uri() + "?mode=ro", uri=True)
    original.row_factory = sqlite3.Row
    task = original.execute("SELECT * FROM tasks WHERE lower(filename) LIKE '%achievement%' "
                            "ORDER BY created_at DESC LIMIT 1").fetchone()
    if not task:
        raise SystemExit("No achievement upload found")
    raw = original.execute("SELECT content FROM uploads WHERE hash=?", (task["upload_hash"],)).fetchone()[0]
    old_rows = original.execute("SELECT * FROM entries WHERE task_id=? ORDER BY ordinal", (task["id"],)).fetchall()
    replay = {}
    for row in old_rows:
        result = json.loads(row["result"] or "{}")
        items = result.get("candidates", []) + result.get("excluded_candidates", []) + result.get("sources", [])
        items += [r for item in list(items) for r in item.get("records", [])]
        references = {(r["provider"], r["record_id"]) for r in items}
        records = []
        for provider, record_id in sorted(references):
            # Older candidate DTOs have no snapshot ID: use the latest source
            # captured no later than the original task completion, never new data.
            source = original.execute("SELECT data FROM sources WHERE provider=? AND record_id=? AND created_at<=? "
                                      "ORDER BY created_at DESC LIMIT 1", (provider, record_id, task["updated_at"])).fetchone()
            if source:
                records.append(json.loads(source["data"]))
        replay[normalize(json.loads(row["input_fields"]).get("title"))] = (records, result.get("calls", []))

    class Replay:
        async def lookup(self, fields, mode, settings, *, audit, cancelled):
            records, calls = replay.get(normalize(fields.get("title")), ([], []))
            for call in calls:
                # Preserve coverage failures, not old billing or request counts.
                await audit({"provider": call["provider"], "operation": "snapshot_replay", "status": call["status"],
                             "error_code": call.get("error_code"),
                             "message": "Historical source replay; no network request", "cache_hit": True})
            return deepcopy(records)

    args.output.mkdir(parents=True, exist_ok=True)
    data_dir = Path(tempfile.mkdtemp(prefix="online-" if args.online else "replay-", dir=args.output))
    app = create_app(data_dir, start_worker=False, registry=None if args.online else Replay())
    service = app.state.service
    if args.online:
        # Keys stay in the backend database and are never emitted in the report.
        setting = original.execute("SELECT data FROM settings WHERE id=1").fetchone()
        if setting:
            service.store.execute("UPDATE settings SET data=? WHERE id=1", (setting[0],))
    with TestClient(app) as client:
        response = client.post("/api/tasks", files={"file": (task["filename"], raw)},
                               data={"mode": "online", "name": "achievement identity validation"})
        assert response.status_code == 201, response.text
        task_id = response.json()["id"]
        asyncio.run(service.run_task(task_id))
        report = client.get(f"/api/tasks/{task_id}/report").json()
        assert client.get(f"/api/tasks/{task_id}/original").content == raw
        entries = service.entries(task_id, True)
        for entry in entries:
            detail = client.get(f"/api/tasks/{task_id}/entries/{entry['id']}")
            assert detail.status_code == 200 and detail.json()["identity_state"] == entry["identity_state"]
        preview = client.post(f"/api/tasks/{task_id}/export-preview", json={"selections": []})
        assert preview.status_code == 200
        # Export preview must preserve bytes even for undecodable source text.
        assert service.export_preview(task_id, [])[1] == raw
        assert client.get(f"/api/tasks/{task_id}/report").json() == report
    old = {r["citation_key"]: json.loads(r["result"] or "{}") for r in old_rows}
    summary = {"mode": "online" if args.online else "historical_snapshot_replay", "original_task_id": task["id"],
               "validation_task_id": task_id, "validation_data_dir": str(data_dir),
               "original_sha256": hashlib.sha256(raw).hexdigest(),
               "before": dict(Counter(r.get("identity_state") for r in old.values())),
               "after": dict(Counter(e["identity_state"] for e in entries)),
               "entries": [{"key": e["citation_key"], "before": old[e["citation_key"]].get("identity_state"),
                            "after": e["identity_state"], "reason": e["identity_reason"],
                            "candidates_before": len(old[e["citation_key"]].get("candidates", [])),
                            "candidates_after": len(e["candidates"]), "excluded": len(e.get("excluded_candidates", [])),
                            "conflicts": [{"field": a["field"], "original": a["original_value"], "reference": a["reference_value"]}
                                          for a in e["field_assessments"] if a["judgment"] == "conflict"],
                            "calls": dict(Counter(c["status"] for c in e["calls"]))} for e in entries]}
    # Read-only source database remains byte-for-byte unchanged at the record level.
    assert original.execute("SELECT report FROM tasks WHERE id=?", (task["id"],)).fetchone()[0] == task["report"]
    output = args.output / ("online.json" if args.online else "replay.json")
    output.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
