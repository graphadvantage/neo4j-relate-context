import json
import os
from pathlib import Path
import tempfile
import unittest
import signal
import subprocess
import sys
import time
from unittest.mock import patch

from memory_graph import direct_query
from relate_tap.bootstrap import canonical_agent, client_identity, database_for, discover_agent, install_skill
from relate_tap.registry import registry
from relate_tap.tap import Recorder
from relate_tap.worker import run
from relate_tap.memory import execute


class BootstrapTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.spool = self.base/'spool'; self.spool.mkdir()
        self.environment = patch.dict(os.environ, {
            'RELATE_TAP_HOME': str(self.base/'tap'), 'RELATE_TAP_DATABASE_DIR': str(self.base/'databases'),
            'CLAUDE_CONFIG_DIR': str(self.base/'claude'), 'CODEX_HOME': str(self.base/'codex'),
            'RELATE_TAP_AURA_CREDENTIALS': '', 'RELATE_TAP_WRITER': 'native',
        })
        self.environment.start()
        self.launch = patch('relate_tap.tap.start_worker')
        self.start_worker = self.launch.start()

    def tearDown(self):
        self.launch.stop(); self.environment.stop(); self.temporary.cleanup()

    def connect(self, client, server='risk', override=None):
        recorder = Recorder(self.spool, server, override)
        recorder.emit('connected')
        recorder.observe(json.dumps({'id': 0, 'method': 'initialize', 'params': {
            'clientInfo': {'name': client, 'version': 'test'}}}).encode(), 'request')
        return recorder

    def test_discovery_creates_each_agent_database_and_preserves_existing_data(self):
        for client, agent in [('claude-ai','claude-desktop'),('claude-code','claude-code'),('codex-mcp-client','codex')]:
            recorder = self.connect(client)
            self.assertEqual(recorder.agent, agent)
            route = json.loads(recorder.route_path.read_text())
            self.assertEqual(route['database'], agent+'.rl')
            self.assertEqual(route['identity_source'], 'mcp_client')
            recorder.observe(b'{"id":1,"method":"tools/call","params":{"name":"read-cypher","arguments":{"query":"RETURN 1 AS n"}}}', 'request')
            recorder.observe(b'{"id":1,"result":{"content":[]}}','response')
            recorder.close(0)
            database = database_for(agent)
            self.assertFalse(database.exists())
            run(agent, self.spool, idle_seconds=0)
            self.assertTrue(database.is_file())
            direct_query(database, 'CREATE (:Preserved {value: 7})', {}, read_only=False)
            run(agent, self.spool, idle_seconds=0)
            rows = direct_query(database, 'MATCH(n:Preserved) RETURN n.value AS value LIMIT 1', {})
            self.assertEqual(rows, [{'value':7}])
            rows = direct_query(database, 'MATCH(q:ContextQuery) RETURN count(q) AS count', {})
            self.assertEqual(rows, [{'count':1}])
            status = json.loads((self.spool/(agent+'.status.json')).read_text())
            self.assertEqual(status['state'], 'ready')
            self.assertGreater(status['offsets'][recorder.path.name],0)
        self.assertEqual(len(list((self.base/'databases').glob('*.rl'))),3)

    def test_two_servers_share_one_agent_database(self):
        a=self.connect('claude-ai','wam'); b=self.connect('claude-ai','counterparty-risk')
        a.close(0); b.close(0)
        run('claude-desktop',self.spool,idle_seconds=0)
        rows=direct_query(database_for('claude-desktop'),'MATCH(c:CaptureConnection) RETURN c.server AS server ORDER BY server LIMIT 10',{})
        self.assertEqual(rows,[{'server':'counterparty-risk'},{'server':'wam'}])
        self.assertEqual(len(list((self.base/'databases').glob('*.rl'))),1)

    def test_desktop_local_agent_clients_share_the_canonical_desktop_database(self):
        a=self.connect('claude-ai','wam')
        b=self.connect('local-agent-mode-wam','wam')
        self.assertEqual(b.agent,'claude-desktop')
        a.close(0); b.close(0)
        # A pending registration from an already-running older tap is also resolved.
        pending=json.loads(b.route_path.read_text()); pending.update(agent=None,database=None,database_path=None)
        b.route_path.write_text(json.dumps(pending))
        run('claude-desktop',self.spool,idle_seconds=0)
        routes=registry(self.spool)['routes']
        self.assertEqual({r['agent'] for r in routes},{'claude-desktop'})
        self.assertEqual({r['database'] for r in routes},{'claude-desktop.rl'})
        self.assertTrue(all(r['persisted_offset']>0 for r in routes))
        rows=direct_query(database_for('claude-desktop'),'MATCH(c:CaptureConnection) RETURN count(c) AS count',{})
        self.assertEqual(rows,[{'count':2}])

    def test_override_wins_and_repeated_handshake_does_not_repeat_bootstrap(self):
        r=self.connect('claude-ai',override='codex')
        r.observe(b'{"id":2,"method":"initialize","params":{"clientInfo":{"name":"claude-ai"}}}', 'request')
        self.assertEqual(r.agent,'codex')
        self.assertEqual(r.route['identity_source'],'configured')
        self.start_worker.assert_called_once()
        r.close(0)

    def test_unknown_client_is_registered_without_guessing_or_creating_a_database(self):
        r=self.connect('unknown-client')
        self.assertIsNone(r.agent)
        self.assertIsNone(json.loads(r.route_path.read_text())['database'])
        self.start_worker.assert_not_called()
        self.assertFalse((self.base/'databases').exists())
        r.close(0)

    def test_stateless_metadata_and_safe_names(self):
        info=client_identity({'method':'tools/call','params':{'_meta':{'io.modelcontextprotocol/clientInfo':{'name':'codex-mcp-client','version':'test'}}}})
        self.assertEqual(discover_agent(info),'codex')
        for name in ['../claude-desktop','/tmp/graph','agent.rl','x y']:
            with self.assertRaises(ValueError): canonical_agent(name)

    def test_skill_installation_is_non_destructive_and_desktop_uses_mcp(self):
        self.assertEqual(install_skill('claude-desktop')['state'],'mcp')
        self.assertFalse((self.base/'claude').exists())
        first=install_skill('codex'); self.assertEqual(first['state'],'installed')
        path=Path(first['path']); path.write_text('user customized skill')
        self.assertEqual(install_skill('codex')['state'],'existing')
        self.assertEqual(path.read_text(),'user customized skill')
        self.assertEqual(install_skill('claude-code')['state'],'installed')

    def test_registry_distinguishes_live_and_closed_connections(self):
        r=self.connect('claude-ai')
        self.assertTrue(registry(self.spool)['routes'][0]['active'])
        r.close(0)
        self.assertFalse(registry(self.spool)['routes'][0]['active'])

    def test_installed_memory_skill_has_working_native_tools_without_an_extra_mcp(self):
        with patch('relate_tap.memory.start_worker'):
            execute('codex','bootstrap-test','save',{'title':'A note','body':'Saved through the installed skill bridge.'})
            recalled=execute('codex','bootstrap-test','recall')
            self.assertEqual(recalled['notes'][0]['source'],'codex')
            self.assertEqual(recalled['notes'][0]['body'],'Saved through the installed skill bridge.')
            execute('codex','bootstrap-test','record-turn',{'session':'test-session','turn':'test-turn','prompt':'test prompt','response':'test response'})
        rows=direct_query(database_for('codex'),'MATCH(s:ContextSession) RETURN s.source AS source LIMIT 1',{})
        self.assertEqual(rows,[{'source':'codex'}])
        skill=install_skill('codex')
        self.assertIn('memory --agent codex',Path(skill['path']).read_text())
        self.assertNotIn('__AGENT__',Path(skill['path']).read_text())

    def test_detached_native_worker_bootstraps_from_real_stdio_handshake(self):
        server='''import sys,json
for line in sys.stdin:
 m=json.loads(line)
 print(json.dumps({'jsonrpc':'2.0','id':m['id'],'result':{'content':[]}}),flush=True)
'''
        workers=[]
        try:
            for client,agent in [('claude-ai','claude-desktop'),('claude-code','claude-code'),('codex-mcp-client','codex')]:
                messages=[{'jsonrpc':'2.0','id':0,'method':'initialize','params':{'clientInfo':{'name':client,'version':'test'}}},
                    {'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':'read-cypher','arguments':{'query':'RETURN 1 AS n'}}}]
                request=''.join(json.dumps(m)+'\n' for m in messages)
                result=subprocess.run([sys.executable,'-m','relate_tap.tap','--name','native-smoke','--spool',str(self.spool),'--',sys.executable,'-c',server],
                    input=request,text=True,capture_output=True,timeout=10)
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertEqual([json.loads(l)['id'] for l in result.stdout.splitlines()],[0,1])
                self.assertEqual(result.stderr,'')
                status_file=self.spool/(agent+'.status.json')
                deadline=time.monotonic()+8
                while time.monotonic()<deadline:
                    if status_file.exists():
                        status=json.loads(status_file.read_text())
                        if status.get('last_persisted'): break
                    time.sleep(0.05)
                else: self.fail('Native bootstrap worker did not persist capture')
                workers.append(status['worker_pid'])
                self.assertEqual(status['state'],'ready')
                rows=direct_query(database_for(agent),'MATCH(q:ContextQuery) RETURN count(q) AS count',{})
                self.assertEqual(rows,[{'count':1}])
        finally:
            for pid in workers:
                try: os.kill(pid,signal.SIGTERM)
                except ProcessLookupError: pass


if __name__=='__main__': unittest.main()
