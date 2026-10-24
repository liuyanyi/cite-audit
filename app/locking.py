"""Portable advisory lock for the supported one-process SQLite worker."""
from __future__ import annotations
import os
from pathlib import Path

class InstanceLock:
    def __init__(self, path):
        self.path = Path(path)
        self.handle = None
        self.windows = os.name == 'nt'

    def acquire(self):
        self.handle = self.path.open('a+b')
        try:
            if self.windows:
                import msvcrt
                self.handle.seek(0, os.SEEK_END)
                if self.handle.tell() == 0:
                    self.handle.write(b'0')
                    self.handle.flush()
                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.handle.close()
            self.handle = None
            raise RuntimeError('Another cite-audit process is already using this data directory; run one worker only') from None
        return self

    def release(self):
        if self.handle is None: return
        try:
            if self.windows:
                import msvcrt
                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle, fcntl.LOCK_UN)
        finally:
            self.handle.close()
            self.handle = None
