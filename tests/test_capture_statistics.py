import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from memory_graph import direct_query
from relate_tap.store import CaptureStore


class CaptureStatisticsTest(unittest.TestCase):
    def test_durable_counts_survive_replay_small_window_restart_and_reset(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            store=CaptureStore(root/'capture.rl',root,root/'offsets.json',window=1)
            events=[]
            for index in range(3):
                event={'id':str(index),'kind':'request','timestamp':'2026-11-27T18:00:00+00:00',
                       'connection':'connection','server':'risk','host':'laptop','username':'user',
                       'agent':'claude-desktop','project':'risk','call_id':'call-'+str(index),
                       'cypher':'RETURN 1' if index<2 else None}
                events.append(event)
                store.persist(event)
            store.persist(events[0])
            totals=store.statistics()['groups']
            self.assertEqual(totals,[{'agent':'claude-desktop','project':'risk','requests':3,'cyphers':2}])
            self.assertEqual(len(store.snapshot()['events']),1)
            reopened=CaptureStore(store.database,root,store.state,window=1)
            self.assertEqual(reopened.statistics()['groups'],totals)
            direct_query(store.database,"CREATE(:ContextQuery {id:'reported-copy',cypher:'RETURN 1'})",{},read_only=False)
            with patch('relate_tap.store.time.monotonic',return_value=reopened.stats_at+6):
                self.assertEqual(reopened.statistics()['groups'],totals)
            direct_query(store.database,'MATCH(n) DETACH DELETE n',{},read_only=False)
            with patch('relate_tap.store.time.monotonic',return_value=reopened.stats_at+6):
                self.assertEqual(reopened.statistics()['groups'],[])


if __name__=='__main__':
    unittest.main()
