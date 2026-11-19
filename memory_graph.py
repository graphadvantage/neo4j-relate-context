"""Local graph operations and evidence-backed POLE+O capture."""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

RELATE = shutil.which("relate") or str(Path.home() / ".local/bin/relate")

SECRET_RULES = [
    (re.compile(r"sk-ant-api03-[A-Za-z0-9_\-]{20,}"), "[REDACTED_ANTHROPIC_KEY]"),
    (re.compile(r"sk-proj-[A-Za-z0-9_\-]{20,}"), "[REDACTED_OPENAI_KEY]"),
    (re.compile(r"sk-[A-Za-z0-9_\-]{20,}"), "[REDACTED_OPENAI_KEY]"),
    (re.compile(r"github_pat_[A-Za-z0-9_]{20,}"), "[REDACTED_GITHUB_TOKEN]"),
    (re.compile(r"gh[pousr]_[A-Za-z0-9_]{20,}"), "[REDACTED_GITHUB_TOKEN]"),
    (re.compile(r"xox[baprs]-[A-Za-z0-9\-]{20,}"), "[REDACTED_SLACK_TOKEN]"),
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{20,}"), "Bearer [REDACTED_TOKEN]"),
    (re.compile(r"(?i)\bBasic\s+[A-Za-z0-9+/=]{20,}"), "Basic [REDACTED_TOKEN]"),
    (
        re.compile(
            r"""(?i)(\b[A-Z0-9_-]*(?:API[_-]?KEY|CLIENT[_-]?SECRET|PASSWORD|PASSWD|TOKEN|SECRET|PRIVATE[_-]?KEY)[A-Z0-9_-]*["']?\s*[:=]\s*)(["'])(?:\\.|(?!\2)[^\\\r\n])*\2"""
        ),
        r"\1\2[REDACTED]\2",
    ),
    (
        re.compile(
            r"(?i)\b([A-Z0-9_]*(?:API[_-]?KEY|CLIENT[_-]?SECRET|PASSWORD|PASSWD|TOKEN|SECRET|PRIVATE[_-]?KEY)[A-Z0-9_]*)"
            r"(\s*[:=]\s*)([^\s`\"',}]+)"
        ),
        r"\1\2[REDACTED]",
    ),
    (
        re.compile(
            r"(?i)(mongodb(?:\+srv)?|postgres(?:ql)?|mysql|redis|neo4j|bolt|http|https)://([^:\s/@]+):([^@\s]+)@"
        ),
        r"\1://\2:[REDACTED]@",
    ),
]

LOAD = """
MATCH (:ContextProject {name: $input.project})-[:HAS_NOTE]->(n:ContextNote)
RETURN n.id AS id, n.source AS source, n.title AS title, n.body AS body,
       n.created_at AS created_at, n.git_commit AS git_commit
ORDER BY n.created_at DESC, n.id
LIMIT $input.limit
"""

SAVE = """
MERGE (p:ContextProject {name: $input.project})
MERGE (n:ContextNote {id: $input.id})
ON CREATE SET n.source = $input.source, n.title = $input.title, n.body = $input.body,
              n.created_at = $input.created_at, n.git_commit = $input.git_commit
MERGE (p)-[:HAS_NOTE]->(n)
RETURN n.id AS id
LIMIT 1
"""

VERIFY = """
MATCH (:ContextProject {name: $input.project})-[:HAS_NOTE]->(n:ContextNote {id: $input.id})
RETURN n.id AS id, n.source AS source, n.created_at AS created_at
LIMIT 1
"""

RECALL = """
MATCH (:ContextProject {name: $input.project})-[:HAS_SESSION]->(s:ContextSession)
      -[:HAS_MEMORY]->(f:ContextMemory)
OPTIONAL MATCH (f)-[:SUPPORTED_BY]->(m:ContextMessage)
WITH s, f, max(m.timestamp) AS timestamp, collect(m.position) AS evidence
RETURN s.session_id AS session, s.source AS source, f.kind AS kind,
       f.subject AS subject, f.body AS body, evidence, timestamp
ORDER BY timestamp DESC, f.id
LIMIT $input.limit
"""

