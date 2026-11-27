"""Local graph operations and evidence-backed POLE+O capture."""

from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
try:
    import pwd
except ImportError:
    pwd = None
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
       n.created_at AS created_at, n.git_commit AS git_commit, n.username AS username
ORDER BY n.created_at DESC, n.id
LIMIT $input.limit
"""

SAVE = """
MERGE (p:ContextProject {name: $input.project})
MERGE (n:ContextNote {id: $input.id})
ON CREATE SET n.source = $input.source, n.title = $input.title, n.body = $input.body,
              n.created_at = $input.created_at, n.git_commit = $input.git_commit,
              n.username = $input.username, n.identity_source = 'os'
MERGE (p)-[:HAS_NOTE]->(n)
RETURN n.id AS id
LIMIT 1
"""

VERIFY = """
MATCH (:ContextProject {name: $input.project})-[:HAS_NOTE]->(n:ContextNote {id: $input.id})
RETURN n.id AS id, n.source AS source, n.created_at AS created_at, n.username AS username
LIMIT 1
"""

RECALL = """
MATCH (:ContextProject {name: $input.project})-[:HAS_SESSION]->(s:ContextSession)
      -[:HAS_MEMORY]->(f:ContextMemory)
OPTIONAL MATCH (f)-[:SUPPORTED_BY]->(m:ContextMessage)
WITH s, f, max(m.timestamp) AS timestamp, collect(m.position) AS evidence
RETURN s.session_id AS session, s.source AS source, f.kind AS kind,
       f.subject AS subject, f.body AS body, evidence, timestamp, s.username AS username
ORDER BY timestamp DESC, f.id
LIMIT $input.limit
"""

SESSION = """
MATCH (:ContextProject {name: $input.project})-[:HAS_SESSION]->(s:ContextSession {session_id: $input.session})
      -[:HAS_MESSAGE]->(m:ContextMessage)
OPTIONAL MATCH (m)-[:USES_TOOL]->(tool:ContextTool)
OPTIONAL MATCH (m)-[:EXECUTES_QUERY]->(q:ContextQuery)
OPTIONAL MATCH (s)-[:HAS_TURN]->(t:ContextTurn)-[:HAS_MESSAGE]->(m)
OPTIONAL MATCH (t)-[:HAS_REASONING]->(reasoning:ContextReasoning)
RETURN s.source AS source, m.position AS position, m.role AS role, m.body AS body,
       m.timestamp AS timestamp, m.kind AS kind, m.tool AS tool, m.cypher AS cypher,
       m.username AS username, tool.id AS tool_id, q.id AS query_id,
       q.parameters AS parameters, m.rationale AS rationale, m.result_summary AS result_summary,
       reasoning.id AS reasoning_id, reasoning.summary AS reasoning_summary,
       reasoning.model AS model, reasoning.model_version AS model_version, reasoning.model_effort AS model_effort,
       reasoning.input_tokens AS input_tokens, reasoning.output_tokens AS output_tokens,
       reasoning.total_tokens AS total_tokens, reasoning.elapsed_ms AS elapsed_ms
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

# A tool is a project-scoped catalog entry; a query is an individual execution.
# The message remains the ordered evidence for both, including the full arguments.
TOOL_GRAPH = """
UNWIND $input.tools AS row
MATCH (p:ContextProject {name: row.project})-[:HAS_SESSION]->(:ContextSession)
      -[:HAS_MESSAGE]->(m:ContextMessage {id: row.message_id})
MERGE (tool:ContextTool {id: row.tool_id})
ON CREATE SET tool.name = row.name, tool.source = row.source, tool.project = row.project
MERGE (p)-[:HAS_TOOL]->(tool)
MERGE (m)-[:USES_TOOL]->(tool)
CALL (row, m, tool) {
    WITH row, m, tool WHERE row.cypher IS NOT NULL
    MERGE (q:ContextQuery {id: row.query_id})
    ON CREATE SET q.cypher = row.cypher, q.parameters = row.parameters,
                  q.rationale = row.rationale, q.result_summary = row.result_summary,
                  q.timestamp = m.timestamp, q.username = m.username, q.identity_source = m.identity_source
    MERGE (m)-[:EXECUTES_QUERY]->(q)
    MERGE (q)-[:USES_TOOL]->(tool)
    RETURN count(*) AS queries
}
RETURN count(*) AS tools
"""

