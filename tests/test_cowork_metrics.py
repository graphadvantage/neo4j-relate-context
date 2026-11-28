from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from database_lock import DatabaseLock
from memory_graph import direct_query
from relate_tap.cowork_metrics import CoworkMetrics, cowork_hit, SAVE_HIT
from relate_tap.server import create_app

PROJECT='git:github.com/team/repo'
REQUEST={'context_kind':'Code','project':PROJECT}
CLIENT={'agent':'codex','host':'laptop'}
RESULT={'scope':{'context_kind':'Code','project':PROJECT},'receipt_id':'receipt',
        'state':'ready','matches':[{'turn_id':'peer-turn','agent':'claude-code',
                                  'response':'Use canonical identities'}]}


class CoworkMetricsTest(unittest.TestCase):
    def test_qualifying_evidence_excludes_same_agent_empty_and_session_inventory(self):
        self.assertEqual(cowork_hit('recall',REQUEST,RESULT,CLIENT)['source_agents'],['claude-code'])
        for result in [
                {**RESULT,'matches':[]},
                {**RESULT,'matches':[{'turn_id':'same','agent':'codex','response':'Same-agent evidence'}]},
                {**RESULT,'matches':[],'sessions':[{'id':'peer-session','agent':'claude-code','messages':20}]},
                {**RESULT,'matches':[{'turn_id':'no-content','agent':'claude-code'}]},
                {**RESULT,'state':'error'},
                {**RESULT,'scope':{'context_kind':'Chat','project':PROJECT}},
                {**RESULT,'scope':{'context_kind':'Code','project':'git:github.com/other/repo'}}]:
            self.assertIsNone(cowork_hit('recall',REQUEST,result,CLIENT))
        self.assertIsNone(cowork_hit('recall',REQUEST,RESULT,None))
        self.assertIsNone(cowork_hit('query',REQUEST,RESULT,CLIENT))

    def test_session_evidence_and_pending_local_can_qualify(self):
        for key,operation in [('messages','session'),('evidence','evidence'),('memories','recall')]:
            result={**RESULT,'matches':[],key:[{'message_id':'m','agent':'claude-code','body':'Peer evidence'}]}
            self.assertIsNotNone(cowork_hit(operation,REQUEST,result,CLIENT))
        pending={**RESULT,'state':'pending_replication','matches':[],
                 'pending_local':[{'id':'m','agent':'claude-code','body':'Locally available', 'record_type':'memories'}]}
        self.assertIsNotNone(cowork_hit('recall',REQUEST,pending,CLIENT))

    def test_counts_persist_and_deduplicate_receipts_without_storing_content(self):
        with tempfile.TemporaryDirectory() as directory:
            db=Path(directory)/'capture.rl'
            metrics=CoworkMetrics(db,DatabaseLock(db))
            result=deepcopy(RESULT)
            result['matches'].append({'turn_id':'second','agent':'future-agent','response':'Private evidence'})
            for _ in range(3):self.assertTrue(metrics.record('recall',REQUEST,result,CLIENT))
            self.assertEqual(metrics.statistics()['groups'],[{'agent':'codex','project':PROJECT,'hits':1}])
            reopened=CoworkMetrics(db,DatabaseLock(db))
            self.assertEqual(reopened.statistics(),metrics.statistics())
            saved=direct_query(db,'MATCH(h:CoworkRecallHit) RETURN properties(h) AS metadata',{})
            self.assertEqual(saved[0]['metadata']['source_agents'],['claude-code','future-agent'])
            self.assertNotIn('Private evidence',json.dumps(saved))
            reopened.record('recall',REQUEST,{**RESULT,'receipt_id':'different-request'},CLIENT)
            self.assertEqual(reopened.statistics()['groups'][0]['hits'],2)

    def test_api_counts_successful_cross_agent_evidence_and_survives_app_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            with patch('relate_tap.server.IntentRecall.request',return_value=RESULT), \
                 patch('relate_tap.coding_search.CodingSearch.prepare'), \
                 patch('relate_tap.server.IntentRecall.enabled',return_value=False):
                body={**REQUEST,'query':'Private question','_client':CLIENT}
                with TestClient(create_app(root/'data',root/'spool')) as client:
                    for _ in range(2):self.assertEqual(client.post('/api/memory/recall',json=body).status_code,200)
                    self.assertEqual(client.get('/api/topology').json()['cowork_stats']['groups'][0]['hits'],1)
                    with patch('relate_tap.server.IntentRecall.request',side_effect=ValueError('Bad scope')):
                        self.assertEqual(client.post('/api/memory/recall',json=body).status_code,400)
                with TestClient(create_app(root/'data',root/'spool')) as client:
                    result=client.get('/api/topology').json()['cowork_stats']
                    self.assertEqual(result['state'],'ready')
                    self.assertEqual(result['groups'][0]['hits'],1)

    def test_metric_write_failure_preserves_recall_and_reports_unavailable_totals(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            def query(db,q,p,**kw):
                if q==SAVE_HIT:raise OSError('private filesystem detail')
                return direct_query(db,q,p,**kw)
            with patch('relate_tap.server.IntentRecall.request',return_value=RESULT), \
                 patch('relate_tap.server.IntentRecall.enabled',return_value=False), \
                 patch('relate_tap.coding_search.CodingSearch.prepare'), \
                 patch('relate_tap.cowork_metrics.direct_query',side_effect=query):
                with TestClient(create_app(root/'data',root/'spool')) as client:
                    response=client.post('/api/memory/recall',json={**REQUEST,'_client':CLIENT})
                    self.assertEqual(response.status_code,200)
                    self.assertEqual(response.json()['state'],'ready')
                    topology=client.get('/api/topology').json()
                    self.assertEqual(topology['cowork_stats']['state'],'unavailable')
                    self.assertNotIn('private filesystem detail',json.dumps(topology))


if __name__=='__main__':unittest.main()
