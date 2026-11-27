import tempfile
import unittest
import subprocess
from pathlib import Path
from unittest.mock import patch

from memory_graph import EXCHANGE, direct_query, exchange, save_note
from relate_tap.bootstrap import context_kind
from relate_tap.project_labels import backfill
from relate_tap.store import CaptureStore
from relate_tap.tap import Recorder


class ContextLabelsTest(unittest.TestCase):
    def test_repository_evidence_and_explicit_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo=Path(tmp)
            self.assertIsNone(context_kind(repo.name, 'codex', directory=repo))
            subprocess.run(['git','init',str(repo)],check=True,capture_output=True)
            subprocess.run(['git','-C',str(repo),'remote','add','origin','git@github.com:team/shared.git'],check=True)
            self.assertEqual(context_kind('git:github.com/team/shared','codex',directory=repo),'Code')
            self.assertEqual(context_kind('git:github.com/team/shared','claude-code',directory=repo),'Code')
            self.assertIsNone(context_kind('another-project','codex',directory=repo))
            self.assertEqual(context_kind('git:github.com/team/shared','claude-desktop',directory=repo),'Chat')
            self.assertEqual(context_kind('git:github.com/team/shared','claude-desktop','Code',directory=repo),'Code')
        with self.assertRaises(ValueError):context_kind('repo','codex','anything')

    def test_project_and_session_labels_preserve_graph_and_allow_mixed_projects(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=Path(tmp)/'test.rl'
            def run(q,p,**kw):return direct_query(db,q,p,**kw)
            for kind,agent in [('Code','codex'),('Code','claude-code'),('Chat','claude-desktop')]:
                data=exchange('shared',agent,{'session':agent,'turn':'t','prompt':'Work',
                    'response':'Done','context_kind':kind})
                run(EXCHANGE,data,read_only=False)
                run(EXCHANGE,data,read_only=False)
            rows=run('MATCH(p:ContextProject)-[:HAS_SESSION]->(s:ContextSession) RETURN labels(p) AS project, labels(s) AS session, s.source AS agent',{})
            self.assertEqual(len(rows),3)
            for row in rows:
                self.assertTrue({'Code','Chat'} <= set(row['project']))
                kind='Chat' if row['agent']=='claude-desktop' else 'Code'
                self.assertIn(kind,row['session'])
                self.assertNotIn('Code' if kind=='Chat' else 'Chat',row['session'])
            self.assertEqual(run('MATCH(m:ContextMessage) RETURN count(m) AS n',{}),[{'n':6}])
            save_note(run,'notes','claude-desktop',{'title':'Note','body':'Body'})
            self.assertEqual(run("MATCH(p:ContextProject {name:'notes'}) RETURN labels(p) AS labels",{})[0]['labels'],['ContextProject','Chat'])

    def test_tap_labels_are_transported_without_reclassifying_legacy_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            with patch('relate_tap.tap.start_worker'),patch('relate_tap.tap.install_skill',return_value={}):
                recorder=Recorder(root,'server','codex','shared','Code')
                self.assertEqual(recorder.route['context_kind'],'Code')
                recorder.emit('connected');recorder.close(0)
            store=CaptureStore(root/'test.rl',root,root/'offsets.json')
            store.poll()
            self.assertEqual(direct_query(store.database,'MATCH(p:ContextProject) RETURN labels(p) AS labels',{})[0]['labels'],['ContextProject','Code'])

    def test_backfill_is_additive_idempotent_and_preserves_ambiguous_sessions(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=Path(tmp)/'test.rl'
            def run(q,p,**kw):return direct_query(db,q,p,**kw)
            run('''UNWIND ['repo','chat','mixed','unknown'] AS name
                CREATE(p:ContextProject {name:name})-[:HAS_SESSION]->(:ContextSession {id:name})''',{},read_only=False)
            for _ in range(2):backfill(run,['repo','mixed'],['chat','mixed'])
            rows=run('MATCH(p:ContextProject)-[:HAS_SESSION]->(s) RETURN p.name AS name,labels(p) AS p,labels(s) AS s',{})
            by_name={r['name']:r for r in rows}
            self.assertIn('Code',by_name['repo']['s'])
            self.assertIn('Chat',by_name['chat']['s'])
            self.assertEqual(by_name['mixed']['s'],['ContextSession'])
            self.assertEqual(by_name['unknown']['p'],['ContextProject'])
