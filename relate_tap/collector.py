"""Route taps hosted in Docker to databases owned by the console's Linux runtime."""
from pathlib import Path
import json
import os
import tempfile

from .bootstrap import canonical_agent, resolved_route
from .store import CaptureStore
from memory_graph import direct_query


class ContainerRoutes:
    def __init__(self, data, spool):
        self.directory = Path(data)/'agents'
        self.directory.mkdir(parents=True, exist_ok=True)
        self.spool, self.stores = Path(spool), {}
        self.locks, self.errors, self.syncs = {}, [], {}
        self.credentials = os.environ.get('RELATE_TAP_AURA_CREDENTIALS')
        if not self.credentials and all(os.environ.get('AURA_'+key) for key in ('URI','USERNAME','PASSWORD')):
            credentials = self.directory/'enterprise.env'
            fd, temporary = tempfile.mkstemp(dir=self.directory, prefix='.enterprise-')
            try:
                with os.fdopen(fd, 'w') as target:
                    for key in ('URI','USERNAME','PASSWORD','DATABASE'):
                        value = os.environ.get('AURA_'+key, 'neo4j' if key == 'DATABASE' else '')
                        if '\n' in value or '\r' in value:
                            raise ValueError('Invalid Aura connection value')
                        target.write('NEO4J_'+key+'='+value+'\n')
                os.replace(temporary, credentials)
            finally:
                if os.path.exists(temporary): os.unlink(temporary)
            self.credentials = str(credentials)

    def poll(self):
        self.errors = []
        for file in self.spool.glob('*.route.json'):
            try:
                raw = file.read_text()
            except FileNotFoundError:
                # A route can disappear between enumeration and read while its
                # owner atomically replaces it across the Docker shared mount.
                continue
            try:
                route = resolved_route(json.loads(raw))
                agent = canonical_agent(route.get('agent'))
                if not agent or route.get('writer') != 'console':
                    continue
                if agent not in self.stores:
                    store = CaptureStore(self.directory/(agent+'.rl'), self.spool,
                        self.directory/(agent+'.offsets.json'), route_agent=agent, route_writer='console')
                    with store.graph_lock:
                        if not store.database.exists():
                            direct_query(store.database, 'RETURN 1 AS initialized', {}, read_only=False)
                    self.stores[agent] = store
                    self.locks[str(store.database)] = store.graph_lock
                    if self.credentials:
                        from aura_sync import AuraSync
                        self.syncs[agent] = AuraSync(store.database, Path(self.credentials), store.graph_lock)
            except Exception as exc:
                self.errors.append({'file': file.name, 'error': type(exc).__name__, 'offset': 0})
        for store in self.stores.values():
            store.poll()

    def statuses(self):
        return [{'agent': agent, 'database': store.database.name, 'database_path': str(store.database),
                 'state': 'retrying' if store.errors else 'ready', 'offsets': dict(store.offsets),
                 'commits': list(store.commits),
                 'errors': store.errors, 'replication': dict(self.syncs[agent].status) if agent in self.syncs else {'state': 'disabled'}} for agent, store in self.stores.items()]