EXCHANGE = """
MERGE (p:ContextProject {name: $input.project})
MERGE (s:ContextSession {id: $input.key})
SET s.source = $input.source, s.session_id = $input.session,
    s.username = $input.username, s.identity_source = 'os'
SET s.git_commit = coalesce($input.git_commit, s.git_commit)
MERGE (p)-[:HAS_SESSION]->(s)
MERGE (t:ContextTurn {id: $input.turn_key})
ON CREATE SET t.fingerprint = $input.fingerprint
MERGE (s)-[:HAS_TURN]->(t)
WITH p, s, t
WHERE t.fingerprint = $input.fingerprint
OPTIONAL MATCH (s)-[:HAS_MESSAGE]->(old:ContextMessage)
WITH p, s, t, coalesce(max(old.position), 0) AS last
UNWIND $input.messages AS row
MERGE (m:ContextMessage {id: row.id})
ON CREATE SET m.position = last + row.position, m.timestamp = $input.timestamp,
              m.role = row.role, m.body = row.body, m.kind = row.kind,
              m.tool = row.tool, m.cypher = row.cypher, m.call_id = row.call_id,
              m.rationale = row.rationale, m.result_summary = row.result_summary,
              m.parameters = row.parameters, m.mcp_source_id = row.mcp_source_id,
              m.username = $input.username, m.identity_source = 'os'
MERGE (s)-[:HAS_MESSAGE]->(m)
MERGE (t)-[:HAS_MESSAGE]->(m)
WITH DISTINCT p, s, t
MERGE (reasoning:ContextReasoning {id: $input.reasoning.id})
ON CREATE SET reasoning.summary = $input.reasoning.summary,
              reasoning.model = $input.reasoning.model, reasoning.model_version = $input.reasoning.model_version,
              reasoning.model_effort = $input.reasoning.model_effort,
              reasoning.input_tokens = $input.reasoning.input_tokens, reasoning.output_tokens = $input.reasoning.output_tokens,
              reasoning.total_tokens = $input.reasoning.total_tokens, reasoning.elapsed_ms = $input.reasoning.elapsed_ms,
              reasoning.model_details_source = $input.reasoning.model_details_source,
              reasoning.usage_source = $input.reasoning.usage_source, reasoning.timing_source = $input.reasoning.timing_source,
              reasoning.timestamp = $input.timestamp, reasoning.username = $input.username, reasoning.identity_source = 'os'
MERGE (t)-[:HAS_REASONING]->(reasoning)
CALL (t, reasoning) {
    MATCH (t)-[:HAS_MESSAGE]->(evidence:ContextMessage)
    WHERE evidence.kind IN ['tool_call', 'final_response']
    MERGE (reasoning)-[:SUPPORTED_BY]->(evidence)
    RETURN count(*) AS reasoning_evidence
}
WITH p, s
CALL () {
""" + TOOL_GRAPH + """
}
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
            reported = clean(item["cypher"], 64000, "cypher")
            if event.get('cypher') and event['cypher'].strip() != reported:
                raise ValueError('Reported Cypher must match the complete query in tool arguments; do not summarize or abbreviate it')
            event["cypher"] = reported
        if event.get('cypher'):
            from relate_tap.intent_model import query_integrity
            if query_integrity(event['cypher']) == 'incomplete':
                raise ValueError('Incomplete Cypher: copy the full submitted statement, including every RETURN column; never use ellipsis placeholders')
            if query_integrity(event['cypher']) == 'not_cypher':
                raise ValueError('Cypher must be the exact submitted statement, not a description; put descriptions in rationale instead')
        for field in ("rationale", "result_summary"):
            if field in item:
                event[field] = clean(item[field], 4000, field)
        if "parameters" in item:
            if not isinstance(item["parameters"], dict):
                raise ValueError("parameters must be a JSON object")
            event["parameters"] = clean(json.dumps(item["parameters"], ensure_ascii=False), 64000, "parameters")
        if item.get('mcp_source_id'):
            from relate_tap.intent_model import scope
            event['mcp_source_id'] = scope(project,item['mcp_source_id'])['mcp_source_id']
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
    turn_key = digest([key, turn])
    reasoning = reasoning_record(turn_key, args)
    fingerprint_parts = [messages, mentions, facts]
    if any(field in args for field in REASONING_FIELDS):
        fingerprint_parts.append(reasoning)
    return {"project": project, "source": source, "session": session, "key": key,
            "username": pwd.getpwuid(os.getuid()).pw_name if pwd else __import__('getpass').getuser(),
            "turn_key": turn_key, "fingerprint": digest(fingerprint_parts), "reasoning": reasoning,
            "timestamp": datetime.now(timezone.utc).isoformat(), "messages": messages,
            "tools": tool_records(project, source, messages),
            "entities": mentions, "memories": facts, "git_commit": None}


REASONING_FIELDS = ("reasoning_summary", "model", "model_version", "model_effort", "input_tokens", "output_tokens", "total_tokens", "elapsed_ms")


def reasoning_record(turn_key, args):
    record = {"id": digest([turn_key, "reasoning"])}
    for field, limit in (("reasoning_summary", 4000), ("model", 200), ("model_version", 200), ("model_effort", 80)):
        record["summary" if field == "reasoning_summary" else field] = clean(args[field], limit, field) if field in args else None
    for field in ("input_tokens", "output_tokens", "total_tokens"):
        value = args.get(field)
        if field in args and (type(value) is not int or not 0 <= value <= 2**63 - 1):
            raise ValueError(f"{field} must be a nonnegative 64-bit integer")
        record[field] = value
    if record["input_tokens"] is not None and record["output_tokens"] is not None:
        total = record["input_tokens"] + record["output_tokens"]
        if total > 2**63 - 1:
            raise ValueError("total_tokens exceeds a 64-bit integer")
        if record["total_tokens"] is not None and record["total_tokens"] != total:
            raise ValueError("total_tokens must equal input_tokens + output_tokens when both are supplied")
        record["total_tokens"] = total
    elapsed = args.get("elapsed_ms")
    if "elapsed_ms" in args and (type(elapsed) not in (int, float) or not 0 <= elapsed <= 2**63 - 1 or not math.isfinite(elapsed)):
        raise ValueError("elapsed_ms must be finite and nonnegative")
    record["elapsed_ms"] = elapsed
    record["model_details_source"] = "client_reported" if any(record[f] for f in ("model", "model_version", "model_effort")) else "unavailable"
    record["usage_source"] = "client_reported" if any(record[f] is not None for f in ("input_tokens", "output_tokens", "total_tokens")) else "unavailable"
    record["timing_source"] = "client_reported" if elapsed is not None else "unavailable"
    return record


def tool_records(project, source, messages):
    records = []
    for message in messages:
        if message.get("kind") != "tool_call":
            continue
        arguments = json.loads(message["body"])
        parameters = message.get("parameters")
        if parameters is None and isinstance(arguments, dict):
            value = arguments.get("parameters", arguments.get("params"))
            if isinstance(value, dict):
                parameters = clean(json.dumps(value, ensure_ascii=False), 64000, "parameters")
        records.append({
            "project": project, "source": source, "message_id": message["id"],
            "tool_id": digest([project, source, message["tool"]]), "name": message["tool"],
            "query_id": digest([message["id"], "query"]), "cypher": message.get("cypher"),
            "parameters": parameters, "mcp_source_id": message.get("mcp_source_id"), "rationale": message.get("rationale"),
            "result_summary": message.get("result_summary"),
        })
    return records


def direct_query(database, statement, parameters, *, read_only=True):
    with tempfile.TemporaryDirectory(prefix='relate-params-') as directory:
        params = Path(directory)/'input.json'
        params.write_text(json.dumps(parameters), encoding='utf-8')
        command = [RELATE, "query", "--database", str(database), "--mode", "envelope"]
        if read_only:
            command.append("--read-only")
        command.extend(["--fetch-params", f"input={params}", statement])
        result = subprocess.run(command, capture_output=True, text=True, check=True,
                                timeout=30, **({'umask': 0o077} if os.name == 'posix' else {}))
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
        "username": pwd.getpwuid(os.getuid()).pw_name if pwd else __import__('getpass').getuser(),
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
        from relate_tap.intent_model import query_integrity
        if query_integrity(cypher) in ('missing','not_cypher'):
            cypher=None
    return {"position": position, "timestamp": timestamp, "role": "assistant",
            "kind": "tool_call", "tool": redact(name or "unknown"),
            "call_id": call_id, "cypher": redact(cypher) if cypher else None,
            "body": redact(json.dumps(arguments, ensure_ascii=False))}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()

