"""Run the dependency-free JS DOM contract smoke, without live provider calls.

This validates rendering branches and accessibility attribute serialization, not
browser layout. Run: python -m unittest discover -s tests -v
Node.js is optional for application users and required only for this JS test.
"""
from pathlib import Path
import json
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
    @unittest.skipUnless(shutil.which('node'), 'Node.js is needed for frontend contract tests')
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
            result = subprocess.run([shutil.which('node'), str(ROOT / 'tests/frontend_smoke.js'), str(fixture)],
                                    cwd=ROOT, text=True, capture_output=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('"rendering":"passed"', result.stdout)
            self.assertIn('Unverified DOI labels and filters passed.', result.stdout)


if __name__ == '__main__':
    unittest.main()