SESSION = """
MATCH (:ContextProject {name: $input.project})-[:HAS_SESSION]->(s:ContextSession {session_id: $input.session})
      -[:HAS_MESSAGE]->(m:ContextMessage)
RETURN s.source AS source, m.position AS position, m.role AS role, m.body AS body,
       m.timestamp AS timestamp, m.kind AS kind, m.tool AS tool, m.cypher AS cypher
ORDER BY m.position DESC, m.id
LIMIT $input.limit
"""

ENTITIES = """
MATCH (:ContextProject {name: $input.project})-[:HAS_ENTITY]->(e:ContextEntity)
OPTIONAL MATCH (m:ContextMessage)-[:MENTIONS]->(e)
RETURN e.name AS name, e.type AS type, count(m) AS mentions
ORDER BY mentions DESC, name, type
LIMIT $input.limit
"""

LINK_MESSAGES = """
MATCH (s)-[:HAS_MESSAGE]->(m:ContextMessage)
WITH s, m ORDER BY m.position, m.id
WITH s, collect(m) AS ordered
WITH s, [i IN range(0, size(ordered) - 2) |
    {previous: ordered[i].id, following: ordered[i + 1].id}] AS pairs
CALL (s, pairs) {
    MATCH (s)-[:HAS_MESSAGE]->(a:ContextMessage)-[r:NEXT]->(b:ContextMessage)<-[:HAS_MESSAGE]-(s)
    WHERE NOT any(pair IN pairs WHERE pair.previous = a.id AND pair.following = b.id)
    DELETE r
    RETURN count(*) AS removed
}
UNWIND pairs AS pair
MATCH (a:ContextMessage {id: pair.previous}), (b:ContextMessage {id: pair.following})
MERGE (a)-[:NEXT]->(b)
RETURN count(*) AS next_links
"""

EXCHANGE = """
MERGE (p:ContextProject {name: $input.project})
MERGE (s:ContextSession {id: $input.key})
SET s.source = $input.source, s.session_id = $input.session
SET s.git_commit = coalesce($input.git_commit, s.git_commit)
MERGE (p)-[:HAS_SESSION]->(s)
MERGE (t:ContextTurn {id: $input.turn_key})
ON CREATE SET t.fingerprint = $input.fingerprint
MERGE (s)-[:HAS_TURN]->(t)
WITH p, s, t
WHERE t.fingerprint = $input.fingerprint
OPTIONAL MATCH (s)-[:HAS_MESSAGE]->(old:ContextMessage)
WITH p, s, coalesce(max(old.position), 0) AS last
UNWIND $input.messages AS row
MERGE (m:ContextMessage {id: row.id})
ON CREATE SET m.position = last + row.position, m.timestamp = $input.timestamp,
              m.role = row.role, m.body = row.body, m.kind = row.kind,
              m.tool = row.tool, m.cypher = row.cypher, m.call_id = row.call_id
MERGE (s)-[:HAS_MESSAGE]->(m)
WITH DISTINCT p, s
CALL (p, s) {
    UNWIND $input.entities AS row
    MATCH (s)-[:HAS_MESSAGE]->(m:ContextMessage {id: row.message_id})
    MERGE (e:ContextEntity {id: row.id})
    ON CREATE SET e.name = row.name, e.type = row.type
    MERGE (p)-[:HAS_ENTITY]->(e)
    MERGE (m)-[:MENTIONS]->(e)
    RETURN count(*) AS mentions
}
CALL (s) {
    UNWIND $input.memories AS row
    MATCH (s)-[:HAS_MESSAGE]->(m:ContextMessage {id: row.message_id})
    MERGE (f:ContextMemory {id: row.id})
    ON CREATE SET f.kind = row.kind, f.subject = row.subject, f.body = row.body
    MERGE (s)-[:HAS_MEMORY]->(f)
    MERGE (f)-[:SUPPORTED_BY]->(m)
    RETURN count(*) AS facts
}
WITH s
""" + LINK_MESSAGES

ENTITY_TYPES = {"Person", "Object", "Location", "Event", "Organization"}
MEMORY_KINDS = {"fact", "decision", "open_item"}


