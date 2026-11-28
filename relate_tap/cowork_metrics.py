"""Durable, metadata-only counts of cross-agent repository evidence retrieval."""
from datetime import datetime, timezone
import threading
import time

from memory_graph import direct_query

EVIDENCE_LISTS = ('matches','memories','notes','evidence','messages')
SAVE_HIT = '''MERGE(h:CoworkRecallHit {id:$input.id})
ON CREATE SET h.agent=$input.agent,h.project=$input.project,
              h.source_agents=$input.source_agents,h.timestamp=$input.timestamp
RETURN h.id AS id'''
COUNTS = '''MATCH(h:CoworkRecallHit)
RETURN h.agent AS agent,h.project AS project,count(h) AS hits
ORDER BY agent,project'''


def cowork_hit(operation, request, result, client):
    """One receipt qualifies once, regardless of how many peers/records it returns."""
    if operation not in ('recall','session','evidence') or not isinstance(client,dict):
        return None
    actor=client.get('agent')
    scoped=result.get('scope',{})
    project=scoped.get('project')
    receipt=result.get('receipt_id')
    if (not isinstance(actor,str) or not actor or len(actor)>64
            or result.get('state') not in ('ready','degraded','pending_replication')
            or scoped.get('context_kind')!='Code' or request.get('context_kind')!='Code'
            or not isinstance(project,str) or not project.startswith(('git:','git-local:'))
            or project!=request.get('project') or len(project)>512
            or not isinstance(receipt,str) or not receipt or len(receipt)>200):
        return None
    peers=set()
    rows=[row for key in EVIDENCE_LISTS for row in result.get(key,[])]
    rows.extend(row for row in result.get('pending_local',[]) if row.get('record_type') in EVIDENCE_LISTS)
    for row in rows:
        author=row.get('agent')
        if (isinstance(author,str) and author and author!=actor and len(author)<=64
                and (row.get('turn_id') or row.get('message_id') or row.get('id'))
                and any(isinstance(row.get(field),str) and row[field].strip()
                        for field in ('response','body','reasoning_summary'))):
            peers.add(author)
    if not peers:
        return None
    return {'id':receipt,'agent':actor,'project':project,'source_agents':sorted(peers),
            'timestamp':datetime.now(timezone.utc).isoformat()}


class CoworkMetrics:
    def __init__(self, database, graph_lock):
        self.database,self.graph_lock=database,graph_lock
        self.lock=threading.Lock()
        self.cache=None
        self.cached_at=0
        self.write_error=None

    def record(self, operation, request, result, client):
        hit=cowork_hit(operation,request,result,client)
        if hit is None:
            return False
        with self.lock:
            try:
                with self.graph_lock:
                    direct_query(self.database,SAVE_HIT,hit,read_only=False)
                self.cache=None
            except Exception as exc:
                self.write_error=type(exc).__name__
                raise
        return True

    def statistics(self):
        with self.lock:
            if self.cache is None or time.monotonic()-self.cached_at>=5:
                with self.graph_lock:
                    rows=direct_query(self.database,COUNTS,{})
                self.cache={'state':'ready','groups':rows}
                self.cached_at=time.monotonic()
            if self.write_error:
                return {**self.cache,'state':'unavailable','error':self.write_error}
            return self.cache
