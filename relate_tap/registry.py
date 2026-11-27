"""Read live route leases and persistence receipts; never infer current state from history."""
from datetime import datetime
import json
import time
from .bootstrap import resolved_route


def registry(spool):
    routes, agents = [], {}
    for path in sorted(spool.glob('*.status.json')):
        try:
            status = json.loads(path.read_text())
            if isinstance(status.get('agent'), str):
                agents[status['agent']] = status
        except (OSError, ValueError):
            pass
    for path in sorted(spool.glob('*.push.json')):
        try:
            push = json.loads(path.read_text())
            agent = push['agent']
            if agent in agents:
                agents[agent]['replication'] = push
        except (OSError, ValueError, KeyError):
            pass
    for path in sorted(spool.glob('*.route.json')):
        try:
            route = resolved_route(json.loads(path.read_text()))
            seen = datetime.fromisoformat(route['last_seen']).timestamp()
            route['active'] = bool(route.get('connected') and time.time()-seen < 20)
            receipt = agents.get(route.get('agent'), {})
            route['database_path'] = route.get('database_path') or receipt.get('database_path')
            route['database_state'] = receipt.get('state', 'pending')
            route['persisted_offset'] = receipt.get('offsets', {}).get(route['connection']+'.jsonl', 0)
            route['replication'] = receipt.get('replication', {'state': 'disabled'})
            route['commits'] = [c for c in receipt.get('commits', []) if c.get('connection') == route['connection']]
            routes.append(route)
        except (OSError, ValueError, KeyError):
            pass
    return {'routes': routes, 'agents': list(agents.values())}
