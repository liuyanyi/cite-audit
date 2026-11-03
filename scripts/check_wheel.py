"""Check installed package assets and offline API behavior outside the checkout."""

import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

SMOKE = r'''
import asyncio
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from copy import deepcopy
from pathlib import Path
import cite_audit
from fastapi.testclient import TestClient
from cite_audit.api import create_app
from cite_audit.sources import MOCK_BERT_RECORDS

assert Path(cite_audit.__file__).resolve().is_relative_to(Path(os.environ["WHEEL_ROOT"]).resolve())
subprocess.run([sys.executable, "-m", "cite_audit.cli", "--help"], check=True, capture_output=True)
with tempfile.TemporaryDirectory() as data:
    with TestClient(create_app(data_dir=data, start_worker=False)) as client:
        response = client.get("/")
        assert response.status_code == 200, response.text
        assert 'type="module"' in response.text
        assets = re.findall(r'(?:src|href)="([^\"]+\.(?:js|css))"', response.text)
        assert assets, "Built frontend resources are absent"
        for asset in assets:
            result = client.get(asset)
            assert result.status_code == 200, asset
            assert result.content and "text/html" not in result.headers.get("content-type", "")
        for route in ("/tasks", "/knowledge", "/settings"):
            assert client.get(route).text == response.text, route
        for route in ("/api/health", "/api/tasks", "/api/articles", "/api/settings"):
            assert client.get(route).status_code == 200, route
        assert client.get("/api/nonexistent").status_code == 404
        raw = b'% installed-wheel fixture\r\n@misc{sample, title={Unknown Example}, year={2026}}\r\n'
        runtime = client.get("/api/settings").json()["runtime"]
        assert runtime["entry_request_budget"] == 18 and runtime["request_budget"] == 180
        config = json.dumps({"entry_request_budget": 2, "request_budget": 1})
        warning = client.post("/api/tasks", files={"file": ("smoke.bib", raw)}, data={"config": config})
        assert warning.status_code == 409, warning.text
        assert warning.json()["error"]["code"] == "budget_confirmation_required"
        assert warning.json()["error"]["details"]["entry_count"] == 1
        assert client.get("/api/tasks").json()["total"] == 0
        ids = []
        for _ in range(2):
            upload = client.post("/api/tasks", files={"file": ("smoke.bib", raw, "application/x-bibtex")},
                                 data={"mode": "demo", "config": config, "confirm_budget": "true"})
            assert upload.status_code == 201, upload.text
            task = upload.json()
            ids.append(task["id"])
            assert task["mode"] == "demo"
            assert task["config"]["entry_request_budget"] == 2 and task["config"]["request_budget"] == 1
            assert task["original_sha256"] == hashlib.sha256(raw).hexdigest()
            assert client.get(task["original_url"]).content == raw
        assert ids[0] != ids[1], "Repeated uploads must be independent tasks"
        for task_id in ids:
            asyncio.run(client.app.state.service.run_task(task_id))
        client.app.state.service.knowledge.ingest(deepcopy(MOCK_BERT_RECORDS), "mock")
        knowledge = client.get("/api/articles", params={"q": "10.18653/V1/N19-1423", "mode": "demo"}).json()
        assert knowledge["total"] == 1 and knowledge["items"][0]["version_count"] == 2
        assert "versions" not in knowledge["items"][0]
        article = client.get(f"/api/articles/{knowledge['items'][0]['id']}").json()
        assert len(article["versions"]) == 2 and article["source_count"] == 3
        assert client.get("/api/articles", params={"q": "bert", "mode": "online"}).json()["total"] == 0
        assert client.get("/api/articles", params={"q": "bert", "page": 2, "page_size": 1}).json()["items"] == []
        report = client.get(f"/api/tasks/{ids[0]}/report").json()
        calls = client.get("/api/calls", params={"task_id": ids[0]}).json()["items"]
        assert calls and "response_summary" not in calls[0]
        detail = client.get(f"/api/calls/{calls[0]['id']}")
        assert detail.status_code == 200 and detail.json()["mode"] == "demo"
        assert detail.json()["response_summary"]["record_count"] == 0
        assert client.get(f"/api/tasks/{ids[0]}/report").json() == report
        assert client.get(f"/api/tasks/{ids[0]}").json()["finalized"] is True
        assert client.get(f"/api/tasks/{ids[0]}").json()["summary"]["not_found"] == 1
        classified = client.get(f"/api/tasks/{ids[0]}/entries", params={"classification": "not_found"}).json()
        assert classified["total"] == 1 and classified["items"][0]["classification"] == "not_found"
        events = client.get("/api/events", params={"task_id": ids[0], "after": 0})
        assert events.headers["content-type"].startswith("text/event-stream")
        assert "event: sync" in events.text and "event: done" in events.text
        assert all(isinstance(warning, str) for warning in report["task"]["warnings"])
        preview = client.post(f"/api/tasks/{ids[0]}/export-preview", json={"selections": []})
        assert preview.status_code == 200, preview.text
        output_hash = preview.json()["output_sha256"]
        artifact = client.post(f"/api/tasks/{ids[0]}/exports", json={
            "selections": [], "confirmed": True, "expected_output_sha256": output_hash,
        })
        assert artifact.status_code == 201, artifact.text
        assert client.get(artifact.json()["download_url"]).content == raw
        assert client.get(f"/api/tasks/{ids[0]}/original").content == raw
        retry = client.post(f"/api/tasks/{ids[0]}/entries/{report['entries'][0]['id']}/retry")
        assert retry.status_code == 200 and retry.json()["id"] == ids[0]
        assert retry.json()["report_revision"] == 2
        assert client.get("/api/tasks").json()["total"] == 2
        assert client.get(f"/api/tasks/{ids[0]}/reports/1").json() == report
        asyncio.run(client.app.state.service.run_task(ids[0]))
        latest = client.get(f"/api/tasks/{ids[0]}/report").json()
        assert latest["entries"][0]["id"] == report["entries"][0]["id"]
        assert latest["task"]["report_revision"] == 2
        assert client.get(artifact.json()["download_url"]).content == raw
        assert client.delete(f"/api/tasks/{ids[0]}").status_code == 200
        assert client.get(f"/api/tasks/{ids[1]}/original").content == raw
        assert client.delete(f"/api/tasks/{ids[1]}").status_code == 200
print("Installed wheel: React assets, CLI, API, frozen report, safe export and shared-upload deletion passed (offline).")
'''


def main() -> None:
    directory = Path(sys.argv[1] if len(sys.argv) > 1 else "dist").resolve()
    wheels = list(directory.glob("cite_audit_remake-*.whl"))
    if not wheels:
        raise SystemExit(f"No application wheel found in {directory}")
    wheel = max(wheels, key=lambda item: item.stat().st_mtime)
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        if "cite_audit/static/index.html" not in names:
            raise SystemExit("Wheel is missing built UI. Run npm run build in frontend before uv build.")
        if not any(name.startswith("cite_audit/static/assets/") and name.endswith(".js") for name in names):
            raise SystemExit("Wheel is missing frontend JavaScript assets")
    with tempfile.TemporaryDirectory() as temporary:
        target = Path(temporary) / "installed"
        subprocess.run([
            "uv", "pip", "install", "--python", sys.executable, "--offline",
            "--no-deps", "--target", str(target), str(wheel),
        ], check=True)
        environment = {**os.environ, "PYTHONPATH": str(target), "WHEEL_ROOT": str(target)}
        subprocess.run([sys.executable, "-c", SMOKE], check=True, cwd=temporary, env=environment)


if __name__ == "__main__":
    main()
