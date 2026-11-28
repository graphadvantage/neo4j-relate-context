import json
from pathlib import Path
import tempfile
import unittest

from memory_graph import EXCHANGE, direct_query, exchange
from relate_tap.coding_recall import CodingRecall, FULLTEXT_TURNS, TURNS
from relate_tap.coding_search import CodingSearch, search_query


class CodingSearchTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.db=Path(self.tmp.name)/'search.rl'
        def run(q,**p):return direct_query(self.db,q.replace('$','$input.'),p,read_only=False)
        self.run=run
        for project,agent,response in [('one','codex','Snapshots preserve database consistency'),
                ('one','claude-code','Snapshot maintenance'),('two','codex','Snapshot snapshot snapshot')]:
            direct_query(self.db,EXCHANGE,exchange(project,agent,{'session':agent,'turn':'t',
                'context_kind':'Code','prompt':'Architecture decisions','response':response,
                'reasoning_summary':'Independent serialization'}),read_only=False)
        self.search=CodingSearch(run)
        self.recall=CodingRecall(run,search=self.search)

    def test_existing_turns_are_indexed_without_embeddings_and_scoped_before_limit(self):
        state=self.search.prepare()
        self.assertEqual(state['state'],'ready',state)
        r=self.recall.request('recall',{'project':'one','query':'Snapshots','limit':1})
        self.assertEqual(r['state'],'ready',r)
        self.assertEqual(r['retrieval_mode'],'fulltext_recency')
        self.assertTrue(r['matches'])
        self.assertEqual(r['matches'][0]['response'],'Snapshots preserve database consistency')
        r=self.recall.request('recall',{'project':'one','source_agent':'claude-code','query':'Snapshot'})
        self.assertEqual([m['agent'] for m in r['matches']],['claude-code'])
        # Match reasoning text, even when neither prompt nor response includes it.
        r=self.recall.request('recall',{'project':'one','query':'serialization'})
        self.assertEqual(len(r['matches']),2)

    def test_indexing_is_incremental_and_resumes_with_a_new_worker(self):
        self.assertEqual(self.search.prepare(batch=1)['state'],'building')
        restarted=CodingSearch(self.run)
        self.assertEqual(restarted.prepare(batch=10),{'state':'ready','indexed':2})
        self.assertEqual(restarted.prepare(batch=10),{'state':'ready','indexed':0})

    def test_missing_index_is_disclosed_and_recreated_on_next_maintenance_pass(self):
        self.search.prepare()
        self.run('DROP INDEX context_code_turns_text')
        self.assertEqual(self.search.prepare()['state'],'unavailable')
        self.assertEqual(self.search.prepare()['state'],'ready')

    def test_query_operators_are_quoted_and_empty_search_uses_recent_history(self):
        self.assertEqual(search_query('owner OR *:* project:else'), '"owner" OR "OR" OR "project" OR "else"')
        self.assertIsNone(search_query('***'))
        r=self.recall.request('recall',{'project':'one','query':''})
        self.assertEqual(r['retrieval_mode'],'lexical_recency')
        self.assertEqual(len(r['matches']),2)

    def test_index_failure_falls_back_within_the_same_scope_and_discloses_it(self):
        self.search.prepare()
        queries=[]
        def run(q,**p):
            queries.append((q,p))
            if q==FULLTEXT_TURNS:raise OSError('private endpoint')
            return self.run(q,**p)
        r=CodingRecall(run,search=self.search).request('recall',{'project':'one','query':'snapshot'})
        self.assertEqual(r['state'],'degraded')
        self.assertEqual(r['retrieval_mode'],'lexical_recency')
        self.assertTrue(r['matches'])
        self.assertNotIn('private endpoint',json.dumps(r))
        self.assertTrue(any(q==TURNS for q,p in queries))
        self.assertTrue(all(p['project']=='one' for q,p in queries))

    def test_index_setup_failure_is_visible_and_fallback_remains_available(self):
        def broken(q,**p):raise OSError('private endpoint')
        r=CodingRecall(self.run,search=CodingSearch(broken)).request('recall',{'project':'one','query':'snapshot'})
        self.assertEqual(r['state'],'degraded')
        self.assertEqual(r['retrieval_mode'],'lexical_recency')
        self.assertEqual(r['search_index']['state'],'unavailable')
        self.assertTrue(r['matches'])
        self.assertNotIn('private endpoint',json.dumps(r))

    def test_local_pending_uses_read_only_lexical_queries(self):
        self.search.prepare()
        queries=[]
        class Sources:
            def public(self):return [{'kind':'relate','id':'local','name':'codex.rl'}]
            def query(self,source,q,p):
                queries.append(q)
                return {'rows':[]}
        CodingRecall(self.run,Sources(),self.search).request('recall',{'project':'one','query':'snapshot'})
        self.assertIn(TURNS,queries)
        self.assertNotIn(FULLTEXT_TURNS,queries)


if __name__=='__main__':unittest.main()
