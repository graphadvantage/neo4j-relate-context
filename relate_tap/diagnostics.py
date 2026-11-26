"""Durable, redacted console diagnostics; recovery does not erase incidents."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import threading
import uuid

from memory_graph import redact
from .bootstrap import atomic_json
from .privacy import sanitize


class Diagnostics:
    def __init__(self, directory, limit=200):
        self.state = Path(directory)/'errors.json'
        self.log = Path(directory)/'errors.jsonl'
        self.state.parent.mkdir(parents=True, exist_ok=True)
        self.limit, self.lock = limit, threading.Lock()
        self.entries = json.loads(self.state.read_text()) if self.state.exists() else []

    def _save(self, changes):
        if not changes:
            return
        fd = os.open(self.log, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        with os.fdopen(fd, 'a') as output:
            for entry in changes:
                output.write(json.dumps(entry)+'\n')
            output.flush()
            os.fsync(output.fileno())
        self.entries = self.entries[-self.limit:]
        atomic_json(self.state, self.entries)

    def update(self, errors):
        """Record new errors and recovery transitions once, without poll spam."""
        now = datetime.now(timezone.utc).isoformat()
        with self.lock:
            active = {e['key']: e for e in self.entries if e.get('active')}
            present, changes = set(), []
            for error in errors:
                safe = sanitize(error)
                key = hashlib.sha256(json.dumps(safe, sort_keys=True).encode()).hexdigest()
                present.add(key)
                if key not in active:
                    entry = {**safe, 'id': str(uuid.uuid4()), 'key': key, 'first_seen': now,
                             'active': True, 'resolved_at': None}
                    self.entries.append(entry)
                    active[key] = entry
                    changes.append(dict(entry))
            for key, entry in active.items():
                if key not in present:
                    entry.update(active=False, resolved_at=now)
                    changes.append(dict(entry))
            self._save(changes)

    def report(self, source, message):
        with self.lock:
            entry = {'id': str(uuid.uuid4()), 'source': redact(source),
                     'error': redact(message), 'first_seen': datetime.now(timezone.utc).isoformat(),
                     'active': False, 'resolved_at': None}
            self.entries.append(entry)
            self._save([entry])
            return dict(entry)

    def clear(self):
        """Clear retained history, preserving active failures and a recoverable archive."""
        with self.lock:
            retained = [dict(entry) for entry in self.entries if entry.get('active')]
            cleared = len(self.entries)-len(retained)
            if self.log.exists():
                archive = self.state.parent/'errors-archive'
                archive.mkdir(mode=0o700, exist_ok=True)
                os.replace(self.log, archive/(str(uuid.uuid4())+'.jsonl'))
            self.entries = retained
            if retained:
                self._save(retained)
            else:
                atomic_json(self.state, [])
            return {'cleared': cleared, 'active': len(retained)}

    def snapshot(self):
        with self.lock:
            return [dict(entry) for entry in reversed(self.entries)]
