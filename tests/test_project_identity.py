import subprocess
import tempfile
import unittest
from pathlib import Path
from relate_tap.bootstrap import discover_project
from unittest.mock import patch
import memory_mcp

class ProjectIdentityTest(unittest.TestCase):
    def test_origin_is_authoritative_for_renamed_checkouts_and_worktrees(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)/'different-local-name'
            repo.mkdir()
            def git(*args):
                return subprocess.run(['git', '-C', str(repo), *args], check=True, capture_output=True)
            git('init')
            for url in ['git@github.com:neo4j/neo4j-semantic-robot.git', 'https://github.com/neo4j/neo4j-semantic-robot.git']:
                git('config', 'remote.origin.url', url)
                self.assertEqual(discover_project(repo), 'neo4j-semantic-robot')
            git('-c','user.name=Test','-c','user.email=test@example.com','commit','--allow-empty','-m','initial')
            worktree=Path(tmp)/'feature-checkout'
            git('worktree','add','-b','feature',str(worktree))
            self.assertEqual(discover_project(worktree), 'neo4j-semantic-robot')
            git('remote','remove','origin')
            self.assertEqual(discover_project(worktree), 'different-local-name')

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
