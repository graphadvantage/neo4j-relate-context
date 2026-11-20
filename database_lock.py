"""Serialize short-lived Relate CLI operations across MCP connections."""

import fcntl
from pathlib import Path
import threading


class DatabaseLock:
    def __init__(self, database):
        self.path = Path(database).with_suffix(".rl.lock")
        self.thread_lock = threading.Lock()
        self.owner = None

    def __enter__(self):
        self.thread_lock.acquire()
        try:
            self.owner = self.path.open("a")
            fcntl.flock(self.owner, fcntl.LOCK_EX)
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
