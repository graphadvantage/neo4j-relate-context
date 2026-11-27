import json
import unittest
from unittest.mock import patch

from memory_mcp import respond
from relate_tap.intent_model import bounded, candidate, fuse, lucene_query, scope, source_from_tool, query_integrity
from memory_graph import exchange
from relate_tap.intent_recall import IntentRecall, ENN, ANN, FULLTEXT, REFERENCES


class IntentModelTest(unittest.TestCase):
    def test_descriptions_are_not_cypher_and_search_text_does_not_create_queries(self):
        for text in ('tie-break by total sharing links and max pair overlap',
                     'Customer PII detail UNION pairs sharing all 3 items'):
            self.assertEqual(query_integrity(text),'not_cypher')
            self.assertIsNone(candidate(self.row(cypher=text)))
            with self.assertRaisesRegex(ValueError,'not a description'):
                exchange('repo','claude-desktop',{'session':'s','turn':'t','prompt':'p','response':'r',
                    'tool_calls':[{'name':'risk__read_cypher','cypher':text}]})
        payload=exchange('repo','claude-desktop',{'session':'s','turn':'t','prompt':'p','response':'r',
            'tool_calls':[{'name':'ToolSearch','arguments':{'query':'+neo4j-mcp-fraud'}}]})
        self.assertIsNone(payload['messages'][1]['cypher'])
        self.assertEqual(query_integrity('// comment\n/* explanation */ MATCH(n) RETURN n'),'not_validated')

    def test_recall_budget_keeps_query_id_and_requires_full_fetch(self):
        result=bounded({'matches':[{'id':'i','queries':[{'id':'q','cypher':'RETURN "'+('x'*5000)+'"','parameters':{}}]}]},2000)
        query=result['matches'][0]['queries'][0]
        self.assertEqual(query['id'],'q')
        self.assertNotIn('cypher',query)
        self.assertTrue(query['retrieval_required'])
        self.assertTrue(result['truncated'])
    def test_abbreviated_queries_are_rejected_without_rejecting_literals(self):
        for cypher in ('MATCH (n) RETURN ...','MATCH (n) RETURN …'):
            self.assertEqual(query_integrity(cypher),'incomplete')
            self.assertIsNone(candidate(self.row(cypher=cypher)))
            with self.assertRaisesRegex(ValueError,'Incomplete Cypher'):
                exchange('repo','claude-desktop',{'session':'s','turn':'t','prompt':'p','response':'r',
                    'tool_calls':[{'name':'risk__read_cypher','cypher':cypher}]})
        for cypher in ("RETURN '...' AS text",'RETURN "…" AS text','RETURN 1 // ...',
                       'RETURN 1 /* ... */','MATCH (n) RETURN n.`...`'):
            self.assertEqual(query_integrity(cypher),'not_validated')

    def test_reported_query_must_match_tool_arguments(self):
        with self.assertRaisesRegex(ValueError,'must match'):
            exchange('repo','claude-desktop',{'session':'s','turn':'t','prompt':'p','response':'r',
                'tool_calls':[{'name':'risk__read_cypher','arguments':{'query':'RETURN 1 AS answer'},'cypher':'RETURN ...'}]})
    def row(self, **change):
        return {'project':'repo','prompt_id':'prompt','turn_id':'turn','question':'Find shared supplier ownership',
                'query_id':'query','cypher':'MATCH (n) RETURN n','tool':'risk__read-cypher',**change}

    def test_scopes_do_not_depend_on_agent_or_transport(self):
        a=candidate(self.row())
        self.assertEqual(a['mcp_source_id'],'risk')
        self.assertEqual(a['id'],candidate(self.row(prompt_id='another-agent-message'))['id'])
        self.assertNotEqual(a['id'],candidate(self.row(tool='fraud__read-cypher'))['id'])
        self.assertNotEqual(a['id'],candidate(self.row(project='other-repo'))['id'])

    def test_ambiguous_queries_are_not_assigned_a_source(self):
        self.assertIsNone(candidate(self.row(tool='read_cypher')))
        self.assertIsNone(candidate(self.row(query_id=None)))
        self.assertIsNone(candidate(self.row(question='ok')))
        self.assertEqual(candidate(self.row(tool='read_cypher',mcp_source_id='risk'))['mcp_source_id'],'risk')
        self.assertEqual(source_from_tool('mcp__robot-twin__read_cypher'),'robot-twin')
        self.assertEqual(source_from_tool('robot-twin.read_cypher'),'robot-twin')

    def test_scope_required_and_lucene_cannot_widen_it(self):
        for project,source in [('', 'risk'),('repo',None),('repo','risk OR *:*')]:
            with self.assertRaises(ValueError): scope(project,source)
        key=scope('repo','risk')['scope_key']
        query=lucene_query('supplier OR *:* scope_key:other',key)
        self.assertTrue(query.startswith('scope_key:'+key+' AND search_text:('))
        self.assertNotIn('*:*',query)
        self.assertIn('"OR"',query)

    def test_hybrid_uses_ranks_not_incompatible_raw_scores(self):
        vector=[{'id':'a','score':0.99},{'id':'b','score':0.7}]
        text=[{'id':'b','score':100000},{'id':'c','score':99999}]
        result=fuse(vector,text,3)
        self.assertEqual(result[0]['id'],'b')
        self.assertEqual(result[1]['id'],'a')
        self.assertEqual(result[0]['matched_by'],['vector','fulltext'])

    def test_budget_never_returns_a_truncated_executable_query(self):
        result=bounded({'query':{'cypher':'MATCH (n) RETURN n '+'x'*3000}},2000)
        self.assertIsNone(result['query'])
        self.assertTrue(result['truncated'])
        self.assertGreater(result['required_chars'],2000)
        result=bounded({'matches':[{'question':'x'*1500},{'question':'y'*1500}]},2000)
        self.assertLessEqual(len(json.dumps(result)),2000)
        self.assertTrue(result['truncated'])


