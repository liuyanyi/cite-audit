"""Deterministic synthetic workload metrics, not real-world accuracy claims."""
import json
from pathlib import Path
import sys
import tempfile
import time
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.main import create_app
from app.bibtex import parse_bibtex

def main():
    source = (Path(__file__).resolve().parent.parent / 'examples/demo.bib').read_bytes()
    with tempfile.TemporaryDirectory() as directory:
        app = create_app(directory, start_worker=False)
        db, config, worker = app.state.db, app.state.config, app.state.worker
        parsed = parse_bibtex(source.decode())
        start = time.perf_counter()
        first = db.create_task('demo.bib', source, parsed, 'mock', 'off', config.fingerprint('mock'))
        while worker.run_next(): pass
        first = db.task(first['id'], True)
        elapsed = round((time.perf_counter()-start)*1000, 2)
        second = db.create_task('demo.bib', source, parsed, 'mock', 'off', config.fingerprint('mock'), first['id'])
        while worker.run_next(): pass
        second = db.task(second['id'], True)
        unresolved = sum(e['result']['identity_state'] != 'unique' for e in first['entries'])
        print(json.dumps({'dataset':'8 synthetic offline fixtures; NOT real accuracy measurement', 'entries':first['total'], 'completed':first['completed'], 'processing_failures':first['failed'], 'unresolved':unresolved, 'unresolved_fraction':unresolved/first['total'], 'first_run_ms':elapsed, 'external_calls':first['api_calls']+second['api_calls'], 'repeat_cache_hits':second['cache_hits'], 'repeat_total':second['total'], 'real_field_accuracy':None,'real_false_positive_rate':None,'real_version_mismatch_rate':None},ensure_ascii=False,indent=2))

if __name__=='__main__':main()
