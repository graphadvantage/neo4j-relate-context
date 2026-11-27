import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from relate_tap.privacy import sanitize
from relate_tap.tap import os_username, query_arguments, Recorder, response_details

ROOT = Path(__file__).resolve().parent.parent
STUB = '''import sys,json
for line in sys.stdin.buffer:
    message=json.loads(line)
    if message.get("method")=="tools/call":
        response={"jsonrpc":"2.0","id":message["id"],"result":{"content":[{"type":"text","text":"ok"}]}}
        sys.stdout.buffer.write((json.dumps(response)+"\\n").encode());sys.stdout.buffer.flush()
'''


class TapTest(unittest.TestCase):
    def test_exact_interface_and_transparent_bytes(self):
        with tempfile.TemporaryDirectory() as folder:
            spool = Path(folder)/"spool"
            request = b'{ "jsonrpc":"2.0", "id":7, "method":"tools/call", "params":{"name":"read-cypher","arguments":{"query":"MATCH (n)\\nRETURN n LIMIT $limit", "parameters":{"limit":2}}}}\n'
            result = subprocess.run([sys.executable, '-m', 'relate_tap.tap', '--name', 'neo4j-fraud', '--spool', str(spool), '--', sys.executable, '-c', STUB], input=request, capture_output=True, cwd=ROOT, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            expected = subprocess.run([sys.executable, '-c', STUB], input=request, capture_output=True, timeout=10)
            self.assertEqual(result.stdout, expected.stdout)
            self.assertEqual(result.stderr, b'')
            events = [json.loads(line) for line in next(spool.glob('*.jsonl')).read_text().splitlines()]
            self.assertEqual([e['kind'] for e in events], ['connected','request','response','disconnected'])
            call = events[1]
            self.assertEqual(call['cypher'], 'MATCH (n)\nRETURN n LIMIT $limit')
            self.assertEqual(call['parameters'], {'limit':2})
            self.assertEqual(call['call_id'], events[2]['call_id'])
            self.assertEqual(events[2]['status'], 'response_received')
            self.assertIsNone(events[2]['row_count'])
            self.assertGreaterEqual(events[2]['duration_ms'], 0)

    def test_bytes_are_not_redacted_in_transport(self):
        with tempfile.TemporaryDirectory() as folder:
            # Echo stdin verbatim to prove whitespace, Unicode and secrets are untouched.
            request = json.dumps({'jsonrpc':'2.0','id':'a','method':'tools/call','params':{'name':'query','arguments':{'cypher':"RETURN 'héllo' AS name",'password':'very-private'}}}, ensure_ascii=False).encode()+b'\n'
            result = subprocess.run([sys.executable,'-m','relate_tap.tap','--name','echo','--spool',folder,'--',sys.executable,'-c','import sys;sys.stdout.buffer.write(sys.stdin.buffer.read());sys.stdout.buffer.flush()'],input=request,capture_output=True,timeout=10)
            self.assertEqual(result.stdout,request)
            contents=next(Path(folder).glob('*.jsonl')).read_text()
            self.assertNotIn('very-private',contents)
            self.assertIn('[REDACTED]',contents)

    def test_failure_to_capture_does_not_block_server(self):
        with tempfile.NamedTemporaryFile() as file:
            result=subprocess.run([sys.executable,'-m','relate_tap.tap','--name','stub','--spool',file.name,'--',sys.executable,'-c','print("hello")'],capture_output=True,timeout=10)
            self.assertEqual(result.returncode,0)
            self.assertEqual(result.stdout,b'hello\n')
            self.assertIn(b'capture unavailable',result.stderr)

    def test_child_exit_propagates(self):
        with tempfile.TemporaryDirectory() as folder:
            result=subprocess.run([sys.executable,'-m','relate_tap.tap','--name','stub','--spool',folder,'--',sys.executable,'-c','raise SystemExit(23)'],input=b'',capture_output=True,timeout=10)
            self.assertEqual(result.returncode,23)

    def test_same_rpc_ids_do_not_collide_between_connections(self):
        with tempfile.TemporaryDirectory() as folder:
            recorders=[Recorder(Path(folder),'test') for _ in range(2)]
            for r in recorders:
                r.observe(b'{"id":1,"method":"tools/call","params":{"name":"read","arguments":{"cypher":"RETURN 1"}}}','request')
                r.observe(b'{"id":1,"error":{"code":-1,"message":"bad"}}','response')
                r.close(0)
            events=[[json.loads(l) for l in r.path.read_text().splitlines()] for r in recorders]
            self.assertNotEqual(events[0][0]['call_id'],events[1][0]['call_id'])
            self.assertEqual(events[0][1]['status'],'mcp_error')

    def test_query_detection_and_response_semantics(self):
        self.assertEqual(query_arguments({'query':'// explanation\n MATCH (n) RETURN n'})[0],'// explanation\n MATCH (n) RETURN n')
        self.assertEqual(query_arguments({'query':'search for risk'}),(None,None))
        self.assertEqual(query_arguments({'query':'SELECT * FROM accounts'}),(None,None))
        self.assertEqual(response_details({'result':{'isError':True}}),('tool_error',None))
        self.assertEqual(response_details({'result':{'structuredContent':{'rows':[{},{}]}}}),('response_received',2))

    def test_nested_secret_redaction(self):
        value={'authorization':'Bearer anything','arguments':{'parameters':{'api_key':'abc'},'cypher':"RETURN {password: 'abc'}"},'response':{'text':'neo4j+s://user:pass@host'}}
        safe=sanitize(value)
        self.assertNotIn('abc',json.dumps(safe))
        self.assertNotIn('user:pass@',json.dumps(safe))
        self.assertEqual(value['arguments']['parameters']['api_key'],'abc')

    @unittest.skipIf(sys.platform == 'win32', 'POSIX OS user lookup')
    def test_os_identity_ignores_launcher_environment(self):
        import pwd
        from unittest.mock import patch
        with patch.dict(os.environ, {'USER':'incorrect-user','LOGNAME':'incorrect-user'}):
            self.assertEqual(os_username(),pwd.getpwuid(os.getuid()).pw_name)


if __name__ == '__main__':
    unittest.main()
