import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient

from relate_tap.server import create_app
from relate_tap.recall_client import request


class RecallActivityTest(unittest.TestCase):
    def test_console_reports_unwrapped_mcp_calls_without_recording_question(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            with patch('relate_tap.server.IntentRecall.request',return_value={'state':'ready','matches':[]}), \
                 patch('relate_tap.server.IntentRecall.enabled',return_value=False):
                with TestClient(create_app(root/'data',root/'spool')) as client:
                    body={'project':'repo','mcp_source_id':'risk','query':'private prompt not included in receipt',
                          '_client':{'agent':'claude-desktop','host':'laptop'}}
                    self.assertEqual(client.post('/api/memory/recall',json=body).status_code,200)
                    receipts=client.get('/api/topology').json()['recalls']
                    self.assertEqual(len(receipts),1)
                    self.assertEqual(receipts[0]['agent'],'claude-desktop')
                    self.assertEqual(receipts[0]['tool'],'recall_context')
                    self.assertEqual(receipts[0]['state'],'ready')
                    self.assertIn('finished_at',receipts[0])
                    self.assertNotIn('private prompt',json.dumps(receipts))
                    with patch('relate_tap.server.IntentRecall.request',side_effect=ValueError('invalid scope')):
                        self.assertEqual(client.post('/api/memory/query',json=body).status_code,400)
                    self.assertEqual(client.get('/api/topology').json()['recalls'][-1]['state'],'error')

    def test_native_forwarder_includes_actual_agent_identity(self):
        response=type('Response',(),{'__enter__':lambda self:self,'__exit__':lambda *args:None,
                                    'read':lambda self:b'{"state":"ready"}'})()
        with patch('relate_tap.recall_client.settings',return_value={}), \
             patch('relate_tap.recall_client.socket.gethostname',return_value='laptop'), \
             patch('relate_tap.recall_client.urlopen',return_value=response) as urlopen:
            request('query',{'project':'repo','mcp_source_id':'risk','query_id':'query'},agent='codex')
        body=json.loads(urlopen.call_args.args[0].data)
        self.assertEqual(body['_client'],{'agent':'codex','host':'laptop'})


if __name__=='__main__':
    unittest.main()
