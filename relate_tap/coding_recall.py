"""Repository-scoped recall of recorded coding work across agents.

Uses lexical relevance and recency over existing evidence; no provider calls,
new summaries, or requirement for structured memories or saved Cypher.
"""
import re
import uuid

from .intent_model import bounded

BASE = '''MATCH (p:ContextProject:Code {name:$project})-[:HAS_SESSION]->(s:ContextSession:Code)
WHERE ($source_agent IS NULL OR s.source=$source_agent)
'''
SESSIONS = BASE + '''
OPTIONAL MATCH (s)-[:HAS_MESSAGE]->(m:ContextMessage)
WITH s,max(m.timestamp) AS last_seen,count(DISTINCT m) AS messages
OPTIONAL MATCH(s)-[:HAS_TURN]->(t:ContextTurn)-[:HAS_MESSAGE]->(u:ContextMessage {kind:'prompt'})
WITH s,last_seen,messages,t,u ORDER BY u.timestamp DESC,t.id DESC
WITH s,last_seen,messages,head(collect(t)) AS latest
RETURN s.id AS id,s.session_id AS session,s.source AS agent,latest.git_commit AS git_commit,
       latest.git_branch AS git_branch,latest.git_dirty AS git_dirty,
       coalesce(latest.git_source,'unavailable') AS git_source,
       last_seen,messages ORDER BY last_seen DESC,s.id LIMIT $limit
'''
TURNS = BASE + '''
MATCH(s)-[:HAS_TURN]->(t:ContextTurn)-[:HAS_MESSAGE]->(u:ContextMessage {kind:'prompt'})
MATCH(t)-[:HAS_MESSAGE]->(a:ContextMessage {kind:'final_response'})
OPTIONAL MATCH(t)-[:HAS_REASONING]->(r:ContextReasoning)
WITH s,t,u,a,r,toLower(u.body+' '+a.body+' '+coalesce(r.summary,'')) AS text
WITH s,t,u,a,r,reduce(score=0,word IN $words | score+CASE WHEN text CONTAINS word THEN 1 ELSE 0 END) AS relevance
WHERE size($words)=0 OR relevance>0
RETURN t.id AS turn_id,s.id AS session_key,s.session_id AS session,s.source AS agent,
       u.id AS prompt_id,substring(u.body,0,1600) AS prompt,
       a.id AS response_id,substring(a.body,0,4000) AS response,
       r.summary AS reasoning_summary,u.timestamp AS timestamp,t.git_commit AS git_commit,
       t.git_branch AS git_branch,t.git_dirty AS git_dirty,coalesce(t.git_source,'unavailable') AS git_source,
       size(u.body)>1600 OR size(a.body)>4000 AS evidence_truncated
ORDER BY relevance DESC,timestamp DESC,turn_id LIMIT $limit
'''
MEMORIES = BASE + '''
MATCH(s)-[:HAS_MEMORY]->(f:ContextMemory)
OPTIONAL MATCH(f)-[:SUPPORTED_BY]->(m:ContextMessage)
WITH s,f,max(m.timestamp) AS timestamp,collect(DISTINCT m.id) AS evidence
OPTIONAL MATCH(f)-[:HAS_STATUS_EVENT]->(event:ContextMemoryEvent)
WITH s,f,timestamp,evidence,event ORDER BY event.at DESC,event.id DESC
WITH s,f,timestamp,evidence,head(collect(event)) AS latest
RETURN f.id AS id,f.kind AS kind,f.subject AS subject,f.body AS body,s.source AS agent,
       s.id AS session_key,s.session_id AS session,timestamp,evidence,
       f.git_commit AS git_commit,f.git_dirty AS git_dirty,coalesce(f.git_source,'unavailable') AS git_source,
       coalesce(latest.kind,'open') AS status,latest.id AS status_event_id,
       latest.reason AS status_reason,latest.at AS status_at,latest.replacement_id AS replacement_id
ORDER BY CASE WHEN status IN ['open','reopened'] THEN 0 WHEN status='resolved' THEN 1 ELSE 2 END,
         timestamp DESC,f.id LIMIT $limit
'''
NOTES = """MATCH(p:ContextProject:Code {name:$project})-[:HAS_NOTE]->(n:ContextNote)
WHERE $source_agent IS NULL OR n.source=$source_agent
RETURN n.id AS id,n.title AS title,n.body AS body,n.source AS agent,
       n.created_at AS timestamp,n.git_commit AS git_commit
ORDER BY timestamp DESC,n.id LIMIT $limit
"""
MESSAGES = BASE + '''
WITH s WHERE s.id=$session OR s.session_id=$session
MATCH(s)-[:HAS_MESSAGE]->(m:ContextMessage)
WHERE $before_position IS NULL OR m.position<$before_position
OPTIONAL MATCH(t:ContextTurn)-[:HAS_MESSAGE]->(m)
RETURN s.id AS session_key,s.session_id AS session,s.source AS agent,
       m.id AS message_id,m.position AS position,m.role AS role,m.kind AS kind,
       m.body AS body,m.timestamp AS timestamp,t.git_commit AS git_commit,
       t.git_branch AS git_branch,t.git_dirty AS git_dirty,coalesce(t.git_source,'unavailable') AS git_source
ORDER BY m.position DESC,m.id LIMIT $limit
'''
EVIDENCE = BASE + '''
MATCH(s)-[:HAS_TURN]->(t:ContextTurn {id:$turn_id})-[:HAS_MESSAGE]->(m:ContextMessage)
OPTIONAL MATCH(t)-[:HAS_REASONING]->(r:ContextReasoning)
RETURN s.id AS session_key,s.source AS agent,t.id AS turn_id,m.id AS message_id,
       m.position AS position,m.role AS role,m.body AS body,m.timestamp AS timestamp,
       r.summary AS reasoning_summary,t.git_commit AS git_commit,
       t.git_branch AS git_branch,t.git_dirty AS git_dirty,coalesce(t.git_source,'unavailable') AS git_source
ORDER BY m.position,m.id LIMIT $limit
'''


