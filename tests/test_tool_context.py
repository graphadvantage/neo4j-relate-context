import copy
from pathlib import Path
import tempfile
import unittest

from backfill_tool_context import backfill
from memory_graph import EXCHANGE, SESSION, direct_query, exchange
from memory_mcp import TOOLS, respond


class ToolContextTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / "context.rl"
        self.args = {"session": "session", "turn": "turn", "prompt": "Count records",
                     "response": "There are two records.", "tool_calls": [
                         {"name": "read_cypher", "arguments": {"query": "RETURN $n", "parameters": {"n": 2}},
                          "rationale": "Read the count needed to answer.", "result_summary": "Returned 2."}],
                     "reasoning_summary": "The reported count comes from the query result.",
                     "model": "Opus 5.5", "model_effort": "Medium", "model_version": "test-reported-version",
                     "input_tokens": 100, "output_tokens": 20, "elapsed_ms": 1250.5}

    def write(self, args):
        return direct_query(self.db, EXCHANGE, exchange("project", "claude-desktop", args), read_only=False)

    def count(self, label):
        # Labels here are test-owned constants, never user input.
        return direct_query(self.db, f"MATCH (n:{label}) RETURN count(n) AS n", {})[0]["n"]

    def test_execution_graph_telemetry_and_retries(self):
        self.write(self.args)
        self.write(self.args)
        for label in ("ContextTool", "ContextQuery", "ContextReasoning"):
            self.assertEqual(self.count(label), 1)
        messages = direct_query(self.db, SESSION, {"project": "project", "session": "session", "limit": 10})
        self.assertEqual(len(messages), 3)
        call = next(m for m in messages if m["kind"] == "tool_call")
        self.assertEqual(call["parameters"], '{"n": 2}')
        self.assertEqual(call["total_tokens"], 120)
        self.assertEqual(call["model_effort"], "Medium")
        self.assertEqual(call["elapsed_ms"], 1250.5)
        evidence = direct_query(self.db, 'MATCH (:ContextReasoning)-[:SUPPORTED_BY]->(m) RETURN count(m) AS n', {})
        self.assertEqual(evidence[0]["n"], 2)
        changed = copy.deepcopy(self.args)
        changed["output_tokens"] = 21
        self.assertEqual(self.write(changed), [{"next_links": 0}])
        self.assertEqual(self.count("ContextMessage"), 3)

    def test_reused_tool_distinct_query_executions_and_no_query_tool(self):
        args = copy.deepcopy(self.args)
        args["tool_calls"] *= 2
        args["tool_calls"].append({"name": "get_schema", "arguments": {}})
        self.write(args)
        self.assertEqual(self.count("ContextTool"), 2)
        self.assertEqual(self.count("ContextQuery"), 2)

    def test_missing_metadata_and_empty_tools(self):
        args = {k: self.args[k] for k in ("session", "turn", "prompt", "response")}
        self.write(args)
        self.assertEqual(self.count("ContextTool"), 0)
        self.assertEqual(self.count("ContextQuery"), 0)
        rows = direct_query(self.db, 'MATCH (r:ContextReasoning) RETURN r.total_tokens AS tokens, r.elapsed_ms AS elapsed, r.usage_source AS source', {})
        self.assertEqual(rows, [{"tokens": None, "elapsed": None, "source": "unavailable"}])

    def test_validation_and_redaction(self):
        for field, value in (("input_tokens", True), ("output_tokens", -1), ("elapsed_ms", float('nan')),
                             ("elapsed_ms", float('inf')), ("elapsed_ms", 10**400), ("total_tokens", 999)):
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                exchange("project", "claude-desktop", {**self.args, field: value})
        args = copy.deepcopy(self.args)
        args["tool_calls"][0]["parameters"] = {"password": "test-secret"}
        args["reasoning_summary"] = 'password="test-secret"'
        payload = exchange("project", "claude-desktop", args)
        self.assertNotIn("test-secret", str(payload))

    def test_idempotent_backfill_preserves_existing_telemetry(self):
        self.write(self.args)
        # Simulate an older store whose tool/query nodes were never materialized.
        direct_query(self.db, 'MATCH (n:ContextTool|ContextQuery) DETACH DELETE n', {}, read_only=False)
        self.assertEqual(backfill(self.db, 1)["tool_calls_processed"], 1)
        backfill(self.db, 1)
        self.assertEqual(self.count("ContextTool"), 1)
        self.assertEqual(self.count("ContextQuery"), 1)
        rows = direct_query(self.db, 'MATCH (r:ContextReasoning) RETURN r.total_tokens AS tokens', {})
        self.assertEqual(rows, [{"tokens": 120}])

    def test_mcp_schema_and_error_response(self):
        record = next(t for t in TOOLS if t['name'] == 'record_turn')
        self.assertIn('model_effort', record['inputSchema']['properties'])
        args = {**self.args, "input_tokens": -1, "project": "project"}
        response = respond(None, None, {'method': 'tools/call', 'params': {'name': 'record_turn', 'arguments': args}})
        self.assertTrue(response['isError'])


if __name__ == "__main__":
    unittest.main()
