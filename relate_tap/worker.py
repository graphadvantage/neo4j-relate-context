"""A host-native, detached spool consumer; one writer per agent database."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time

from database_lock import FileLock
from memory_graph import direct_query
from .bootstrap import atomic_json, canonical_agent, database_for, resolved_route, settings, tap_home
from .store import CaptureStore


def run(agent, spool, idle_seconds=120):
    database = database_for(agent)
    database.parent.mkdir(parents=True, exist_ok=True)
    # Native writers share this lock with the existing native memory MCP service.
    with FileLock(database.with_suffix('.worker.lock'), blocking=False):
        home = tap_home()
        state = home/'state'/f'{agent}.offsets.json'
        store = CaptureStore(database, spool, state, route_agent=agent)
        with store.graph_lock:
            if not database.exists():
                direct_query(database, 'RETURN 1 AS initialized', {}, read_only=False)
        sync = None
        credentials = os.environ.get('RELATE_TAP_AURA_CREDENTIALS') or settings().get('aura_credentials')
        if credentials:
            from aura_sync import AuraSync
            sync = AuraSync(database, Path(credentials), store.graph_lock)
        last_change, last_revision = time.monotonic(), -1
        last_flush = None
        while True:
            store.poll()
            flush = spool/(agent+'.flush.json')
            if sync and flush.exists() and flush.stat().st_mtime_ns != last_flush:
                last_flush = flush.stat().st_mtime_ns
                sync.flush()
            snapshot = store.snapshot()
            if snapshot['revision'] != last_revision:
                last_change, last_revision = time.monotonic(), snapshot['revision']
            live = False
            for path in spool.glob('*.route.json'):
                try:
                    route = resolved_route(json.loads(path.read_text()))
                    at = datetime.fromisoformat(route['last_seen']).timestamp()
                    if route.get('agent') == agent and route.get('connected') and time.time()-at < 20:
                        live = True
                except (OSError, ValueError, KeyError):
                    pass
            atomic_json(spool/(agent+'.status.json'), {
                'agent': agent, 'database': database.name, 'database_path': str(database),
                'worker_pid': os.getpid(),
                'state': 'retrying' if store.errors else 'ready', 'updated_at': datetime.now(timezone.utc).isoformat(),
                'last_persisted': store.events[-1]['timestamp'] if store.events else None,
                'offsets': dict(store.offsets), 'errors': store.errors,
                'commits': list(store.commits),
                'replication': dict(sync.status) if sync else {'state': 'disabled'},
            })
            if not live and time.monotonic()-last_change >= idle_seconds:
                break
            time.sleep(0.5)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--agent', type=canonical_agent, required=True)
    parser.add_argument('--spool', type=Path, required=True)
    args = parser.parse_args()
    try:
        run(args.agent, args.spool)
    except BlockingIOError:
        # Another connection already started the worker for this agent.
        pass
    except Exception as exc:
        atomic_json(args.spool/(args.agent+'.status.json'), {
            'agent': args.agent, 'database': args.agent+'.rl', 'state': 'error',
            'errors': [{'file': args.agent+'.rl', 'error': type(exc).__name__, 'offset': 0}],
            'replication': {'state': 'disabled'},
        })
        raise


if __name__ == '__main__':
    main()
