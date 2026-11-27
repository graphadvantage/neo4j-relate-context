import unittest
from unittest.mock import patch

from memory_mcp import call
from relate_tap.bootstrap import discover_project


class MemoryRoutingTest(unittest.TestCase):
    def test_new_agent_uses_repository_provenance_without_an_allowlist(self):
        project=discover_project()
        with patch('relate_tap.recall_client.request',return_value={}) as forward:
            call(None,None,'recall_context',{},'future-agent',project)
        self.assertEqual(forward.call_args.args[1]['context_kind'],'Code')
        self.assertEqual(forward.call_args.kwargs['agent'],'future-agent')

    def test_stored_code_provenance_routes_outside_checkout(self):
        def run(q,p,**kw):return [{'labels':['ContextProject','Code']}]
        with patch('memory_mcp.context_kind',return_value=None), patch('relate_tap.recall_client.request',return_value={}) as forward:
            call(run,None,'recall_context',{},'future-agent','known-project')
        self.assertEqual(forward.call_args.args[1]['context_kind'],'Code')

    def test_unknown_agent_and_mixed_projects_do_not_guess_code(self):
        for labels in [[],[{'labels':['ContextProject','Code','Chat']}]]:
            with patch('memory_mcp.context_kind',return_value=None), patch('relate_tap.recall_client.request') as forward:
                r=call(lambda *a,**k:labels,None,'recall_context',{},'future-agent','unrelated')
            forward.assert_not_called()
            self.assertIn('notes',r)

    def test_explicit_chat_and_domain_source_override_repository_default(self):
        project=discover_project()
        with patch('relate_tap.recall_client.request',return_value={}) as forward:
            call(None,None,'recall_context',{'query':'ownership','mcp_source_id':'risk'},'future-agent',project)
        self.assertNotIn('context_kind',forward.call_args.args[1])
        with patch('relate_tap.recall_client.request') as forward:
            result=call(lambda *a,**k:[],None,'recall_context',{'context_kind':'Chat'},'codex',project)
        forward.assert_not_called()
        self.assertIn('notes',result)

    def test_desktop_remains_chat_and_explicit_code_is_supported(self):
        project=discover_project()
        with patch('relate_tap.recall_client.request') as forward:
            r=call(lambda *a,**k:[],None,'recall_context',{},'claude-desktop',project)
        forward.assert_not_called()
        self.assertIn('notes',r)
        with patch('relate_tap.recall_client.request',return_value={}) as forward:
            call(None,None,'recall_context',{'context_kind':'Code'},'claude-desktop',project)
        self.assertEqual(forward.call_args.args[1]['context_kind'],'Code')


if __name__=='__main__':unittest.main()
