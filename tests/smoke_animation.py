"""Interactive, isolated visual check. Commands: wam, risk, push, done, error, recover, idle, quit.

Uses synthetic tool messages and simulated push receipts in a temporary graph.
Never connects to Aura or changes the production console/spool.
"""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, timezone

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from relate_tap.bootstrap import atomic_json
from relate_tap.tap import Recorder


def main():
    image=sys.argv[1] if len(sys.argv)>1 else 'relate-tap:local'
    with tempfile.TemporaryDirectory(prefix='tap-animation-fixture-') as folder:
        base=Path(folder); spool=base/'spool'; spool.mkdir(); (base/'databases').mkdir()
        os.environ.update(RELATE_TAP_HOME=str(base/'tap'),RELATE_TAP_DATABASE_DIR=str(base/'databases'),
            RELATE_TAP_AURA_CREDENTIALS='',RELATE_TAP_WRITER='native',RELATE_TAP_SPOOL=str(spool))
        recorders={name:Recorder(spool,'fixture-'+name,project='Animation verification') for name in ('wam','risk')}
        for recorder in recorders.values():
            recorder.emit('connected')
            recorder.observe(json.dumps({'id':0,'method':'initialize','params':{'clientInfo':{'name':'claude-ai','version':'fixture'}}}).encode(),'request')
        name='relate-animation-'+uuid.uuid4().hex[:8]
        config=Path(__file__).resolve().parents[1]/'docker/sources.json'
        subprocess.run(['docker','run','-d','--rm','--init','--name',name,'--entrypoint','relate-console',
            '-p','127.0.0.1:18081:8080','-e','RELATE_CONSOLE_SOURCE_DIR=/sources','-e','RELATE_CONSOLE_SOURCES=/config/sources.json',
            '--mount',f'type=bind,source={spool},target=/spool,readonly',
            '--mount',f'type=bind,source={base/"databases"},target=/sources,readonly',
            '--mount',f'type=bind,source={config},target=/config/sources.json,readonly',image],check=True,capture_output=True)
        print('Isolated fixture at http://localhost:18081. Commands: wam, risk, push, done, error, recover, idle, quit.',flush=True)
        push_id=None; serial=1
        try:
            for line in sys.stdin:
                command=line.strip()
                if command=='quit': break
                if command in recorders:
                    recorders[command].observe(json.dumps({'id':serial,'method':'tools/call','params':{'name':'read-cypher','arguments':{'query':'RETURN 1 AS fixture'}}}).encode(),'request')
                    serial+=1
                elif command=='idle':
                    for recorder in recorders.values():
                        with recorder.lock:
                            pending=list(recorder.pending)
                        for request in pending:
                            recorder.observe(json.dumps({'id':json.loads(request),'result':{'content':[]}}).encode(),'response')
                elif command=='error':
                    (spool/'fixture-error.jsonl').write_text('invalid fixture JSON\n')
                elif command=='recover':
                    (spool/'fixture-error.jsonl').unlink(missing_ok=True)
                elif command in {'push','done'}:
                    push_id=push_id or str(uuid.uuid4())
                    receipt={'agent':'claude-desktop','database':'claude-desktop.rl','push_id':push_id,
                        'state':'pushing' if command=='push' else 'synced'}
                    receipt['started_at' if command=='push' else 'last_pushed_at']=datetime.now(timezone.utc).isoformat()
                    atomic_json(spool/'claude-desktop.push.json',receipt)
                print('Fixture action:',command,flush=True)
        finally:
            for recorder in recorders.values(): recorder.close(0)
            subprocess.run(['docker','stop',name],capture_output=True)
            for path in spool.glob('*.status.json'):
                pid=json.loads(path.read_text()).get('worker_pid')
                if pid:
                    try: os.kill(pid,signal.SIGTERM)
                    except ProcessLookupError: pass


if __name__=='__main__': main()
