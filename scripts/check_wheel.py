"""Smoke-test an installed wheel away from the checkout, without external calls."""
from pathlib import Path
import os
import subprocess
import sys
import tempfile


def main():
    directory = Path(sys.argv[1] if len(sys.argv) > 1 else 'dist').resolve()
    wheels = list(directory.glob('cite_audit-*.whl'))
    if not wheels:
        raise SystemExit(f'No cite-audit wheel in {directory}')
    wheel = max(wheels, key=lambda path: path.stat().st_mtime)
    with tempfile.TemporaryDirectory() as temporary:
        target = Path(temporary) / 'installed'
        subprocess.run([sys.executable, '-m', 'pip', 'install', '--no-deps', '--target', str(target), str(wheel)], check=True)
        environment = {**os.environ, 'PYTHONPATH': str(target), 'CITE_AUDIT_WHEEL_ROOT': str(target)}
        subprocess.run([sys.executable, '-c', '''
import os
from pathlib import Path
import re
import tempfile
import app
from fastapi.testclient import TestClient
from app.main import create_app

assert Path(app.__file__).resolve().is_relative_to(Path(os.environ['CITE_AUDIT_WHEEL_ROOT']).resolve())
with tempfile.TemporaryDirectory() as data:
    application = create_app(data, start_worker=False)
    with TestClient(application) as client:
        page = client.get('/')
        assert page.status_code == 200, page.text
        assert 'type="module"' in page.text, 'React module entry missing'
        assets = re.findall(r'(?:src|href)="(/static/[^\"]+)"', page.text)
        assert any(path.endswith('.js') for path in assets), assets
        assert any(path.endswith('.css') for path in assets), assets
        for path in assets:
            response = client.get(path)
            assert response.status_code == 200, (path, response.status_code)
            assert response.content, path
            assert 'text/html' not in response.headers.get('content-type', ''), path
        sample = client.get('/examples/demo.bib')
        assert sample.status_code == 200 and sample.content
        task = client.post('/api/tasks', files={'file': ('demo.bib', sample.content)}, data={'mode': 'mock'})
        assert task.status_code == 201, task.text
        while application.state.worker.run_next():
            pass
        detail = client.get('/api/tasks/' + task.json()['id']).json()
        assert detail['entries'] and detail['api_calls'] == 0
        original = client.get('/api/tasks/' + task.json()['id'] + '/source')
        assert original.content == sample.content
        task_id = task.json()['id']
        preview = client.get('/api/tasks/' + task_id + '/export-preview')
        assert preview.status_code == 200 and preview.json()['entries']
        exported = client.post('/api/tasks/' + task_id + '/export', json={'selections': []})
        assert exported.status_code == 200 and exported.json()['bibtex'].encode() == sample.content
        library = client.get('/api/library').json()
        assert library['total'] > 0
        assert client.get('/api/library/' + library['items'][0]['id']).json()['snapshots']
        audit = client.get('/api/audit').json()
        assert audit['summary']['total_calls'] == 0
        assert client.delete('/api/tasks/' + task_id).status_code == 200
        assert client.get('/api/library').json()['total'] == library['total']
        assert client.get('/api/audit').json()['total'] == audit['total']
print('Installed wheel: React assets, offline task, source/export preservation and persistent library/audit passed.')
'''], check=True, cwd=temporary, env=environment)


if __name__ == '__main__':
    main()