def exchange(project, source, args):
    if not isinstance(args, dict):
        raise ValueError("Expected a JSON exchange object")
    session = clean(args.get("session"), 160, "session")
    turn = clean(args.get("turn"), 160, "turn")
    key = digest([project, source, session])
    prompt = clean(args.get("prompt"), 64000, "prompt")
    response = clean(args.get("response"), 64000, "response")
    tools = args.get("tool_calls", [])
    entities = args.get("entities", [])
    memories = args.get("memories", [])
    for name, items in (("tool_calls", tools), ("entities", entities), ("memories", memories)):
        if not isinstance(items, list) or len(items) > 100 or any(not isinstance(x, dict) for x in items):
            raise ValueError(f"{name} must contain at most 100 objects")
    messages = [{"role": "user", "kind": "prompt", "body": prompt}]
    for item in tools:
        name = clean(item.get("name"), 200, "tool name")
        args_text = json.dumps(item.get("arguments"), ensure_ascii=False)
        if len(args_text) > 64000:
            raise ValueError("Tool arguments exceed 64000 characters")
        event = tool_event(0, None, name, args_text, None)
        if item.get("cypher"):
            event["cypher"] = clean(item["cypher"], 64000, "cypher")
        messages.append(event)
    messages.append({"role": "assistant", "kind": "final_response", "body": response})
    for index, item in enumerate(messages, 1):
        item.update(position=index, id=digest([key, turn, index]))
    evidence = {"prompt": messages[0]["id"], "response": messages[-1]["id"]}
    mentions = []
    for item in entities:
        name = clean(item.get("name"), 160, "entity name")
        kind = item.get("type")
        if kind not in ENTITY_TYPES:
            raise ValueError("Expected a POLE+O entity type")
        mentions.append({"id": digest([project, kind, name.casefold()]), "name": name,
                         "type": kind, "message_id": evidence[item.get("evidence", "prompt")]})
    facts = []
    for item in memories:
        kind = item.get("kind")
        if kind not in MEMORY_KINDS:
            raise ValueError("Expected a fact, decision or open_item")
        subject = clean(item.get("subject"), 160, "memory subject")
        body = clean(item.get("body"), 4000, "memory body")
        message_id = evidence[item.get("evidence", "prompt")]
        facts.append({"id": digest([key, turn, kind, subject, body]), "kind": kind,
                      "subject": subject, "body": body, "message_id": message_id})
    return {"project": project, "source": source, "session": session, "key": key,
            "turn_key": digest([key, turn]), "fingerprint": digest([messages, mentions, facts]),
            "timestamp": datetime.now(timezone.utc).isoformat(), "messages": messages,
            "entities": mentions, "memories": facts, "git_commit": None}


def direct_query(database, statement, parameters, *, read_only=True):
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8") as params:
        json.dump(parameters, params)
        params.flush()
        command = [RELATE, "query", "--database", str(database), "--mode", "envelope"]
        if read_only:
            command.append("--read-only")
        command.extend(["--fetch-params", f"input={params.name}", statement])
        result = subprocess.run(command, capture_output=True, text=True, check=True,
                                timeout=30, umask=0o077)
    return json.loads(result.stdout)["rows"]

def redact(value):
    for pattern, replacement in SECRET_RULES:
        value = pattern.sub(replacement, value)
    return value


def clean(value, limit, field):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be nonempty text")
    value = redact(value)
    if len(value.strip()) > limit:
        raise ValueError(f"{field} must be at most {limit} characters")
    return value.strip()


def save_note(run, project, source, note, git_commit=None):
    if not isinstance(note, dict):
        raise ValueError("Expected a JSON object with title and body")
    payload = {
        "project": project, "source": source,
        "title": clean(note.get("title"), 160, "title"),
        "body": clean(note.get("body"), 4000, "body"), "git_commit": git_commit,
    }
    note_id = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    payload.update(id=note_id, created_at=datetime.now(timezone.utc).isoformat())
    run(SAVE, payload, read_only=False)
    rows = run(VERIFY, {"project": project, "id": note_id})
    if not rows:
        raise ValueError("The saved note could not be verified")
    return rows

def tool_event(position, timestamp, name, arguments, call_id):
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except ValueError:
            pass
    cypher = None
    if isinstance(arguments, dict):
        cypher = next((arguments[key] for key in ("cypher", "statement", "query")
                       if isinstance(arguments.get(key), str)), None)
    return {"position": position, "timestamp": timestamp, "role": "assistant",
            "kind": "tool_call", "tool": redact(name or "unknown"),
            "call_id": call_id, "cypher": redact(cypher) if cypher else None,
            "body": redact(json.dumps(arguments, ensure_ascii=False))}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()

