"""Manual Aura acceptance: bind legacy sessions and replicate lifecycle edges.

Runs against configured AURA_* credentials with the branch's Python sources and
Relate CLI available. Creates only UUID-scoped synthetic history, pushes with
the production skip-existing policy, verifies committed reads, then removes its
own exact node IDs/project keys. No embeddings, user-history migration or paid
provider calls. Excluded from automatic unit tests.
"""
import json
import os
from pathlib import Path
import tempfile
import uuid

from aura_sync import AuraSync
from database_lock import DatabaseLock
from memory_graph import EXCHANGE, direct_query, exchange
from relate_tap.coding_recall import CodingRecall
from relate_tap.project_identity import bind_sessions


def main():
    from neo4j import GraphDatabase
    token=uuid.uuid4().hex
    legacy='lifecycle-smoke-'+token
    canonical='git:smoke.invalid/lifecycle/'+token
    projects=[legacy,canonical]
    ids=set()
    steps=[]
    database=os.environ.get('AURA_DATABASE') or 'neo4j'
    with GraphDatabase.driver(os.environ['AURA_URI'],
            auth=(os.environ['AURA_USERNAME'],os.environ['AURA_PASSWORD']),
            connection_timeout=10,connection_acquisition_timeout=15) as driver:
        driver.verify_connectivity()
        def remote(q,**p):
            with driver.session(database=database) as session:
                with session.begin_transaction(timeout=30) as tx:
                    rows=tx.run(q,**p).data()
                    tx.commit()
                    return rows
        try:
            with tempfile.TemporaryDirectory(prefix='relate-lifecycle-smoke-') as tmp:
                root=Path(tmp);db=root/'synthetic.rl'
                credentials=root/'credentials.env'
                fd=os.open(credentials,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
                with os.fdopen(fd,'w') as output:
                    for name in ('URI','USERNAME','PASSWORD','DATABASE'):
                        value=database if name=='DATABASE' else os.environ['AURA_'+name]
                        if '\r' in value or '\n' in value:
                            raise ValueError('Invalid connection value')
                        output.write('NEO4J_'+name+'='+value+'\n')
                lock=DatabaseLock(db)
                def local(q,p,**kw):
                    with lock:return direct_query(db,q,p,**kw)
                def record(project,turn,**args):
                    payload=exchange(project,'codex',{'session':token,'turn':turn,'context_kind':'Code',
                        'prompt':'Synthetic lifecycle acceptance','response':'Synthetic evidence',**args})
                    ids.update([payload['key'],payload['turn_key'],payload['reasoning']['id']])
                    for key in ('messages','memories','memory_events','entities','tools'):
                        ids.update(row['id'] for row in payload[key])
                    rows=local(EXCHANGE,payload,read_only=False)
                    assert rows and rows[0]['next_links']>0,'Local capture failed'
                    return payload
                # No background thread and no writes to the live capture spool.
                sync=AuraSync.__new__(AuraSync)
                sync.database=db;sync.credentials=credentials;sync.graph_lock=lock
                sync.status={'state':'pending'};sync.receipt={}
                sync.publish=lambda:None
                original=record(legacy,'initial',memories=[{'kind':'open_item',
                    'subject':'Synthetic work','body':'Original evidence must survive'}])
                old=original['memories'][0]['id']
                fingerprint=sync.push(None)
                assert remote('MATCH(m:ContextMemory {id:$id}) RETURN m.body AS body',id=old)==[
                    {'body':'Original evidence must survive'}]
                steps.append('original_memory_committed')

                bind_sessions(local,legacy,canonical,[original['key']],apply=True)
                fingerprint=sync.push(fingerprint)
                bound=remote('''MATCH(p:ContextProject:Code {name:$project})-[:HAS_SESSION]->
                    (s:ContextSession:Code {id:$id}) RETURN count(s) AS n''',
                    project=canonical,id=original['key'])
                assert bound==[{'n':1}], 'Binding edge did not reach existing session'
                steps.append('binding_to_existing_session_committed')

                replacement=record(canonical,'supersede',memories=[{'kind':'decision',
                    'subject':'Synthetic replacement','body':'Use replacement evidence','supersedes':old}])
                fingerprint=sync.push(fingerprint)
                edge=remote('''MATCH(n:ContextMemory {id:$new})-[:SUPERSEDES]->(m:ContextMemory {id:$old})
                    MATCH(m)-[:HAS_STATUS_EVENT]->(e:ContextMemoryEvent {id:$event})
                    MATCH(e)-[:RECORDED_IN]->(t:ContextTurn {id:$turn})
                    RETURN count(e) AS n,m.body AS original''',new=replacement['memories'][0]['id'],
                    old=old,event=replacement['memory_events'][0]['id'],turn=replacement['turn_key'])
                assert edge==[{'n':1,'original':'Original evidence must survive'}], 'Lifecycle edges missing'
                recall=CodingRecall(remote)
                memories=recall.request('recall',{'project':canonical})['memories']
                assert next(m for m in memories if m['id']==old)['status']=='superseded'
                steps.append('supersession_edges_and_recall_committed')

                record(canonical,'reopen',memory_events=[{'memory_id':old,'kind':'reopened',
                    'reason':'Synthetic reopening verification'}])
                sync.push(fingerprint)
                memories=recall.request('recall',{'project':canonical})['memories']
                assert next(m for m in memories if m['id']==old)['status']=='reopened'
                assert remote('''MATCH(m:ContextMemory {id:$id})-[:HAS_STATUS_EVENT]->(e)
                    RETURN count(e) AS n''',id=old)==[{'n':2}]
                steps.append('new_event_on_existing_memory_committed')
        finally:
            # Exact UUID-derived IDs only; never delete by general Context labels.
            remote('''MATCH(n) WHERE n.id IN $ids OR (n:ContextProject AND n.name IN $projects)
                DETACH DELETE n''',ids=sorted(ids),projects=projects)
            remaining=remote('''MATCH(n) WHERE n.id IN $ids OR (n:ContextProject AND n.name IN $projects)
                RETURN count(n) AS n''',ids=sorted(ids),projects=projects)
            assert remaining==[{'n':0}],'Synthetic cleanup incomplete'
    print(json.dumps({'status':'passed','steps':steps,'synthetic_cleanup_verified':True,
                      'embeddings':0,'user_history_modified':False}))


if __name__=='__main__':
    try:main()
    except Exception as exc:
        # Relate/driver diagnostics can contain credentials; report only the type.
        print(json.dumps({'status':'failed','error':type(exc).__name__}))
        raise SystemExit(1)
