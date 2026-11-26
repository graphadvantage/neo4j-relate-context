"""Serialize short-lived Relate CLI operations across MCP connections."""

import os
from pathlib import Path
import threading
import time


class FileLock:
    def __init__(self, path, blocking=True):
        self.path = Path(path)
        self.blocking = blocking
        self.thread_lock = threading.Lock()
        self.owner = None

    def __enter__(self):
        self.thread_lock.acquire()
        try:
            self.owner = self.path.open("a+b")
            if os.name == 'nt':
                import msvcrt
                self.owner.seek(0, 2)
                if not self.owner.tell():
                    self.owner.write(b'0'); self.owner.flush()
                while True:
                    try:
                        self.owner.seek(0)
                        msvcrt.locking(self.owner.fileno(), msvcrt.LK_NBLCK, 1)
                        break
                    except OSError:
                        if not self.blocking:
                            raise BlockingIOError('Lock already held') from None
                        time.sleep(0.05)
            else:
                import fcntl
                fcntl.flock(self.owner, fcntl.LOCK_EX | (0 if self.blocking else fcntl.LOCK_NB))
        except BaseException:
            if self.owner is not None:
                self.owner.close()
                self.owner = None
            self.thread_lock.release()
            raise
        return self

    def __exit__(self, *exc):
        try:
            self.owner.close()
            self.owner = None
        finally:
            self.thread_lock.release()


class DatabaseLock(FileLock):
    def __init__(self, database):
        super().__init__(Path(database).with_suffix('.rl.lock'))
