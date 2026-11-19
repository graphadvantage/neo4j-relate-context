#!/usr/bin/env python3
"""Expose Claude Desktop's Relate database over MCP stdio."""

import argparse
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import threading

from aura_sync import AuraSync, DEFAULT_CREDENTIALS
from memory_graph import ENTITIES, EXCHANGE, LOAD, RECALL, SESSION, clean, direct_query, exchange, save_note

DEFAULT_DATABASE = Path(__file__).resolve().parent / "relate-dbs" / "claude-desktop.rl"


def tool(name, description, required, optional=None):
    properties = {key: {"type": "string"} for key in required}
    properties.update(optional or {})
    return {"name": name, "description": description, "inputSchema": {
        "type": "object", "properties": properties, "required": required,
        "additionalProperties": False,
    }}


LIMIT = {"limit": {"type": "integer", "minimum": 1, "maximum": 50}}
TOOLS = [
    tool("save_context", "Save an explicitly requested note in the named collection.", ["project", "title", "body"]),
    tool("recall_context", "Read recent notes and evidence-backed memories for a collection.", ["project"], LIMIT),
    tool("get_session", "Read recent captured messages for a session.", ["project", "session"], LIMIT),
    tool("record_turn", "Save a completed exchange in order, including observed tool calls and POLE+O entities.",
         ["project", "session", "turn", "prompt", "response"], {
             "tool_calls": {"type": "array", "items": {"type": "object", "required": ["name"], "properties": {
                 "name": {"type": "string"}, "arguments": {}, "cypher": {"type": "string"}}}},
             "entities": {"type": "array", "items": {"type": "object", "required": ["name", "type"], "properties": {
                 "name": {"type": "string"}, "type": {"enum": ["Person", "Object", "Location", "Event", "Organization"]},
                 "evidence": {"enum": ["prompt", "response"]}}}},
             "memories": {"type": "array", "items": {"type": "object", "required": ["kind", "subject", "body"],
                 "properties": {"kind": {"enum": ["fact", "decision", "open_item"]}, "subject": {"type": "string"},
                                "body": {"type": "string"}, "evidence": {"enum": ["prompt", "response"]}}}},
         }),
    tool("flush_memory", "Request a background Aura replication attempt.", []),
]

INSTRUCTIONS = """When the user enables session capture, use record_turn for each completed exchange.
Use the exact shared project key from Project instructions. Generate a UUID for the session and
keep it for the conversation. Generate a new turn ID per exchange; reuse the ID and payload on retry. Include the user prompt,
observed domain tool names and arguments in execution order, Cypher when visible, and the final
response you intend to send. Omit secrets and tool arguments you cannot verify. Extract grounded
POLE+O entities (Person, Object, Location, Event, Organization) and concise facts or decisions,
with evidence pointing to the prompt or response. Use recall_context for earlier memory.
On an explicit wrap-up, call flush_memory after recording the final turn."""


def call(run, sync, name, args):
    if name == "flush_memory":
        return sync.flush() if sync else {"queued": False, "state": "disabled"}
    project = clean(args.get("project"), 160, "project")
    if name == "record_turn":
        payload = exchange(project, "claude-desktop", args)
        rows = run(EXCHANGE, payload, read_only=False)
        if not rows or rows[0]["next_links"] == 0:
            raise ValueError("Turn was not saved; check whether the turn ID was reused with different content")
        return {"session": payload["session"], "turn": args["turn"], "events": len(payload["messages"])}
    if name == "save_context":
        return {"notes": save_note(run, project, "claude-desktop", args)}
    limit = args.get("limit", 10)
    if type(limit) is not int or not 1 <= limit <= 50:
        raise ValueError("limit must be an integer between 1 and 50")
    params = {"project": project, "limit": limit}
    if name == "recall_context":
        return {"notes": run(LOAD, params), "memories": run(RECALL, params),
                "entities": run(ENTITIES, params)}
    if name == "get_session":
        params["session"] = clean(args.get("session"), 160, "session")
        return {"messages": run(SESSION, params)}
    raise ValueError("Unknown memory tool")


def respond(run, sync, message):
    method = message.get("method")
    if method == "initialize":
        version = message.get("params", {}).get("protocolVersion")
        return {"protocolVersion": version if version in {"2024-11-05", "2025-03-26", "2025-06-18"}
                else "2025-06-18", "capabilities": {"tools": {}},
                "serverInfo": {"name": "relate-memory", "version": "1.1.0"},
                "instructions": INSTRUCTIONS}
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": TOOLS}
    if method == "tools/call":
        try:
            params = message["params"]
            result = call(run, sync, params["name"], params.get("arguments", {}))
            return {"content": [{"type": "text", "text": json.dumps(result)}]}
        except subprocess.CalledProcessError as exc:
            return {"content": [{"type": "text", "text": f"Relate query failed (exit {exc.returncode})"}],
                    "isError": True}
        except subprocess.TimeoutExpired:
            return {"content": [{"type": "text", "text": "Relate query timed out"}], "isError": True}
        except (OSError, ValueError, KeyError, TypeError) as exc:
            return {"content": [{"type": "text", "text": str(exc)}], "isError": True}
    return None


def serve(run, sync):
    for line in sys.stdin:
        try:
            message = json.loads(line)
        except ValueError:
            print(json.dumps({"jsonrpc": "2.0", "id": None,
                              "error": {"code": -32700, "message": "Parse error"}}), flush=True)
            continue
        if not isinstance(message, dict) or "id" not in message:
            continue
        result = respond(run, sync, message)
        reply = {"jsonrpc": "2.0", "id": message["id"]}
        if result is None:
            reply["error"] = {"code": -32601, "message": "Method not found"}
        else:
            reply["result"] = result
        print(json.dumps(reply), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    aura = parser.add_mutually_exclusive_group()
    aura.add_argument("--aura", type=Path, default=DEFAULT_CREDENTIALS)
    aura.add_argument("--no-aura", action="store_true")
    args = parser.parse_args()
    database = args.database.resolve()
    if database.suffix != ".rl" or not database.is_file():
        parser.error("Expected an existing Relate .rl file")

    os.umask(0o077)
    graph_lock = threading.Lock()

    def run(statement, parameters, *, read_only=True):
        with graph_lock:
            return direct_query(database, statement, parameters, read_only=read_only)

    with database.with_suffix(".rl.lock").open("a") as owner:
        try:
            fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            parser.error("Another memory MCP process already owns this database")
        sync = None if args.no_aura else AuraSync(database, args.aura.resolve(), graph_lock)
        serve(run, sync)


if __name__ == "__main__":
    main()
