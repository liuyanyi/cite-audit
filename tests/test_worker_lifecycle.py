"""Runs the actual platform lock and background worker, without network calls."""
import tempfile
import time
import unittest
from app.bibtex import parse_bibtex
from app.config import Config
from app.storage import Database
from app.worker import Worker

class WorkerLifecycleTests(unittest.TestCase):
    def test_native_lock_background_queue_and_clean_shutdown(self):
        with tempfile.TemporaryDirectory() as directory:
            db,config=Database(directory),Config(directory)
            raw=b'@article{native,title={Native Worker},author={Example, Alice},year={2024}}'
            task=db.create_task('native.bib',raw,parse_bibtex(raw.decode()),'mock','off',config.fingerprint('mock'))
            first=Worker(db,config);first.start()
            try:
                with self.assertRaises(RuntimeError):Worker(db,config).start()
                deadline=time.monotonic()+10
                while time.monotonic()<deadline:
                    current=db.task(task['id'])
                    if current['status']=='completed':break
                    time.sleep(.02)
                self.assertEqual('completed',current['status'])
                self.assertEqual(0,current['api_calls'])
                self.assertEqual(raw,db.source(task['id'])['content'])
            finally:first.stop()
            self.assertFalse(first.thread.is_alive())
            second=Worker(db,config);second.start();second.stop()
            self.assertFalse(second.thread.is_alive())

if __name__=='__main__':unittest.main()
