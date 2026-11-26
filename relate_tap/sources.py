"""Server-side source registry and bounded, read-only playground queries."""
import json
import hashlib
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time

from memory_graph import RELATE


def read_statement(query):
    # Conservative lexer: quoted values/identifiers and comments cannot hide clauses.
    tokens, i = [], 0
    while i < len(query):
        if query.startswith("//", i):
            end = query.find("\n", i)
            i = len(query) if end < 0 else end + 1
        elif query.startswith("/*", i):
            end = query.find("*/", i + 2)
            if end < 0:
                raise ValueError("Unterminated comment")
            i = end + 2
        elif query[i] in "'\"`":
            quote = query[i]
            i += 1
            while i < len(query):
                if query[i] == "\\":
                    i += 2
                elif query[i] == quote:
                    i += 1
                    if i < len(query) and query[i] == quote:
                        i += 1
                    else:
                        break
                else:
                    i += 1
            else:
                raise ValueError("Unterminated quoted value")
        elif query[i] == ";":
            if query[i+1:].strip():
                raise ValueError("Run one read statement at a time")
            i += 1
        elif query[i].isalpha() or query[i] == "_":
            match = re.match(r"\w+", query[i:])
            tokens.append(match.group().upper())
            i += len(match.group())
        else:
            i += 1
    if not tokens or tokens[0] not in {"MATCH", "OPTIONAL", "RETURN", "WITH", "UNWIND", "EXPLAIN", "PROFILE"}:
        raise ValueError("The playground accepts read queries starting with MATCH, RETURN, WITH, UNWIND, EXPLAIN or PROFILE")
    if set(tokens) & {"CREATE", "MERGE", "SET", "DELETE", "DETACH", "REMOVE", "DROP", "ALTER", "RENAME", "INSERT", "LOAD", "CALL", "FOREACH", "GRANT", "DENY", "REVOKE", "USE", "START", "STOP", "TERMINATE"}:
        raise ValueError("Writes, procedures and external data loading are disabled in the playground")


def wire(value):
    from neo4j.graph import Node, Relationship, Path as GraphPath
    if isinstance(value, Node):
        return {"id": value.element_id, "elementId": value.element_id,
                "labels": list(value.labels), "properties": wire(dict(value))}
    if isinstance(value, Relationship):
        return {"id": value.element_id, "elementId": value.element_id, "type": value.type,
                "start_node": value.start_node.element_id, "end_node": value.end_node.element_id,
                "properties": wire(dict(value))}
    if isinstance(value, GraphPath):
        return {"nodes": [wire(n) for n in value.nodes], "relationships": [wire(r) for r in value.relationships]}
    if isinstance(value, dict):
        return {k: wire(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [wire(v) for v in value]
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    return str(value)


class Sources:
    def __init__(self, database, config=None, capture_lock=None, directory=None, extra_directory=None):
        self.capture_lock = capture_lock
        self.directory = Path(directory) if directory else None
        self.extra_directory = Path(extra_directory) if extra_directory else None
        self.database_locks = {}
        self.configured_ids = {"capture"}
        self.sources = {"capture": {"id": "capture", "name": "Tap capture", "kind": "relate", "path": str(database)}}
        self.drivers = {}
        if config:
            for item in json.loads(Path(config).read_text())["sources"]:
                if not re.fullmatch(r"[a-zA-Z0-9_-]+", item["id"]) or item["id"] in self.sources:
                    raise ValueError("Duplicate or invalid source id")
                if item["kind"] not in {"relate", "aura"}:
                    raise ValueError("Source kind must be relate or aura")
                self.sources[item["id"]] = item
                self.configured_ids.add(item["id"])

    def inventory(self):
        sources = {k: v for k, v in self.sources.items() if k in self.configured_ids}
        for directory in (self.directory, self.extra_directory):
            if not directory:
                continue
            for path in sorted(directory.glob('*.rl')):
                if not path.is_file() or path.is_symlink():
                    continue
                key = 'agent-' + hashlib.sha256(str(path).encode()).hexdigest()[:16]
                sources[key] = {"id": key, "name": path.name, "kind": "relate", "path": str(path)}
        return sources

    def public(self):
        return [{"id": s["id"], "name": s["name"], "kind": s["kind"], "read_only": True} for s in self.inventory().values()]

    def query(self, source_id, query, parameters):
        inventory = self.inventory()
        if source_id not in inventory:
            raise ValueError("Unknown source")
        if not isinstance(parameters, dict) or not all(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", k) for k in parameters):
            raise ValueError("Parameters must be an object with identifier keys")
        read_statement(query)
        source = inventory[source_id]
        started = time.monotonic()
        if source["kind"] == "relate":
            lock = self.capture_lock if source_id == 'capture' else self.database_locks.get(source['path'])
            if lock:
                with lock:
                    result = self.local(source, query, parameters)
            else:
                result = self.local(source, query, parameters)
        else:
            result = self.aura(source, query, parameters)
        result["elapsed_ms"] = (time.monotonic() - started) * 1000
        result["source"] = source_id
        result["truncated"] = len(result["rows"]) > 1000
        result["rows"] = result["rows"][:1000]
        result.setdefault("stats", {})
        result.setdefault("error", None)
        return result

    def local(self, source, query, parameters):
        if not Path(source["path"]).is_file():
            raise ValueError("The source .rl file does not exist yet")
        # Never pass browser-supplied file paths or URLs to --fetch-params.
        with tempfile.TemporaryDirectory() as directory:
            command = [RELATE, "query", "--database", source["path"], "--read-only", "--mode", "envelope",
                       "--timeout", "15s", "--max-rows", "1001"]
            for i, (key, value) in enumerate(parameters.items()):
                path = Path(directory) / str(i)
                path.write_text(json.dumps(value))
                command.extend(["--fetch-params", f"{key}={path}"])
            command.extend(["--", query])
            response = subprocess.run(command, capture_output=True, text=True, timeout=25)
        if response.returncode:
            # CLI errors can echo credentials or user query text; return a bounded safe error.
            raise ValueError(f"Relate query failed (exit {response.returncode}). Check syntax, source availability or writer contention.")
        result = json.loads(response.stdout)
        if result.get("error"):
            raise ValueError("Relate rejected the query")
        return result

    def aura(self, source, query, parameters):
        from neo4j import GraphDatabase, READ_ACCESS
        key = source["id"]
        if key not in self.drivers:
            self.drivers[key] = GraphDatabase.driver(os.environ[source["uri_env"]],
                auth=(os.environ[source["username_env"]], os.environ[source["password_env"]]),
                connection_timeout=10, connection_acquisition_timeout=15)
        database = os.environ.get(source.get("database_env", ""), source.get("database", "neo4j"))
        with self.drivers[key].session(database=database, default_access_mode=READ_ACCESS) as session:
            with session.begin_transaction(timeout=15) as tx:
                cursor = tx.run(query, parameters)
                columns = list(cursor.keys())
                rows = []
                for record in cursor:
                    rows.append({k: wire(record[k]) for k in columns})
                    if len(rows) >= 1001:
                        break
                # Reads only; closing rolls back and cancels any unconsumed stream.
        return {"columns": columns, "rows": rows, "stats": {}, "error": None}

    def close(self):
        for driver in self.drivers.values():
            driver.close()
