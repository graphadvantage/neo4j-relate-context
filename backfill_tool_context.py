"""Idempotently materialize tool/query links from already captured messages."""

import argparse
import json
from pathlib import Path

from database_lock import DatabaseLock
from memory_graph import TOOL_GRAPH, direct_query, reasoning_record, tool_records

TOOL_MESSAGES = """
MATCH (p:ContextProject)-[:HAS_SESSION]->(s:ContextSession)-[:HAS_MESSAGE]->(m:ContextMessage)
WHERE m.kind = 'tool_call' AND m.id > $input.after
RETURN p.name AS project, s.source AS source, m.id AS id, m.kind AS kind,
       m.tool AS tool, m.body AS body, m.cypher AS cypher,
       m.rationale AS rationale, m.result_summary AS result_summary, m.parameters AS parameters
ORDER BY m.id LIMIT $input.limit
"""

TURNS = """
MATCH (s:ContextSession)-[:HAS_TURN]->(t:ContextTurn)
WHERE t.id > $input.after
RETURN t.id AS id, s.username AS username
ORDER BY t.id LIMIT $input.limit
"""

BACKFILL_REASONING = """
UNWIND $input.turns AS row
MATCH (t:ContextTurn {id: row.turn_id})
MERGE (r:ContextReasoning {id: row.id})
ON CREATE SET r.username = row.username, r.identity_source = 'os',
              r.model_details_source = 'unavailable', r.usage_source = 'unavailable',
              r.timing_source = 'unavailable'
MERGE (t)-[:HAS_REASONING]->(r)
CALL (t, r) {
    MATCH (t)-[:HAS_MESSAGE]->(m:ContextMessage)
    WHERE m.kind IN ['tool_call', 'final_response']
    MERGE (r)-[:SUPPORTED_BY]->(m)
    RETURN count(*) AS evidence
}
RETURN count(*) AS turns
"""


def backfill(database, batch_size=100):
    if type(batch_size) is not int or not 1 <= batch_size <= 500:
        raise ValueError("batch_size must be between 1 and 500")
    report = {"tool_calls_processed": 0, "reasoning_turns_processed": 0}
    lock = DatabaseLock(database)
    for query, field in ((TOOL_MESSAGES, "tool_calls_processed"), (TURNS, "reasoning_turns_processed")):
        after = ""
        while True:
            with lock:
                rows = direct_query(database, query, {"after": after, "limit": batch_size})
                if not rows:
                    break
                if query == TOOL_MESSAGES:
                    records = [record for row in rows for record in tool_records(row["project"], row["source"], [row])]
                    direct_query(database, TOOL_GRAPH, {"tools": records}, read_only=False)
                else:
                    records = [{"turn_id": row["id"], "id": reasoning_record(row["id"], {})["id"],
                                "username": row["username"]} for row in rows]
                    direct_query(database, BACKFILL_REASONING, {"turns": records}, read_only=False)
                after = rows[-1]["id"]
                report[field] += len(rows)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    args = parser.parse_args()
    if not args.database.is_file() or args.database.suffix != ".rl":
        parser.error("Expected an existing .rl database")
    print(json.dumps(backfill(args.database.resolve())))
