import tempfile
import unittest
from pathlib import Path
from fastapi.testclient import TestClient
from unittest.mock import patch
from relate_tap.collector import ContainerRoutes
from relate_tap.diagnostics import Diagnostics
from relate_tap.server import create_app

class DiagnosticsTest(unittest.TestCase):
    def test_recovery_retains_error_and_survives_restart_without_poll_spam(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal=Diagnostics(Path(tmp))
            error={'source':'codex','file':'connection.jsonl','offset':123,'error':'CalledProcessError'}
            journal.update([error]);journal.update([error])
            self.assertEqual(len(journal.snapshot()),1)
            self.assertEqual(len(journal.log.read_text().splitlines()),1)
            journal.update([])
            restored=Diagnostics(Path(tmp))
            self.assertFalse(restored.snapshot()[0]['active'])
            self.assertIsNotNone(restored.snapshot()[0]['resolved_at'])
            restored.update([error])
            self.assertEqual(len(restored.snapshot()),2)
            self.assertEqual(len(restored.log.read_text().splitlines()),3)

    def test_ui_error_persists_redacted_and_can_be_downloaded(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp)
            with TestClient(create_app(base/'data',base/'spool')) as client:
                message='Failed with password="do-not-log-this"'
                result=client.post('/api/errors',json={'source':'Playground query','error':message})
                self.assertEqual(result.status_code,200)
                self.assertNotIn('do-not-log-this',result.text)
                history=client.get('/api/errors').json()
                self.assertEqual(len(history),1)
                self.assertEqual(history[0]['source'],'Playground query')
                self.assertIn(history[0]['id'],client.get('/api/errors/log').text)
                self.assertEqual(client.post('/api/errors',headers={'Origin':'http://evil.example'},json={'source':'x','error':'x'}).status_code,403)
            self.assertEqual(len(Diagnostics(base/'data').snapshot()),1)

    def test_route_replacement_race_retries_without_false_backlog(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);spool=base/'spool';spool.mkdir()
            route=spool/'connection.route.json'
            route.write_text('{"agent":"codex","writer":"native"}')
            collector=ContainerRoutes(base/'data',spool)
            with patch.object(Path,'read_text',side_effect=FileNotFoundError):
                collector.poll()
            self.assertEqual(collector.errors,[])
            collector.poll()
            self.assertEqual(collector.errors,[])
            self.assertEqual(collector.stores,{})

    def test_clear_keeps_active_errors_and_archives_history_across_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);journal=Diagnostics(base)
            error={'source':'codex','file':'capture.jsonl','offset':2,'error':'ValueError'}
            journal.report('Live topology','Disconnected')
            journal.update([error])
            self.assertEqual(journal.clear(),{'cleared':1,'active':1})
            restored=Diagnostics(base)
            self.assertEqual(len(restored.snapshot()),1)
            self.assertTrue(restored.snapshot()[0]['active'])
            self.assertEqual(len(list((base/'errors-archive').glob('*.jsonl'))),1)
            restored.update([])
            self.assertEqual(restored.clear(),{'cleared':1,'active':0})
            self.assertEqual(Diagnostics(base).snapshot(),[])
            restored.report('Playground query','New error')
            self.assertEqual(len(restored.snapshot()),1)

    def test_clear_api_does_not_allow_cross_origin_requests(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp)
            with TestClient(create_app(base/'data',base/'spool')) as client:
                client.post('/api/errors',json={'source':'Test','error':'Test error'})
                denied=client.post('/api/errors/clear',headers={'Origin':'http://evil.example'})
                self.assertEqual(denied.status_code,403)
                self.assertEqual(len(client.get('/api/errors').json()),1)
                self.assertEqual(client.post('/api/errors/clear').json(),{'cleared':1,'active':0})
                self.assertEqual(client.get('/api/errors').json(),[])
