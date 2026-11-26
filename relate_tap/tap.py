"""Usage: relate-tap --name neo4j-fraud -- node server.js"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import socket
import subprocess
import sys
import threading
import time
import uuid

from .privacy import sanitize
from .bootstrap import (atomic_json, canonical_agent, client_identity, database_for,
                        discover_agent, discover_project, install_skill, start_worker)


def now():
    return datetime.now(timezone.utc).isoformat()


def os_username():
    # USER/LOGNAME can be inherited from a launcher and differ from the OS account.
    try:
        import pwd
        return pwd.getpwuid(os.getuid()).pw_name
    except ImportError:
        import getpass
        return getpass.getuser()


def query_arguments(arguments):
    """Keep exact submitted text, never reconstruct Cypher from a response."""
    if not isinstance(arguments, dict):
        return None, None
    for field in ("cypher", "statement", "query"):
        value = arguments.get(field)
        if isinstance(value, str):
            # A field called query can contain ordinary search text or SQL.
            stripped = re.sub(r"(?s)/\*.*?\*/|(?m://[^\n]*)", "", value).lstrip()
            if re.match(r"(?i)^(CYPHER|MATCH|OPTIONAL\s+MATCH|RETURN|WITH|UNWIND|CALL|CREATE|MERGE|SHOW|EXPLAIN|PROFILE|USE|DELETE|SET|REMOVE|FOREACH)\b", stripped):
                params = arguments.get("parameters", arguments.get("params"))
                return value, params if isinstance(params, dict) else None
    return None, None


def response_details(message):
    if "error" in message:
        return "mcp_error", None
    result = message.get("result", {})
    if not isinstance(result, dict):
        return "response_received", None
    status = "tool_error" if result.get("isError") else "response_received"
    values = [result.get("structuredContent")]
    for item in result.get("content", []):
        if isinstance(item, dict) and item.get("type") == "text":
            try:
                values.append(json.loads(item.get("text", "")))
            except (ValueError, TypeError):
                pass
    for value in values:
        if isinstance(value, dict):
            if type(value.get("row_count")) is int and value["row_count"] >= 0:
                return status, value["row_count"]
            if isinstance(value.get("rows"), list):
                return status, len(value["rows"])
    return status, None


class Recorder:
    def __init__(self, directory, name, agent=None, project=None):
        directory.mkdir(parents=True, exist_ok=True)
        self.connection = str(uuid.uuid4())
        self.path = directory / f"{self.connection}.jsonl"
        self.fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        self.lock = threading.Lock()
        self.pending = {}
        self.name, self.agent, self.project = name, agent, project
        self.agent = canonical_agent(agent)
        self.override = self.agent
        self.client_info = None
        self.route = None
        self.route_path = directory/(self.connection+'.route.json')
        self.route_lock = threading.Lock()
        self.stopped = threading.Event()
        self.worker_agent = None
        self.keepalive_started = False
        self.last_worker_start = 0
        if self.agent:
            self.register()

    def register(self):
        if self.route and self.route.get('agent') == self.agent:
            self.route['client_info'] = self.client_info
            self.heartbeat()
            return
        try:
            skill = install_skill(self.agent) if self.agent else {'state': 'pending'}
        except OSError as exc:
            skill = {'state': 'error', 'error': type(exc).__name__}
        self.route = {'connection': self.connection, 'server': self.name, 'agent': self.agent,
                      'host': socket.gethostname(), 'username': os_username(), 'project': self.project,
                      'client_info': self.client_info, 'identity_source': 'configured' if self.override else 'mcp_client',
                      'database': database_for(self.agent).name if self.agent else None,
                      'database_path': str(database_for(self.agent)) if self.agent else None,
                      'writer': os.environ.get('RELATE_TAP_WRITER', 'native'),
                      'skill': skill, 'connected': True}
        self.heartbeat()
        if self.agent and self.worker_agent != self.agent and self.route['writer'] == 'native':
            start_worker(self.agent, self.path.parent)
            self.worker_agent = self.agent
            self.last_worker_start = time.monotonic()
        if not self.keepalive_started:
            threading.Thread(target=self.keepalive, daemon=True).start()
            self.keepalive_started = True

    def heartbeat(self, connected=True):
        if self.route:
            with self.route_lock:
                self.route.update(last_seen=now(), connected=connected)
                atomic_json(self.route_path, self.route)

    def keepalive(self):
        while not self.stopped.wait(5):
            try:
                self.heartbeat()
                if self.worker_agent and time.monotonic()-self.last_worker_start > 30:
                    status_path = self.path.parent/(self.worker_agent+'.status.json')
                    stale = not status_path.exists() or time.time()-status_path.stat().st_mtime > 20
                    if stale:
                        start_worker(self.worker_agent, self.path.parent)
                        self.last_worker_start = time.monotonic()
            except OSError:
                print('relate-tap: route heartbeat unavailable', file=sys.stderr, flush=True)

    def emit(self, kind, **fields):
        event = {"id": str(uuid.uuid4()), "kind": kind, "timestamp": now(),
                 "connection": self.connection, "server": self.name,
                 "host": socket.gethostname(), "username": os_username(),
                 "agent": self.agent, "project": self.project,
                 "attribution": ('configured' if self.override else 'mcp_client') if self.agent else "unknown",
                 "client_info": self.client_info,
                 "database": self.route['database'] if self.route else None, **fields}
        safe = sanitize(event)
        safe["redacted"] = event != safe
        event = safe
        data = (json.dumps(event, ensure_ascii=False) + "\n").encode()
        with self.lock:
            view = memoryview(data)
            while view:
                view = view[os.write(self.fd, view):]
            os.fsync(self.fd)

    def observe(self, line, direction):
        try:
            msg = json.loads(line)
            if not isinstance(msg, dict):
                return
            info = client_identity(msg) if direction == 'request' else None
            if info:
                agent = self.override or discover_agent(info)
                if self.agent and agent != self.agent:
                    raise ValueError('MCP client identity changed within one connection')
                self.client_info = info
                self.agent = agent
                self.register()
                if not self.agent:
                    print('relate-tap: unrecognized MCP client; set --agent to enable database routing', file=sys.stderr, flush=True)
            if direction == "request" and msg.get("method") == "tools/call" and "id" in msg:
                params = msg.get("params", {})
                arguments = params.get("arguments", {})
                query, bindings = query_arguments(arguments)
                call = str(uuid.uuid4())
                with self.lock:
                    self.pending[json.dumps(msg["id"])] = (call, time.monotonic())
                self.emit("request", call_id=call, request_id=msg["id"], tool=params.get("name"),
                          arguments=arguments, cypher=query, parameters=bindings,
                          query_sha256=hashlib.sha256(query.encode()).hexdigest() if query else None)
            elif direction == "response" and "id" in msg and ("result" in msg or "error" in msg):
                with self.lock:
                    pending = self.pending.pop(json.dumps(msg["id"]), None)
                if pending:
                    status, rows = response_details(msg)
                    self.emit("response", call_id=pending[0], request_id=msg["id"],
                              status=status, row_count=rows, duration_ms=(time.monotonic()-pending[1])*1000,
                              response=msg)
        except Exception as exc:
            # Observation must never corrupt MCP stdout or prevent forwarding.
            print(f"relate-tap: capture failed ({type(exc).__name__})", file=sys.stderr, flush=True)

    def close(self, returncode):
        self.stopped.set()
        if self.route:
            self.heartbeat(False)
        with self.lock:
            pending = list(self.pending.values())
            self.pending.clear()
        for call, started in pending:
            self.emit("response", call_id=call, status="connection_closed", row_count=None,
                      duration_ms=(time.monotonic()-started)*1000)
        self.emit("disconnected", returncode=returncode)
        os.close(self.fd)


def main():
    if sys.argv[1:2] == ['memory']:
        from .memory import main as memory_main
        memory_main(sys.argv[2:])
        return
    if sys.argv[1:2] == ['memory-server']:
        from memory_mcp import main as server_main
        server_main(sys.argv[2:])
        return
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", required=True)
    parser.add_argument("--spool", type=Path, default=Path(os.environ.get("RELATE_TAP_SPOOL", str(Path.home()/'.relate-tap/spool'))))
    parser.add_argument("--agent", default=os.environ.get("RELATE_TAP_AGENT"))
    parser.add_argument("--project", default=os.environ.get("RELATE_TAP_PROJECT"))
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("Expected -- followed by the MCP server command")
    recorder = None
    try:
        recorder = Recorder(args.spool, args.name, args.agent, args.project or discover_project())
        recorder.emit("connected")
    except OSError as exc:
        print(f"relate-tap: capture unavailable ({type(exc).__name__}); forwarding continues", file=sys.stderr)
    try:
        child = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=None)
    except OSError as exc:
        print(f"relate-tap: cannot launch server ({type(exc).__name__})", file=sys.stderr)
        if recorder:
            recorder.close(127)
        raise SystemExit(127)

    def stop(signum, _frame):
        if child.poll() is None:
            child.send_signal(signum)
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    def inbound():
        try:
            for line in sys.stdin.buffer:
                if recorder:
                    recorder.observe(line, "request")
                child.stdin.write(line)
                child.stdin.flush()
        except (BrokenPipeError, OSError, ValueError):
            pass
        finally:
            try:
                child.stdin.close()
            except OSError:
                pass
    threading.Thread(target=inbound, daemon=True).start()
    try:
        for line in child.stdout:
            if recorder:
                recorder.observe(line, "response")
            sys.stdout.buffer.write(line)
            sys.stdout.buffer.flush()
    except BrokenPipeError:
        child.terminate()
    code = child.wait()
    if recorder:
        try:
            recorder.close(code)
        except OSError:
            print("relate-tap: final capture could not be persisted", file=sys.stderr)
    raise SystemExit(code if code >= 0 else 128-code)


if __name__ == "__main__":
    main()
