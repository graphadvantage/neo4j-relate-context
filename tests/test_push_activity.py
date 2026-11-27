from contextlib import nullcontext
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from aura_sync import AuraSync


class PushActivityTest(unittest.TestCase):
    def test_actual_push_publishes_activity_and_unchanged_poll_does_not(self):
        with tempfile.TemporaryDirectory() as folder:
            base=Path(folder)
            sync=AuraSync.__new__(AuraSync)
            sync.database=base/'codex.rl'; sync.database.write_bytes(b'fixture database')
            sync.credentials=base/'credentials.env'; sync.credentials.write_text('fixture')
            sync.credentials.chmod(0o600)
            sync.graph_lock=nullcontext(); sync.status={'state':'pending'}; sync.receipt={}
            observed=[]
            def command(args,**kwargs):
                if 'push' in args:
                    receipt=json.loads((base/'spool/codex.push.json').read_text())
                    observed.append(receipt['state'])
                return type('Result',(),{'stdout':json.dumps({'entities':[]})})()
            with patch.dict(os.environ,{'RELATE_TAP_SPOOL':str(base/'spool')}), patch('aura_sync.subprocess.run',side_effect=command) as run:
                fingerprint=sync._push(None)
                self.assertEqual(observed,['pushing'])
                receipt=json.loads((base/'spool/codex.push.json').read_text())
                self.assertEqual(receipt['state'],'synced')
                self.assertIn('last_pushed_at',receipt)
                push_id=receipt['push_id']; calls=run.call_count
                self.assertEqual(fingerprint,hashlib.sha256(b'fixture database').hexdigest())
                self.assertEqual(sync._push(fingerprint),fingerprint)
                self.assertEqual(run.call_count,calls)
                self.assertEqual(json.loads((base/'spool/codex.push.json').read_text())['push_id'],push_id)


if __name__=='__main__': unittest.main()
