import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from app.locking import InstanceLock

class LockingTests(unittest.TestCase):
    @unittest.skipIf(os.name=='nt','POSIX lock test')
    def test_exclusive_lock_and_release(self):
        with tempfile.TemporaryDirectory() as directory:
            first=InstanceLock(Path(directory)/'lock').acquire()
            with self.assertRaises(RuntimeError): InstanceLock(Path(directory)/'lock').acquire()
            first.release()
            other=InstanceLock(Path(directory)/'lock').acquire();other.release()
    def test_windows_msvcrt_branch(self):
        with tempfile.TemporaryDirectory() as directory:
            locking=Mock();mod=SimpleNamespace(locking=locking,LK_NBLCK=2,LK_UNLCK=0)
            lock=InstanceLock(Path(directory)/'lock');lock.windows=True
            with patch.dict('sys.modules',{'msvcrt':mod}):
                lock.acquire()
                self.assertEqual(1,lock.path.stat().st_size)
                self.assertEqual((2,1),locking.call_args.args[1:])
                lock.release()
                self.assertEqual((0,1),locking.call_args.args[1:])
    def test_windows_locked_file_closes(self):
        with tempfile.TemporaryDirectory() as directory:
            mod=SimpleNamespace(locking=Mock(side_effect=OSError('busy')),LK_NBLCK=2,LK_UNLCK=0)
            lock=InstanceLock(Path(directory)/'lock');lock.windows=True
            with patch.dict('sys.modules',{'msvcrt':mod}):
                with self.assertRaises(RuntimeError):lock.acquire()
            self.assertIsNone(lock.handle)

if __name__=='__main__':unittest.main()