class RecallContractTest(unittest.TestCase):
    def test_historical_abbreviation_is_not_presented_as_ready(self):
        recall=IntentRecall()
        recall.run=lambda *a,**k:[{'id':'q','cypher':'MATCH (n) RETURN ...'}]
        result=recall.request('query',{'project':'repo','mcp_source_id':'risk','query_id':'q'})
        self.assertEqual(result['state'],'incomplete')
        self.assertFalse(result['query']['reusable'])
        self.assertEqual(result['query']['cypher'],'MATCH (n) RETURN ...')

    def setUp(self):
        self.recall=IntentRecall()
        self.recall.ready=True
        self.calls=[]
        self.recall.pending=lambda *args:([],[])
        def run(query,**params):
            self.calls.append((query,params))
            if 'ai.text.embed(' in query:
                return [{'id':'intent','question':'Find supplier owners','validation':'client_reported_unverified','score':0.8}]
            if query==FULLTEXT:
                return [{'id':'intent','question':'Find supplier owners','score':10}]
            if query==REFERENCES:
                return [{'id':'intent','queries':[{'id':'query','cypher':'MATCH (n) RETURN n','parameters':'{}'}]}]
            return []
        self.recall.run=run

    def test_all_search_paths_receive_both_scopes(self):
        with patch.dict('os.environ',{'OPENAI_API_KEY':'unit-test-placeholder'}):
            result=self.recall.request('recall',{'project':'repo','mcp_source_id':'risk','query':'supplier ownership'})
        self.assertEqual(result['state'],'ready')
        self.assertEqual(result['matches'][0]['queries'][0]['id'],'query')
        self.assertEqual(result['matches'][0]['queries'][0]['cypher'],'MATCH (n) RETURN n')
        self.assertFalse(result['matches'][0]['queries'][0]['retrieval_required'])
        self.assertNotIn('preview',result['matches'][0]['queries'][0])
        for query,params in self.calls:
            self.assertEqual(params['project'],'repo')
            self.assertEqual(params['mcp_source_id'],'risk')
        self.assertIn('scope_key:',next(p['lucene'] for q,p in self.calls if q==FULLTEXT))

    def test_missing_scope_and_invalid_budget_make_no_provider_calls(self):
        for body in ({'project':'repo','query':'supplier'},
                     {'project':'repo','mcp_source_id':'risk','query':'supplier','max_chars':100}):
            with self.assertRaises(ValueError): self.recall.request('recall',body)
        self.assertEqual(self.calls,[])

    def test_failures_are_visible_and_never_expand_scope(self):
        self.recall.run=lambda *args,**kwargs:(_ for _ in ()).throw(RuntimeError('private details'))
        with patch.dict('os.environ',{'OPENAI_API_KEY':'unit-test-placeholder'}):
            result=self.recall.request('recall',{'project':'repo','mcp_source_id':'risk','query':'supplier'})
        self.assertEqual(result['state'],'degraded')
        self.assertNotIn('private details',json.dumps(result))
        self.assertEqual(result['matches'],[])

    def test_tools_share_existing_mcp_and_keep_text_results(self):
        message={'method':'tools/call','params':{'name':'get_query','arguments':
                 {'project':'repo','mcp_source_id':'risk','query_id':'query'}}}
        with patch('relate_tap.recall_client.request',return_value={'query':{'cypher':'RETURN 1'}}) as client:
            result=respond(None,None,message)
        self.assertEqual(json.loads(result['content'][0]['text'])['query']['cypher'],'RETURN 1')
        self.assertEqual(client.call_args.args[0],'query')
        self.assertTrue(respond(None,None,{'method':'tools/call','params':{'name':'get_query',
            'arguments':{'project':'repo','query_id':'query'}}})['isError'])


if __name__=='__main__': unittest.main()
