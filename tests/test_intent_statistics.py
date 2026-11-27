import unittest
from unittest.mock import patch

from relate_tap.intent_recall import IntentRecall


class IntentStatisticsTest(unittest.TestCase):
    def test_background_count_exposes_origin_groups_and_visible_unavailability(self):
        recall=IntentRecall()
        recall.enabled=lambda:True
        groups=[{'project':'repo','agents':['codex','claude-code'],'intents':2}]
        with patch.object(recall,'run',return_value=groups):
            self.assertEqual(recall.refresh_intent_statistics(),{'state':'ready','groups':groups})
        with patch.object(recall,'run',side_effect=RuntimeError('private connection details')):
            unavailable=recall.refresh_intent_statistics()
            self.assertEqual(unavailable['state'],'unavailable')
            self.assertEqual(unavailable['groups'],[])
            self.assertNotIn('private',str(unavailable))
        recall.enabled=lambda:False
        with patch.object(recall,'run') as run:
            self.assertEqual(recall.refresh_intent_statistics()['state'],'disabled')
            run.assert_not_called()


if __name__=='__main__':
    unittest.main()
