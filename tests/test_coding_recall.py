import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from memory_graph import direct_query, EXCHANGE, exchange, save_note
from memory_mcp import call
from relate_tap.coding_recall import CodingRecall


class CodingRecallTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.db=Path(self.tmp.name)/'test.rl'
        def run(statement,**params):return direct_query(self.db,statement.replace('$','$input.'),params,read_only=True)
        self.recall=CodingRecall(run)
        for project,agent,kind,session in [('repo','codex','Code','shared'),('repo','claude-code','Code','other'),
                ('outside','codex','Code','outside'),('repo','claude-desktop','Chat','chat')]:
            for index in range(3):
                data=exchange(project,agent,{'session':session,'turn':str(index),'context_kind':kind,
                    'prompt':'Explain the snapshot architecture '+str(index),
                    'response':'Use one local store per agent. Snapshots push to Aura.',
                    'reasoning_summary':'Independent stores serialize writes.',
                    'memories':[{'kind':'decision','subject':'Persistence','body':'Keep disk persistence.'}]})
                direct_query(self.db,EXCHANGE,data,read_only=False)

    def test_shared_recall_includes_both_agents_without_cypher_or_embeddings(self):
        r=self.recall.request('recall',{'project':'repo','query':'snapshot architecture','limit':10})
        self.assertEqual(r['state'],'ready')
        self.assertEqual({s['agent'] for s in r['sessions']},{'codex','claude-code'})
        self.assertEqual({m['agent'] for m in r['matches']},{'codex','claude-code'})
        self.assertEqual(len(r['matches']),6)
        self.assertTrue(all(m['turn_id'] and m['response'] for m in r['matches']))
        self.assertTrue(r['memories'])
        self.assertEqual(r['retrieval_mode'],'lexical_recency')

    def test_raw_turn_history_remains_retrievable_without_structured_memories(self):
        direct_query(self.db,'MATCH(f:ContextMemory) DETACH DELETE f',{},read_only=False)
        result=self.recall.request('recall',{'project':'repo','query':'snapshot'})
        self.assertEqual(result['state'],'ready')
        self.assertTrue(result['matches'])
        self.assertEqual(result['memories'],[])
        save_note(lambda q,p,**kw:direct_query(self.db,q,p,**kw),'repo','codex',
                  {'title':'Architecture','body':'Use disk persistence','context_kind':'Code'})
        self.assertTrue(self.recall.request('recall',{'project':'repo'})['notes'])

    def test_scope_agent_and_session_expansion(self):
        r=self.recall.request('recall',{'project':'repo','source_agent':'codex','limit':10})
        self.assertEqual({m['agent'] for m in r['matches']},{'codex'})
        key=r['sessions'][0]['id']
        s=self.recall.request('session',{'project':'repo','session':key,'limit':2})
        self.assertEqual(len(s['messages']),2)
        next_page=self.recall.request('session',{'project':'repo','session':key,'limit':2,'before_position':s['next_before_position']})
        self.assertLess(max(m['position'] for m in next_page['messages']),min(m['position'] for m in s['messages']))
        self.assertEqual(self.recall.request('session',{'project':'outside','session':key})['messages'],[])
        turn=r['matches'][0]['turn_id']
        e=self.recall.request('evidence',{'project':'repo','turn_id':turn})
        self.assertEqual(len(e['evidence']),2)
        self.assertEqual(self.recall.request('evidence',{'project':'outside','turn_id':turn})['evidence'],[])

    def test_no_topic_hits_still_reports_existing_sessions(self):
        r=self.recall.request('recall',{'project':'repo','query':'unfindableword'})
        self.assertEqual(r['matches'],[])
        self.assertEqual(len(r['sessions']),2)
        self.assertEqual(r['state'],'ready')

    def test_aura_failure_is_degraded_not_empty_and_scope_errors_make_no_calls(self):
        broken=CodingRecall(lambda *a,**kw:(_ for _ in ()).throw(OSError('private')))
        r=broken.request('recall',{'project':'repo'})
        self.assertEqual(r['state'],'degraded')
        self.assertNotIn('private',str(r))
        with self.assertRaises(ValueError):broken.request('recall',{'project':'repo','mcp_source_id':'relate-memory-mcp'})

    def test_mcp_defaults_repository_work_to_shared_recall(self):
        with patch('memory_mcp.context_kind',return_value='Code'), patch('relate_tap.recall_client.request',return_value={'state':'ready'}) as forward:
            call(None,None,'recall_context',{},'claude-code','repo')
            self.assertEqual(forward.call_args.args[1]['context_kind'],'Code')
            call(None,None,'get_session',{'session':'shared'},'claude-code','repo')
            self.assertEqual(forward.call_args.args[0],'session')
            call(None,None,'get_evidence',{'turn_id':'t'},'codex','repo')
            self.assertEqual(forward.call_args.args[0],'evidence')

    def test_bounded_response_and_local_pending_are_honest(self):
        class Sources:
            def public(self):return [{'kind':'relate','id':'code','name':'codex.rl'}]
            def query(self,source,q,p):return {'rows':[{'id':'local','agent':'codex'}]} if 'AS sessions' not in q else {'rows':[]}
        pending=CodingRecall(lambda *a,**kw:[],Sources()).request('recall',{'project':'repo'})
        self.assertEqual(pending['state'],'pending_replication')
        self.assertTrue(all(r['origin']=='codex.rl' for r in pending['pending_local']))
        r=self.recall.request('recall',{'project':'repo','max_chars':2000})
        import json
        self.assertLessEqual(len(json.dumps(r,ensure_ascii=False)),2000)
        self.assertTrue(r['truncated'])
