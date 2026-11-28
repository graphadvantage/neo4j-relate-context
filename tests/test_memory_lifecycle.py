from pathlib import Path
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from aura_sync import KEYS
from memory_graph import EXCHANGE, direct_query, exchange
from relate_tap.bootstrap import discover_project
from relate_tap.coding_recall import CodingRecall
from relate_tap.git_state import capture_git


class GitStateTest(unittest.TestCase):
    def test_native_stdio_capture_advertises_identity_and_returns_observed_git(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);repo=root/'checkout';repo.mkdir()
            def git(*args):return subprocess.run(['git','-C',str(repo),*args],check=True,capture_output=True)
            git('init');git('remote','add','origin','git@github.com:team/backend.git')
            git('-c','user.name=Test','-c','user.email=test@example.com','commit','--allow-empty','-m','initial')
            requests=[{'id':1,'method':'initialize','params':{}},
                {'id':2,'method':'tools/call','params':{'name':'record_turn','arguments':{
                    'session':'native','turn':'one','prompt':'Check Git','response':'Verified',
                    'memories':[{'kind':'decision','subject':'Identity','body':'Keep owners separate'}]}}}]
            command=[sys.executable,str(Path(__file__).resolve().parents[1]/'memory_mcp.py'),
                     '--agent','codex','--no-aura','--database',str(root/'native.rl')]
            result=subprocess.run(command,cwd=repo,input=''.join(json.dumps(r)+'\n' for r in requests),
                                  capture_output=True,text=True,timeout=15)
            self.assertEqual(result.returncode,0,result.stderr)
            replies=[json.loads(line)['result'] for line in result.stdout.splitlines()]
            self.assertIn('git:github.com/team/backend',replies[0]['instructions'])
            saved=json.loads(replies[1]['content'][0]['text'])
            self.assertEqual(saved['git']['git_source'],'server_observed')
            self.assertFalse(saved['git']['git_dirty'])
            self.assertEqual(len(saved['memory_ids']),1)
            self.assertEqual(direct_query(root/'native.rl',
                'MATCH(p:ContextProject) RETURN p.name AS project',{}),[{'project':'git:github.com/team/backend'}])

    def test_observed_clean_dirty_detached_and_wrong_repository(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo=Path(tmp)
            def git(*args):return subprocess.run(['git','-C',tmp,*args],check=True,capture_output=True).stdout.decode().strip()
            git('init');git('remote','add','origin','https://github.com/team/repo.git')
            git('-c','user.name=Test','-c','user.email=test@example.com','commit','--allow-empty','-m','initial')
            key=discover_project(repo)
            state=capture_git(key,'Code',{},repo)
            self.assertEqual(state['git_commit'],git('rev-parse','HEAD'))
            self.assertFalse(state['git_dirty'])
            self.assertEqual(state['git_source'],'server_observed')
            (repo/'changed').write_text('work')
            self.assertTrue(capture_git(key,'Code',{},repo)['git_dirty'])
            git('checkout','--detach')
            self.assertEqual(capture_git(key,'Code',{},repo)['git_branch'],'HEAD')
            for project,kind in [('git:github.com/other/repo','Code'),(key,'Chat')]:
                self.assertEqual(capture_git(project,kind,{},repo)['git_source'],'unavailable')

    def test_client_fields_are_validated_without_inventing_missing_fields(self):
        state=capture_git('repo','Code',{'git_commit':'a'*40})
        self.assertEqual(state['git_source'],'client_reported')
        self.assertIsNone(state['git_dirty'])
        for args in [{'git_commit':'main'}, {'git_dirty':'false'}, {'git_branch':'bad\nbranch'}]:
            with self.assertRaises(ValueError):capture_git('repo','Code',args)


class MemoryLifecycleTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.db=Path(self.tmp.name)/'test.rl'
        self.recall=CodingRecall(lambda q,**p: direct_query(self.db,q.replace('$','$input.'),p))

    def record(self, turn, project='repo', session='s', **args):
        data=exchange(project,'codex',{'session':session,'turn':turn,'prompt':'Observed work',
            'response':'Evidence for the change','context_kind':'Code',**args})
        rows=direct_query(self.db,EXCHANGE,data,read_only=False)
        return data,rows

    def test_resolve_reopen_and_supersede_preserve_evidence_across_sessions(self):
        original,_=self.record('one',memories=[{'kind':'open_item','subject':'Recall','body':'Implement recall'}])
        old=original['memories'][0]['id']
        resolved,rows=self.record('two',session='other',memory_events=[
            {'memory_id':old,'kind':'resolved','reason':'Implementation verified'}],git_commit='a'*40,git_dirty=True)
        self.assertTrue(rows)
        result=self.recall.request('recall',{'project':'repo'})
        memory=result['memories'][0]
        self.assertEqual(memory['status'],'resolved')
        self.assertEqual(memory['status_reason'],'Implementation verified')
        self.assertEqual(result['matches'][0]['freshness'],'working_tree_modified')
        # Retry changes no event IDs, evidence or timestamps.
        self.record('two',session='other',memory_events=[
            {'memory_id':old,'kind':'resolved','reason':'Implementation verified'}],git_commit='a'*40,git_dirty=True)
        self.assertEqual(direct_query(self.db,'MATCH(e:ContextMemoryEvent) RETURN count(e) AS n',{}),[{'n':1}])
        self.record('three',memory_events=[{'memory_id':old,'kind':'reopened','reason':'New regression'}])
        self.assertEqual(self.recall.request('recall',{'project':'repo'})['memories'][0]['status'],'reopened')
        replacement,_=self.record('four',memories=[{'kind':'decision','subject':'Recall v2','body':'Use the new design','supersedes':old}])
        result=self.recall.request('recall',{'project':'repo'})
        self.assertEqual([m['status'] for m in result['memories']],['open','superseded'])
        self.assertEqual(result['memories'][1]['replacement_id'],replacement['memories'][0]['id'])
        self.assertEqual(direct_query(self.db,'MATCH(n:ContextMemory)-[:SUPERSEDES]->(old) RETURN old.id AS id',{}),[{'id':old}])
        self.assertEqual(direct_query(self.db,'MATCH(m:ContextMessage) RETURN count(m) AS n',{}),[{'n':8}])
        self.assertIn('ContextMemoryEvent:id',KEYS)

    def test_cross_project_and_missing_targets_reject_without_partial_capture(self):
        original,_=self.record('one',memories=[{'kind':'decision','subject':'Design','body':'Original'}])
        old=original['memories'][0]['id']
        for target,project in [(old,'outside'),('absent','repo')]:
            _,rows=self.record('bad',project=project,memory_events=[
                {'memory_id':target,'kind':'resolved','reason':'Should not work'}])
            self.assertTrue(not rows or rows[0]['next_links']==0)
        self.assertEqual(direct_query(self.db,'MATCH(t:ContextTurn) RETURN count(t) AS n',{}),[{'n':1}])
        self.assertEqual(direct_query(self.db,'MATCH(e:ContextMemoryEvent) RETURN count(e) AS n',{}),[{'n':0}])

    def test_unreplicated_resolution_is_pending_even_when_memory_id_already_exists_in_aura(self):
        original,_=self.record('one',memories=[{'kind':'open_item','subject':'Work','body':'Pending work'}])
        remote=Path(self.tmp.name)/'remote.rl';shutil.copyfile(self.db,remote)
        old=original['memories'][0]['id']
        self.record('two',memory_events=[{'memory_id':old,'kind':'resolved','reason':'Verified locally'}])
        local=self.db
        class Sources:
            def public(self):return [{'id':'local','kind':'relate','name':'codex.rl'}]
            def query(self,source,q,p):return {'rows':direct_query(local,q.replace('$','$input.'),p)}
        recall=CodingRecall(lambda q,**p:direct_query(remote,q.replace('$','$input.'),p),Sources())
        result=recall.request('recall',{'project':'repo'})
        self.assertEqual(result['memories'][0]['status'],'open')
        pending=[r for r in result['pending_local'] if r['record_type']=='memories']
        self.assertEqual([m['status'] for m in pending],['resolved'])
        self.assertEqual(pending[0]['state'],'pending_replication')

    def test_missing_git_is_unknown_and_retry_keeps_first_server_anchor(self):
        state={'git_commit':'a'*40,'git_branch':'main','git_dirty':False,'git_source':'server_observed'}
        with patch('relate_tap.git_state.capture_git',return_value=state):
            self.record('one')
        with patch('relate_tap.git_state.capture_git',return_value={**state,'git_commit':'b'*40}):
            self.record('one')
        r=self.recall.request('recall',{'project':'repo'})
        self.assertEqual(r['matches'][0]['git_commit'],'a'*40)
        self.assertEqual(r['sessions'][0]['git_commit'],'a'*40)
        self.record('two')
        r=self.recall.request('recall',{'project':'repo'})
        self.assertEqual(r['matches'][0]['freshness'],'unknown')
        # Session is derived from its latest turn, not a stale roll-up property.
        self.assertIsNone(r['sessions'][0]['git_commit'])
        self.assertEqual(r['sessions'][0]['git_source'],'unavailable')


if __name__=='__main__':unittest.main()
