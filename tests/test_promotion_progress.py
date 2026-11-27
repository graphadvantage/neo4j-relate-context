"""Promotion must not let permanently-invalid candidates starve newer valid ones.

CANDIDATES selects the oldest unresolved rows (ORDER BY timestamp ... LIMIT).
Rows that fail Python validation never became intents, so without a durable
rejection marker they refilled that oldest window on every pass and blocked
newer valid candidates from ever reaching embedding. These tests model the
promotion-relevant graph in memory and prove reachability after failures,
process restarts, and repeated runs.
"""
import unittest
from unittest.mock import patch

from relate_tap.intent_recall import IntentRecall, CANDIDATES, REJECT, EMBED, LINK
from relate_tap.intent_model import MODEL, DIMENSIONS, VALIDATOR_VERSION, candidate


def row(prompt_id, query_id, timestamp, cypher='MATCH (n) RETURN n',
        tool='risk__read-cypher', question='Find shared supplier ownership links',
        project='repo'):
    return {'project': project, 'session': 's', 'turn_id': 't-' + prompt_id,
            'prompt_id': prompt_id, 'question': question, 'timestamp': timestamp,
            'tool': tool, 'mcp_source_id': None, 'query_id': query_id, 'cypher': cypher}


class FakeAura:
    """Minimal durable model of the graph promote() reads and writes."""

    def __init__(self, candidates):
        self.candidates = [dict(r) for r in candidates]   # recorded ContextQuery rows
        self.rejected = {}                                # (prompt_id,query_id) -> validator_version
        self.embedded = {}                                # intent id -> search_text
        self.linked = set()                               # (prompt_id,query_id) with EXPRESSES/RESOLVED_BY

    def run(self, query, **params):
        if query == CANDIDATES:
            eligible = [r for r in self.candidates
                        if (r['prompt_id'], r['query_id']) not in self.linked
                        and self.rejected.get((r['prompt_id'], r['query_id'])) != params['validator']]
            eligible.sort(key=lambda r: (r['timestamp'], r['prompt_id'], r['query_id']))
            return [dict(r) for r in eligible[:params['limit']]]
        if query == REJECT:
            for r in params['rows']:
                self.rejected[(r['prompt_id'], r['query_id'])] = params['validator']
            return [{'rejected': len(params['rows'])}]
        if query == EMBED:
            for r in params['rows']:
                self.embedded[r['id']] = r['search_text']
            return [{'id': r['id'], 'dimensions': DIMENSIONS} for r in params['rows']]
        if query == LINK:
            for r in params['rows']:
                self.linked.add((r['prompt_id'], r['query_id']))
            return [{'linked': len(params['rows'])}]
        if query.startswith('MATCH(i:ContextIntent)'):   # already-embedded check
            return [{'id': i} for i in set(params['ids']) if i in self.embedded]
        return []   # initialize() DDL and anything else


class PromotionProgressTest(unittest.TestCase):
    def setUp(self):
        patcher = patch.dict('os.environ', {'OPENAI_API_KEY': 'unit-test-placeholder'})
        patcher.start()
        self.addCleanup(patcher.stop)

    def recall(self, store):
        recall = IntentRecall()
        recall.ready = True   # skip index DDL; initialize() is covered by smoke tests
        recall.run = store.run
        return recall

    def test_newer_valid_candidates_reachable_after_a_full_invalid_batch(self):
        invalid = [row(f'bad-{i:02d}', f'q-bad-{i:02d}', f'2026-01-01T00:{i:02d}:00Z',
                       cypher='MATCH (n) RETURN ...') for i in range(20)]
        valid = [row(f'ok-{i}', f'q-ok-{i}', f'2026-02-01T00:0{i}:00Z',
                     question=f'Find shared supplier ownership links for cluster {i}') for i in range(3)]
        store = FakeAura(invalid + valid)
        recall = self.recall(store)

        first = recall.promote(limit=20)
        self.assertEqual(first['embedded'], 0)
        self.assertEqual(first['rejected'], 20)

        # Before the fix this returned the same 20 invalid rows forever.
        second = recall.promote(limit=20)
        self.assertEqual(second['embedded'], 3)
        self.assertTrue({candidate(r)['id'] for r in valid} <= set(store.embedded))

    def test_rejections_persist_across_a_restart(self):
        invalid = [row(f'bad-{i:02d}', f'q-bad-{i:02d}', f'2026-01-01T00:{i:02d}:00Z',
                       cypher='MATCH (n) RETURN ...') for i in range(20)]
        valid = row('ok-1', 'q-ok-1', '2026-02-01T00:00:00Z')
        store = FakeAura(invalid + [valid])

        self.recall(store).promote(limit=20)          # first process marks the invalid batch

        restarted = self.recall(store)                # new instance, same durable store
        status = restarted.promote(limit=20)
        self.assertEqual(status['rejected'], 0)       # nothing re-marked: rejection survived restart
        self.assertEqual(status['embedded'], 1)
        self.assertIn(candidate(valid)['id'], store.embedded)

    def test_mixed_batch_promotes_valid_and_marks_invalid_without_churn(self):
        rows = [row('bad-1', 'q-bad-1', '2026-01-01T00:00:00Z', cypher='not cypher at all'),
                row('ok-1', 'q-ok-1', '2026-01-01T00:01:00Z', question='Find shared supplier ownership alpha'),
                row('bad-2', 'q-bad-2', '2026-01-01T00:02:00Z', cypher='MATCH (n) RETURN ...'),
                row('ok-2', 'q-ok-2', '2026-01-01T00:03:00Z', question='Find shared supplier ownership beta')]
        store = FakeAura(rows)
        recall = self.recall(store)

        first = recall.promote(limit=20)
        self.assertEqual(first['embedded'], 2)
        self.assertEqual(first['rejected'], 2)

        # Re-running finds everything already embedded or durably rejected.
        second = recall.promote(limit=20)
        self.assertEqual(second['embedded'], 0)
        self.assertEqual(second['rejected'], 0)

    def test_validator_version_bump_readmits_prior_rejections(self):
        store = FakeAura([row('bad-1', 'q-bad-1', '2026-01-01T00:00:00Z',
                              cypher='MATCH (n) RETURN ...')])
        self.recall(store).promote(limit=20)
        self.assertEqual(store.rejected[('bad-1', 'q-bad-1')], VALIDATOR_VERSION)

        # A newer validator no longer matches the old rejection, so the row is
        # eligible again instead of being excluded forever.
        readmitted = store.run(CANDIDATES, model=MODEL, validator=VALIDATOR_VERSION + 1, limit=20)
        self.assertEqual(len(readmitted), 1)


if __name__ == '__main__':
    unittest.main()
