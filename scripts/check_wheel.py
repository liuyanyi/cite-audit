"""Check installed package assets and offline API behavior outside the checkout."""

import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

SMOKE = r'''
import hashlib
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
import cite_audit
from fastapi.testclient import TestClient
from cite_audit.api import create_app

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
        ids = []
        for _ in range(2):
            upload = client.post("/api/tasks", files={"file": ("smoke.bib", raw, "application/x-bibtex")}, data={"mode": "demo"})
            assert upload.status_code == 201, upload.text
            task = upload.json()
            ids.append(task["id"])
            assert task["mode"] == "demo"
            assert task["original_sha256"] == hashlib.sha256(raw).hexdigest()
            assert client.get(task["original_url"]).content == raw
        assert ids[0] != ids[1], "Repeated uploads must be independent tasks"
print("Installed wheel: React assets, CLI, API and immutable repeated uploads passed (offline).")
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