def coding_scope(body):
    project=body.get('project')
    if not isinstance(project,str) or not project.strip() or len(project)>512:
        raise ValueError('A non-empty repository project is required')
    if body.get('mcp_source_id'):
        raise ValueError('Code recall is scoped by repository; omit mcp_source_id')
    agent=body.get('source_agent')
    if agent is not None and (not isinstance(agent,str) or not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,63}',agent)):
        raise ValueError('Invalid source_agent filter')
    return {'project':project.strip(),'source_agent':agent}


class CodingRecall:
    def __init__(self, run, sources=None):
        self.run,self.sources=run,sources

    def request(self, operation, body):
        scoped=coding_scope(body)
        limit=body.get('limit',5)
        if type(limit) is not int or not 1<=limit<=50:raise ValueError('limit must be between 1 and 50')
        max_chars=body.get('max_chars',12000)
        bounded({},max_chars)
        query=body.get('query','')
        if not isinstance(query,str) or len(query)>8000:raise ValueError('query must be at most 8000 characters')
        words=list(dict.fromkeys(re.findall(r'\w+',query.casefold())))[:32]
        # Avoid ranking every record on common connective words.
        words=[w for w in words if w not in {'the','a','an','and','or','of','for','to','in','is','what','are','this','repo','repository'}]
        params={**scoped,'limit':limit}
        if operation=='recall':
            queries=[('sessions',SESSIONS,params),('matches',TURNS,{**params,'words':words}),('memories',MEMORIES,params),('notes',NOTES,params)]
        elif operation=='session':
            session=body.get('session')
            if not isinstance(session,str) or not session or len(session)>200:raise ValueError('session is required')
            before=body.get('before_position')
            if before is not None and (type(before) is not int or before<1):raise ValueError('Invalid before_position')
            queries=[('messages',MESSAGES,{**params,'session':session,'before_position':before})]
        elif operation=='evidence':
            turn=body.get('turn_id')
            if not isinstance(turn,str) or not turn or len(turn)>200:raise ValueError('turn_id is required for Code evidence')
            queries=[('evidence',EVIDENCE,{**params,'turn_id':turn})]
        else:raise ValueError('Unsupported Code recall operation')
        result={'receipt_id':str(uuid.uuid4()),'scope':{**scoped,'context_kind':'Code'},
                'origin':'aura','state':'ready','retrieval_mode':'lexical_recency',
                'validation':'recorded_evidence_unverified_against_current_git','errors':[]}
        for key,statement,parameters in queries:
            try:result[key]=self.run(statement,**parameters)
            except Exception as exc:
                result[key]=[]
                result['state']='degraded'
                result['errors'].append({'source':'aura','error':type(exc).__name__})
        # Keep pending local work separate and labeled; never call it replicated.
        pending={key:[] for key,_,_ in queries}
        if self.sources:
            for source in self.sources.public():
                if source['kind']!='relate' or source['id']=='capture':continue
                for key,statement,parameters in queries:
                    try:
                        rows=self.sources.query(source['id'],statement,parameters)['rows']
                        for row in rows:
                            identity=row.get('turn_id') or row.get('message_id') or row.get('id')
                            if not any((r.get('turn_id') or r.get('message_id') or r.get('id'))==identity for r in result[key]):
                                pending[key].append({**row,'origin':source['name'],'state':'pending_replication'})
                    except Exception as exc:
                        result['errors'].append({'source':source['name'],'error':type(exc).__name__})
            result['pending_local']=[{'record_type':key,**row} for key,rows in pending.items() for row in rows][:limit]
        if operation=='session':
            positions=[r['position'] for r in result['messages']]
            result['next_before_position']=min(positions) if len(positions)==limit else None
        result['next_step']='Expand a match with get_evidence(context_kind="Code", turn_id=...). Read a shared session with get_session(context_kind="Code", session=session_key). Verify recalled claims against live Git.'
        if result['state']=='ready' and not any(result[key] for key,_,_ in queries):
            result['state']='pending_replication' if any(pending.values()) else 'empty'
        if not result['errors']:result.pop('errors')
        from .git_state import freshness
        for key in ('sessions','matches','memories','messages','evidence','pending_local'):
            for row in result.get(key,[]):
                row['freshness']=freshness(row.get('git_commit'),row.get('git_dirty'))
        result=bounded(result,max_chars)
        if operation=='session':
            positions=[r['position'] for r in result['messages']]
            result['next_before_position']=min(positions) if positions and (len(positions)==limit or result['truncated']) else None
        return result
