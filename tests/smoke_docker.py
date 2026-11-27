"""Manual end-to-end check: python tests/smoke_docker.py [image].

Uses disposable bind mounts and a separate container; never touches real captures
or Aura. Run after docker build -t relate-tap:local .
"""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import uuid


def docker(*args, **kwargs):
    return subprocess.run(['docker', *args], check=True, capture_output=True, timeout=40, **kwargs).stdout


def main():
    image = sys.argv[1] if len(sys.argv) > 1 else 'relate-tap:local'
    with tempfile.TemporaryDirectory(prefix='relate-docker-smoke-') as folder:
        base = Path(folder); spool = base/'spool'; data = base/'data'
        spool.mkdir(); data.mkdir()
        server = base/'server.js'
        server.write_text("const rl=require('readline').createInterface({input:process.stdin}); rl.on('line',line=>{const m=JSON.parse(line); console.log(JSON.stringify({jsonrpc:'2.0',id:m.id,result:{structuredContent:{rows:[{ok:true}]},content:[]}}));});")
        query = 'MATCH (account:Account)\nWHERE account.risk > $risk RETURN account LIMIT 10'
        request = {'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':'read-cypher','arguments':{'query':query,'parameters':{'risk':0.8}}}}
        initialize = {'jsonrpc':'2.0','id':0,'method':'initialize','params':{'clientInfo':{'name':'claude-ai','version':'test'}}}
        response = docker('run','--rm','-i','--init','--mount',f'type=bind,source={spool},target=/spool',
            '--mount',f'type=bind,source={server},target=/server.js,readonly',image,
            '--name','neo4j-fraud','--project','smoke-project','--','node','/server.js',
            input=(json.dumps(initialize)+'\n'+json.dumps(request)+'\n').encode())
        assert [json.loads(line)['id'] for line in response.splitlines()] == [0,1]
        name = 'relate-tap-smoke-' + uuid.uuid4().hex[:8]
        docker('run','-d','--rm','--init','--name',name,'--entrypoint','relate-console',
            '-e','RELATE_CONSOLE_FRONTEND=/app/frontend/dist',
            '--mount',f'type=bind,source={spool},target=/spool,readonly',
            '--mount',f'type=bind,source={data},target=/data',image)
        try:
            probe = '''import json,urllib.request
base='http://127.0.0.1:8080'
snapshot=json.load(urllib.request.urlopen(base+'/api/topology'))
assert len(snapshot['events'])==4, snapshot
assert not snapshot['errors'], snapshot['errors']
assert urllib.request.urlopen(base).status==200
sources=json.load(urllib.request.urlopen(base+'/api/sources'))
source=next(s['id'] for s in sources if s['name']=='claude-desktop.rl')
assert snapshot['routes'][0]['agent']=='claude-desktop'
assert snapshot['routes'][0]['database']=='claude-desktop.rl'
assert snapshot['routes'][0]['persisted_offset']>0
body=json.dumps({'source':source,'query':'MATCH(q:ContextQuery) RETURN q.cypher AS cypher, q.parameters AS params LIMIT 10'}).encode()
result=json.load(urllib.request.urlopen(urllib.request.Request(base+'/api/query',data=body,headers={'Content-Type':'application/json'})))
assert len(result['rows'])==1, result
print(json.dumps(result['rows'][0]))
'''
            def verify():
                last = None
                for _ in range(30):
                    try:
                        row=json.loads(docker('exec',name,'python3','-c',probe))
                        assert row['cypher']==query
                        assert json.loads(row['params'])=={'risk':0.8}
                        return
                    except subprocess.CalledProcessError as exc:
                        last=exc
                        time.sleep(0.3)
                raise AssertionError(last.stderr.decode())
            verify()
            docker('restart',name)
            verify()
            print('PASS: Docker client discovery → claude-desktop.rl → console registration; restart replay stays idempotent.')
        finally:
            docker('stop',name)


if __name__ == '__main__':
    main()
