"""One collector owns the capture graph. Spool replay is idempotent."""
from collections import deque
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import threading
import time

from database_lock import DatabaseLock
from memory_graph import direct_query
from .bootstrap import resolved_route

EVENT = """
MERGE (h:CaptureHost {id: $input.host_id})
ON CREATE SET h.name = $input.host
MERGE (c:CaptureConnection {id: $input.connection})
ON CREATE SET c.server = $input.server, c.agent = $input.agent,
              c.project = $input.project, c.username = $input.username
SET c.agent = coalesce($input.agent, c.agent), c.database = coalesce($input.database, c.database)
MERGE (h)-[:HAS_CONNECTION]->(c)
MERGE (e:CaptureEvent {id: $input.id})
ON CREATE SET e.kind = $input.kind, e.timestamp = $input.timestamp,
              e.payload = $input.payload
MERGE (c)-[:HAS_EVENT]->(e)
CALL (c) {
    WITH c WHERE $input.project IS NOT NULL
    MERGE (project:ContextProject {name: $input.project})
    MERGE (c)-[:IN_PROJECT]->(project)
    RETURN count(*) AS projects
}
CALL (e) {
    WITH e WHERE $input.call_id IS NOT NULL
    MERGE (call:CaptureCall {id: $input.call_id})
    MERGE (e)-[:FOR_CALL]->(call)
    RETURN count(*) AS calls
}
CALL (e) {
    WITH e WHERE $input.kind = 'request'
    MERGE (tool:ContextTool {id: $input.tool_id})
    ON CREATE SET tool.name = $input.tool, tool.server = $input.server,
                  tool.source = 'mcp_transport'
    MERGE (e)-[:USES_TOOL]->(tool)
    RETURN count(*) AS tools
}
CALL (e) {
    WITH e WHERE $input.cypher IS NOT NULL
    MERGE (q:ContextQuery {id: $input.call_id})
    ON CREATE SET q.cypher = $input.cypher, q.parameters = $input.parameters,
                  q.timestamp = $input.timestamp, q.username = $input.username,
                  q.source = 'mcp_transport', q.observation = 'submitted',
                  q.redacted = $input.redacted, q.sha256 = $input.query_sha256
    MERGE (e)-[:SUBMITTED_QUERY]->(q)
    RETURN count(*) AS queries
}
RETURN e.id AS id
"""


class CaptureStore:
    def __init__(self, database, spool, state, window=1000, route_agent=None, route_writer='native'):
        self.database, self.spool, self.state = Path(database), Path(spool), Path(state)
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self.state.parent.mkdir(parents=True, exist_ok=True)
        self.graph_lock = DatabaseLock(self.database)
        self.lock = threading.Lock()
        self.events = deque(maxlen=window)
        self.commits = deque(maxlen=100)
        self.errors = []
        self.revision = 0
        self.stats_lock = threading.Lock()
        self.stats_cache = None
        self.stats_revision = -1
        self.stats_at = 0
        self.route_agent = route_agent
        self.route_writer = route_writer
        self.offsets = json.loads(self.state.read_text()) if self.state.exists() else {}
        if self.database.exists():
            with self.graph_lock:
                rows = direct_query(self.database, "MATCH (e:CaptureEvent) RETURN e.payload AS payload ORDER BY e.timestamp DESC, e.id DESC LIMIT $input.limit", {"limit": window})
            self.events.extend(json.loads(row["payload"]) for row in reversed(rows))

    def persist(self, event):
        required = ("id", "kind", "timestamp", "connection", "server", "host", "username")
        if not all(isinstance(event.get(k), str) for k in required):
            raise ValueError("Invalid capture event")
        params = {k: event.get(k) for k in (
            *required, "agent", "project", "call_id", "tool", "cypher", "query_sha256", "redacted", "database")}
        params.update(host_id=hashlib.sha256(event["host"].encode()).hexdigest(),
                      tool_id=hashlib.sha256(json.dumps([event["server"], event.get("tool")]).encode()).hexdigest(),
                      parameters=json.dumps(event.get("parameters")), payload=json.dumps(event))
        with self.graph_lock:
            direct_query(self.database, EVENT, params, read_only=False)
        with self.lock:
            if not any(e["id"] == event["id"] for e in self.events):
                self.events.append(event)
                self.revision += 1
                self.commits.append({'id': event['id'], 'connection': event['connection'],
                    'call_id': event.get('call_id'), 'kind': event['kind'],
                    'at': datetime.now(timezone.utc).isoformat()})

    def poll(self):
        errors = []
        # A read-only spool mount need not exist until the first tap starts.
        for path in sorted(self.spool.glob("*.jsonl")):
            try:
                offset = self.offsets.get(path.name, 0)
                if path.stat().st_size < offset:
                    raise ValueError("Spool was truncated; restore the append-only file")
                with path.open("rb") as source:
                    source.seek(offset)
                    # Bound each pass so one busy server cannot starve the others.
                    for _ in range(100):
                        line = source.readline()
                        if not line or not line.endswith(b"\n"):
                            break
                        event = json.loads(line)
                        if self.route_agent:
                            route_file = self.spool/(str(event.get('connection'))+'.route.json')
                            if not route_file.is_file():
                                break
                            route = resolved_route(json.loads(route_file.read_text()))
                            if route.get('agent') != self.route_agent or route.get('writer', 'native') != self.route_writer:
                                break
                            if self.route_writer == 'native' and route.get('database_path') and route['database_path'] != str(self.database):
                                break
                            event = {**event, 'agent': self.route_agent, 'database': self.database.name,
                                     'client_info': route.get('client_info'), 'attribution': route.get('identity_source')}
                        self.persist(event)
                        next_offsets = {**self.offsets, path.name: source.tell()}
                        temporary = self.state.with_suffix(".tmp")
                        with temporary.open("w") as checkpoint:
                            json.dump(next_offsets, checkpoint)
                            checkpoint.flush()
                            os.fsync(checkpoint.fileno())
                        os.replace(temporary, self.state)
                        self.offsets = next_offsets
            except Exception as exc:
                errors.append({"file": path.name, "error": type(exc).__name__,
                               "offset": self.offsets.get(path.name, 0)})
        with self.lock:
            self.errors = errors

    def snapshot(self):
        with self.lock:
            return {"revision": self.revision, "events": list(self.events),
                    "errors": list(self.errors), "window": self.events.maxlen}

    def statistics(self):
        """Durable totals, independent of route leases and the retained event window."""
        with self.stats_lock:
            revision=self.revision
            if self.stats_cache is None or self.stats_revision!=revision or time.monotonic()-self.stats_at>=5:
                with self.graph_lock:
                    groups=direct_query(self.database,'''MATCH(c:CaptureConnection)-[:HAS_EVENT]->(e:CaptureEvent {kind:'request'})
                        OPTIONAL MATCH(e)-[:SUBMITTED_QUERY]->(q:ContextQuery)
                        RETURN c.agent AS agent,c.project AS project,
                          count(DISTINCT e) AS requests,count(DISTINCT q) AS cyphers''',{})
                self.stats_cache={'groups':groups}
                self.stats_revision=revision
                self.stats_at=time.monotonic()
            return self.stats_cache
