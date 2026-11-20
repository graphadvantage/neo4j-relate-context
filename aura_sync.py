"""Replicate a consistent Relate snapshot to Aura in the background."""

import hashlib
import fcntl
import json
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import threading

from memory_graph import RELATE

KEYS = ("ContextProject:name", "ContextSession:id", "ContextMessage:id",
        "ContextMemory:id", "ContextNote:id", "ContextEntity:id", "ContextTurn:id",
        "ContextTool:id", "ContextQuery:id", "ContextReasoning:id")
DEFAULT_CREDENTIALS = Path(__file__).resolve().parent / "connections/enterprise-relate-memory.txt"


class AuraSync:
    def __init__(self, database, credentials, graph_lock, interval=30):
        self.database = database
        self.credentials = credentials
        self.graph_lock = graph_lock
        self.interval = interval
        self.wake = threading.Event()
        self.status = {"state": "pending"}
        threading.Thread(target=self.work, daemon=True).start()

    def flush(self):
        self.wake.set()
        return {"queued": True, **self.status}

    def work(self):
        previous = None
        while True:
            self.wake.clear()
            try:
                previous = self.push(previous)
                self.status = {"state": "synced"}
            except (OSError, ValueError, subprocess.SubprocessError) as exc:
                self.status = {"state": "retrying", "error": type(exc).__name__}
                print(f"Aura sync will retry: {type(exc).__name__}", file=sys.stderr)
            self.wake.wait(self.interval)

    def push(self, previous):
        # Each stdio connection has a worker; only one may push at a time.
        with self.database.with_suffix(".rl.sync.lock").open("a") as owner:
            fcntl.flock(owner, fcntl.LOCK_EX)
            return self._push(previous)

    def _push(self, previous):
        mode = self.credentials.stat().st_mode
        if not stat.S_ISREG(mode) or mode & 0o077:
            raise ValueError("Aura credentials must be a private file")
        with tempfile.TemporaryDirectory(prefix="relate-aura-") as directory:
            snapshot = Path(directory) / "graph.rl"
            with self.graph_lock:
                digest = hashlib.sha256()
                with self.database.open("rb") as source:
                    for block in iter(lambda: source.read(1024 * 1024), b""):
                        digest.update(block)
                fingerprint = digest.hexdigest()
                if fingerprint == previous:
                    return fingerprint
                subprocess.run([RELATE, "clone", str(self.database), str(snapshot)],
                               check=True, capture_output=True, timeout=30)
            command = [RELATE, "push", "--database", str(snapshot), "--to", str(self.credentials),
                       "--labels", ",".join(key.split(":")[0] for key in KEYS),
                       "--nodes-update", "skip", "--rels-update", "skip", "--json"]
            for key in KEYS:
                command.extend(["--merge-key", key])
            result = subprocess.run(command, check=True, capture_output=True, timeout=120)
            report = json.loads(result.stdout)
            for entity in report["entities"]:
                skipped = entity["skipped"]
                if skipped["unkeyed"] or skipped["dangling"]:
                    raise ValueError("Aura push skipped rows with missing keys or endpoints")
        return fingerprint
