"""Render real backend mock results through React; no live provider calls.

Node and frontend dev dependencies are required for this integration check.
CI installs both; Python-only application users may skip it explicitly.
"""
from pathlib import Path
import json
import os
import shutil
import subprocess
import tempfile
import unittest

from app.audit import assess
from app.bibtex import parse_bibtex
from app.config import Config
from app.providers import ProviderService
from app.storage import Database, PersistentCache

ROOT = Path(__file__).resolve().parent.parent


class FrontendContractTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node') and (ROOT / 'frontend/node_modules/vitest/vitest.mjs').exists(),
                         'Node.js and npm ci in frontend are needed for React integration tests')
    def test_mock_records_render_every_tab_and_filter(self):
        bib = '''@article{correct, title={A clear original title}, author={Smith, Jane}, year={2024}, journal={Journal of Example}}
@article{wrongdoi,title={Original wrong DOI record},author={Smith, Jane},year={2020},doi={10.5555/wrong}}
@misc{preprint,title={A useful citation tool},author={Smith, Jane},year={2024},eprint={2401.12345},archiveprefix={arXiv}}
@article{ambiguous,title={Similar candidate study},author={Smith, Jane},year={2024}}
@article{unresolved,title={Unresolved study},author={Smith, Jane},year={2024}}
@article{xss,title={<img src=x onerror=alert(1)>},author={Smith, Jane},year={2024}}
@article{chinese,title={基于证据的文献核验},author={张伟 and 李明},year={2023}}
@article{failure,title={DOI request failure},author={Smith, Jane},year={2024}}'''
        with tempfile.TemporaryDirectory() as directory:
            settings = Config(Path(directory)).get()
            settings['mode'] = 'mock'
            service = ProviderService(settings, PersistentCache(Database(directory)))
            entries = parse_bibtex(bib)['entries']
            try:
                for index, entry in enumerate(entries):
                    collected = service.collect(entry)
                    entry['result'] = assess(entry, collected['records'], collected['errors'])
                    entry['result']['calls'] = collected['calls']
                    entry['id'], entry['status'] = str(index), 'completed'
            finally:
                service.close()
            task = {'id': 'test', 'filename': 'test.bib', 'mode': 'mock',
                    'created_at': '2026-10-24T10:00:00Z', 'status': 'completed',
                    'entries': entries, 'total': len(entries), 'completed': len(entries),
                    'parse_issues': []}
            fixture = Path(directory) / 'task.json'
            fixture.write_text(json.dumps(task), encoding='utf-8')
            report = Path(directory) / 'react-report.json'
            environment = {**os.environ, 'CITE_AUDIT_FIXTURE': str(fixture),
                           'CITE_AUDIT_FIXTURE_REPORT': str(report)}
            result = subprocess.run(
                [shutil.which('node'), str(ROOT / 'frontend/node_modules/vitest/vitest.mjs'),
                 'run', 'src/backend-fixture.test.tsx'],
                cwd=ROOT / 'frontend', text=True, encoding='utf-8',
                capture_output=True, timeout=90, env=environment,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertTrue(report.exists(), result.stdout + result.stderr)
            self.assertEqual({'entries': 8, 'tabs': 32}, json.loads(report.read_text(encoding='utf-8')))



if __name__ == '__main__':
    unittest.main()
