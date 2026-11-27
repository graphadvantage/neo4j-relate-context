"""Native memory tools for automatically installed Claude Code/Codex skills."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from database_lock import DatabaseLock
from memory_graph import ENTITIES, EXCHANGE, LOAD, RECALL, clean, direct_query, exchange, save_note
from .bootstrap import atomic_json, canonical_agent, database_for, settings, start_worker, tap_home


def execute(agent, project, operation, payload=None, limit=5):
    agent = canonical_agent(agent)
    project = clean(project,512,'project')
    if operation == 'flush':
        if not (os.environ.get('RELATE_TAP_AURA_CREDENTIALS') or settings().get('aura_credentials')):
            return {'queued': False, 'state': 'disabled'}
        spool = Path(os.environ.get('RELATE_TAP_SPOOL', str(tap_home()/'spool')))
        spool.mkdir(parents=True,exist_ok=True)
        start_worker(agent,spool)
        atomic_json(spool/(agent+'.flush.json'), {'requested_at':time.time()})
        return {'queued': True}
    database = database_for(agent)
    database.parent.mkdir(parents=True,exist_ok=True)
    lock = DatabaseLock(database)
    with lock:
        if not database.exists():
            direct_query(database,'RETURN 1 AS initialized',{},read_only=False)
    def run(statement, parameters, *, read_only=True):
        with lock:
            return direct_query(database, statement, parameters, read_only=read_only)
    def saved(result):
        spool=Path(os.environ.get('RELATE_TAP_SPOOL',str(tap_home()/'spool')))
        try:
            spool.mkdir(parents=True,exist_ok=True)
            start_worker(agent,spool)
        except OSError:
            result['replication']='worker_unavailable'
        return result
    if operation == 'recall':
        params={'project':project,'limit':max(1,min(limit,50))}
        return {'notes':run(LOAD,params),'memories':run(RECALL,params),'entities':run(ENTITIES,params)}
    if operation == 'save':
        return saved({'notes':save_note(run,project,agent,payload)})
    if operation == 'record-turn':
        data=exchange(project,agent,payload)
        rows=run(EXCHANGE,data,read_only=False)
        if not rows or rows[0]['next_links']==0:
            raise ValueError('Turn was not saved; check for a reused turn ID')
        return saved({'session':data['session'],'turn':payload['turn']})
    raise ValueError('Unknown memory operation')


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--agent',required=True,type=canonical_agent)
    parser.add_argument('--project',required=True)
    parser.add_argument('--limit',type=int,default=5)
    parser.add_argument('operation',choices=['recall','save','record-turn','flush'])
    args=parser.parse_args(argv)
    try:
        payload=json.load(sys.stdin) if args.operation in {'save','record-turn'} else None
        result=execute(args.agent,args.project,args.operation,payload,args.limit)
        print(json.dumps(result))
    except (OSError,ValueError,KeyError,TypeError,subprocess.SubprocessError) as exc:
        print('Memory operation failed ('+type(exc).__name__+')',file=sys.stderr)
        raise SystemExit(1)
