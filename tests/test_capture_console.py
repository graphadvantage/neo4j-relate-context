import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from memory_graph import direct_query
from relate_tap.server import create_app
from relate_tap.sources import read_statement, Sources, wire
from relate_tap.store import CaptureStore


def event():
    return {'id':'event-1','kind':'request','timestamp':'2026-11-25T12:00:00+00:00','connection':'conn-1',
            'server':'neo4j-fraud','host':'test-host','username':'test-user','agent':'Claude Desktop',
            'project':'test-project','call_id':'call-1','tool':'read-cypher',
            'cypher':'MATCH (n)\nRETURN n LIMIT $limit','parameters':{'limit':3},'redacted':False,'query_sha256':'abc'}


class CaptureTest(unittest.TestCase):
    def test_real_relate_persistence_replay_and_partial_line(self):
        with tempfile.TemporaryDirectory() as folder:
            base=Path(folder); spool=base/'spool'; spool.mkdir()
            path=spool/'capture.jsonl'
            line=(json.dumps(event())+'\n').encode()
            path.write_bytes(line[:-2])
            store=CaptureStore(base/'capture.rl',spool,base/'offsets.json')
            store.poll()
            self.assertEqual(store.snapshot()['events'],[])
            path.write_bytes(line)
            store.poll()
            self.assertEqual(store.errors,[])
            self.assertEqual(store.offsets[path.name],len(line))
            self.assertEqual(store.snapshot()['events'][0]['cypher'],event()['cypher'])
            # Force replay as after a crash between graph commit and checkpoint rename.
            store.offsets={}
            store.poll()
            rows=direct_query(store.database,'MATCH (e:CaptureEvent) RETURN count(e) AS count',{})
            self.assertEqual(rows[0]['count'],1)
            queries=direct_query(store.database,'MATCH (q:ContextQuery) RETURN q.cypher AS cypher, q.parameters AS params',{})
            self.assertEqual(queries,[{'cypher':event()['cypher'],'params':json.dumps({'limit':3})}])
            restored=CaptureStore(store.database,spool,store.state)
            self.assertEqual(len(restored.events),1)
            self.assertEqual(restored.offsets[path.name],len(line))

    def test_no_checkpoint_on_failed_persistence(self):
        with tempfile.TemporaryDirectory() as folder:
            base=Path(folder); (base/'events.jsonl').write_text(json.dumps(event())+'\n')
            store=CaptureStore(base/'graph.rl',base,base/'offsets.json')
            with patch.object(store,'persist',side_effect=RuntimeError('test')):
                store.poll()
            self.assertEqual(store.offsets,{})
            self.assertEqual(store.errors[0]['offset'],0)

    def test_checkpoint_failure_retries_same_event(self):
        with tempfile.TemporaryDirectory() as folder:
            base=Path(folder); (base/'events.jsonl').write_text(json.dumps(event())+'\n')
            store=CaptureStore(base/'graph.rl',base,base/'offsets.json')
            with patch('relate_tap.store.os.replace',side_effect=OSError):
                store.poll()
            self.assertEqual(store.offsets,{})
            store.poll()
            self.assertEqual(store.errors,[])
            self.assertEqual(len(store.events),1)

    def test_api_sources_queries_and_origin(self):
        with tempfile.TemporaryDirectory() as folder:
            base=Path(folder); app=create_app(base/'data',base/'spool')
            app.state.store.persist(event())
            with TestClient(app) as client:
                self.assertEqual(client.get('/api/sources').json(),[{'id':'capture','name':'Tap capture','kind':'relate','read_only':True}])
                result=client.post('/api/query',json={'source':'capture','query':'MATCH (q:ContextQuery) RETURN q.cypher AS text LIMIT $limit','parameters':{'limit':2}})
                self.assertEqual(result.status_code,200,result.text)
                self.assertEqual(result.json()['rows'][0]['text'],event()['cypher'])
                self.assertIn('timings',result.json())
                self.assertFalse(result.json()['truncated'])
                self.assertEqual(client.post('/api/query',json={'source':'/etc/passwd','query':'RETURN 1'}).status_code,400)
                self.assertEqual(client.post('/api/query',json={'source':'capture','query':'MATCH (n) DELETE n'}).status_code,400)
                self.assertEqual(client.post('/api/query',headers={'Origin':'http://evil.example'},json={'source':'capture','query':'RETURN 1'}).status_code,403)
                self.assertEqual(len(client.get('/api/topology').json()['events']),1)

    def test_conservative_read_only_policy(self):
        for query in ["MATCH(n) RETURN n LIMIT 10", "RETURN 'CREATE (n); CALL foo()' AS text;", "/* SET x */ RETURN 1"]:
            read_statement(query)
        for query in ["CREATE (n)","MATCH(n) SET n.x=1", "RETURN 1; CREATE(n)","CALL apoc.cypher.run('CREATE(n)',{})", "WITH 1 AS x CALL { CREATE(n) RETURN n } RETURN x", "RETURN 'unterminated", "LOAD CSV FROM 'file:///etc/passwd' AS r RETURN r"]:
            with self.assertRaises(ValueError,msg=query):
                read_statement(query)

    def test_aura_registry_keeps_credentials_server_side(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'sources.json'
            path.write_text(json.dumps({'sources':[{'id':'enterprise','name':'Enterprise context','kind':'aura',
                'uri_env':'PRIVATE_URI','username_env':'PRIVATE_USER','password_env':'PRIVATE_PASSWORD'}]}))
            sources=Sources(Path(folder)/'graph.rl',path)
            self.assertEqual(sources.public()[1],{'id':'enterprise','name':'Enterprise context','kind':'aura','read_only':True})
            self.assertNotIn('PRIVATE_',json.dumps(sources.public()))

    def test_inventory_discovers_agent_files_and_tracks_changes(self):
        with tempfile.TemporaryDirectory() as folder:
            base=Path(folder)
            (base/'claude-desktop.rl').touch()
            (base/'codex.rl').touch()
            (base/'codex.rl.lock').touch()
            sources=Sources(base/'capture.rl',directory=base)
            self.assertEqual([s['name'] for s in sources.public()],['Tap capture','claude-desktop.rl','codex.rl'])
            source=next(s for s in sources.public() if s['name']=='codex.rl')
            with patch.object(sources,'local',return_value={'columns':[],'rows':[]}) as query:
                sources.query(source['id'],'RETURN 1',{})
                self.assertEqual(query.call_args.args[0]['path'],str(base/'codex.rl'))
            (base/'codex.rl').unlink()
            with self.assertRaises(ValueError):
                sources.query(source['id'],'RETURN 1',{})

    def test_neo4j_values_keep_element_ids_for_graph_view(self):
        from neo4j.graph import Graph, Node, Path as GraphPath
        graph=Graph()
        a=Node(graph,'node:a',1,['Account'],{'name':'A'})
        b=Node(graph,'node:b',2,['Account'],{'name':'B'})
        rel=graph.relationship_type('TRANSFERRED')(graph,'rel:1',3,{'amount':12})
        rel._start_node,rel._end_node=a,b
        result=wire({'path':GraphPath(a,rel),'nested':[a]})
        self.assertEqual(result['path']['relationships'][0]['start_node'],'node:a')
        self.assertEqual(result['path']['relationships'][0]['end_node'],'node:b')
        self.assertEqual(result['nested'][0]['elementId'],'node:a')
        self.assertEqual(result['path']['relationships'][0]['properties']['amount'],12)


if __name__=='__main__':
    unittest.main()
