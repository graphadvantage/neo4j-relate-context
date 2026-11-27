import subprocess
import tempfile
import unittest
from pathlib import Path
from relate_tap.bootstrap import discover_project
from relate_tap.project_identity import origin_key, local_key, bind_sessions
from memory_graph import direct_query, EXCHANGE, exchange
from unittest.mock import patch
import memory_mcp

class ProjectIdentityTest(unittest.TestCase):
    def test_transport_variants_credentials_and_case_share_one_identity(self):
        variants = ['git@github.com:Team/Backend.git', 'https://github.com/team/backend.git/',
                    'ssh://git@GITHUB.COM:22/team/backend.git',
                    'https://user:password@github.com:443/team/backend.git?token=private#ignored']
        self.assertEqual({origin_key(v) for v in variants}, {'git:github.com/team/backend'})
        self.assertNotEqual(origin_key('https://github.com/a/backend'), origin_key('https://github.com/b/backend'))
        self.assertNotEqual(origin_key('https://github.com/a/backend'), origin_key('https://gitlab.com/a/backend'))
        self.assertEqual(origin_key('git@gitlab.com:group/subgroup/Backend.git'),
                         'git:gitlab.com/group/subgroup/Backend')
        self.assertNotEqual(origin_key('ssh://git@host:2222/team/repo'), origin_key('ssh://git@host/team/repo'))
        for origin in ['https://host/a/../repo', 'https://host/a/%2E%2E/repo', 'https://host/a/']:
            # A trailing slash is allowed; traversal is not.
            if '..' in origin or '%2E' in origin:
                with self.assertRaises(ValueError): origin_key(origin)

    def test_local_repository_identity_does_not_collapse_equal_directory_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            keys=[]
            for owner in ['one','two']:
                path=Path(tmp)/owner/'backend'
                path.mkdir(parents=True)
                subprocess.run(['git','init',str(path)],check=True,capture_output=True)
                keys.append(discover_project(path))
            self.assertNotEqual(*keys)
            self.assertTrue(all(k.startswith('git-local:') and tmp not in k for k in keys))
            self.assertEqual(origin_key('file://'+str(path)), origin_key(str(path)))

    def test_binding_is_explicit_additive_idempotent_and_rejects_ambiguous_sessions(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=Path(tmp)/'test.rl'
            def run(q,p,**kw): return direct_query(db,q,p,**kw)
            code=exchange('backend','codex',{'session':'old','turn':'one','prompt':'Old work',
                'response':'Old evidence','context_kind':'Code'})
            chat=exchange('backend','claude-desktop',{'session':'chat','turn':'one','prompt':'Chat',
                'response':'Chat evidence','context_kind':'Chat'})
            run(EXCHANGE,code,read_only=False);run(EXCHANGE,chat,read_only=False)
            project=origin_key('git@github.com:team/backend.git')
            plan=bind_sessions(run,'backend',project,[code['key']])
            self.assertFalse(plan['apply'])
            self.assertEqual(run('MATCH(p:ContextProject {name:$input.project}) RETURN count(p) AS n',{'project':project}),[{'n':0}])
            for invalid in [chat['key'], 'missing']:
                with self.assertRaises(ValueError):bind_sessions(run,'backend',project,[code['key'],invalid],True)
            for _ in range(2):
                self.assertEqual(bind_sessions(run,'backend',project,[code['key']],True)['bound'],1)
            rows=run('MATCH(p:ContextProject {name:$input.project})-[:HAS_SESSION]->(s) RETURN s.id AS id',{'project':project})
            self.assertEqual(rows,[{'id':code['key']}])
            self.assertEqual(run('MATCH(m:ContextMessage) RETURN count(m) AS n',{}),[{'n':4}])
            self.assertEqual(run('MATCH(p:ContextProject {name:$input.project})-[:HAS_SESSION]->(s) RETURN count(s) AS n',{'project':'backend'}),[{'n':2}])

    def test_same_basename_repositories_have_isolated_recall(self):
        from relate_tap.coding_recall import CodingRecall
        with tempfile.TemporaryDirectory() as tmp:
            db=Path(tmp)/'test.rl'
            for owner in ['one','two']:
                p=origin_key('https://github.com/'+owner+'/backend.git')
                data=exchange(p,'codex',{'session':'shared-session','turn':'same-turn','context_kind':'Code',
                    'prompt':'Architecture decisions','response':owner})
                direct_query(db,EXCHANGE,data,read_only=False)
            recall=CodingRecall(lambda q,**p: direct_query(db,q.replace('$','$input.'),p))
            result=recall.request('recall',{'project':'git:github.com/one/backend'})
            self.assertEqual([m['response'] for m in result['matches']],['one'])
            turn=result['matches'][0]['turn_id']
            other=recall.request('evidence',{'project':'git:github.com/two/backend','turn_id':turn})
            self.assertEqual(other['evidence'],[])

    def test_origin_is_authoritative_for_renamed_checkouts_and_worktrees(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)/'different-local-name'
            repo.mkdir()
            def git(*args):
                return subprocess.run(['git', '-C', str(repo), *args], check=True, capture_output=True)
            git('init')
            for url in ['git@github.com:neo4j/neo4j-semantic-robot.git', 'https://github.com/neo4j/neo4j-semantic-robot.git']:
                git('config', 'remote.origin.url', url)
                self.assertEqual(discover_project(repo), 'git:github.com/neo4j/neo4j-semantic-robot')
            git('-c','user.name=Test','-c','user.email=test@example.com','commit','--allow-empty','-m','initial')
            worktree=Path(tmp)/'feature-checkout'
            git('worktree','add','-b','feature',str(worktree))
            self.assertEqual(discover_project(worktree), 'git:github.com/neo4j/neo4j-semantic-robot')
            git('remote','remove','origin')
            self.assertEqual(discover_project(worktree), local_key(repo/'.git'))
            self.assertEqual(discover_project(repo), discover_project(worktree))

    def test_memory_server_defaults_project_and_preserves_agent_attribution(self):
        calls=[]
        def run(statement, parameters, **kwargs):
            calls.append(parameters)
            return []
        result=memory_mcp.respond(run,None,{'method':'tools/list'},'claude-code','neo4j-semantic-robot')
        self.assertNotIn('project',result['tools'][0]['inputSchema']['required'])
        with patch('relate_tap.recall_client.request',return_value={'state':'ready'}) as request:
            memory_mcp.call(run,None,'recall_context',{},'claude-code','neo4j-semantic-robot')
        self.assertEqual(request.call_args.args[2],'neo4j-semantic-robot')
        self.assertEqual(request.call_args.kwargs['agent'],'claude-code')
        self.assertEqual(request.call_args.args[1]['context_kind'],'Code')

if __name__ == '__main__': unittest.main()
