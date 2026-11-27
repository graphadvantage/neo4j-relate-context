"""Aura-native, MCP-scoped intent promotion and retrieval. No additional MCP server."""
import json
import os
import threading
import uuid
from datetime import datetime, timezone

from .intent_model import (MODEL, DIMENSIONS, VALIDATOR_VERSION, bounded, candidate,
                           rejection_reason, fuse, lucene_query, scope, query_integrity)

CANDIDATES = '''
MATCH (p:ContextProject)-[:HAS_SESSION]->(s:ContextSession {source:'claude-desktop'})
      -[:HAS_TURN]->(t:ContextTurn)-[:HAS_MESSAGE]->(prompt:ContextMessage {kind:'prompt',role:'user'})
MATCH (t)-[:HAS_MESSAGE]->(message:ContextMessage)-[:EXECUTES_QUERY]->(q:ContextQuery)
WHERE q.cypher IS NOT NULL AND size(trim(prompt.body))>=12
  AND p.name IS NOT NULL AND trim(p.name)<>''
  AND (message.mcp_source_id IS NOT NULL OR message.tool CONTAINS '__'
       OR message.tool CONTAINS '.' OR message.tool CONTAINS '/')
  AND NOT EXISTS {
    MATCH (prompt)-[:EXPRESSES]->(i:ContextIntent)-[:RESOLVED_BY]->(q)
    WHERE i.embedding_model = $model
  }
  AND NOT EXISTS {
    MATCH (prompt)-[:REJECTED_AS]->(rej:ContextIntentRejection {query_id:q.id})
    WHERE rej.validator_version = $validator
  }
RETURN DISTINCT p.name AS project,s.session_id AS session,t.id AS turn_id,
       prompt.id AS prompt_id,prompt.body AS question,prompt.timestamp AS timestamp,
       message.tool AS tool,message.mcp_source_id AS mcp_source_id,
       q.id AS query_id,q.cypher AS cypher
ORDER BY timestamp,prompt_id,query_id LIMIT $limit
'''
REJECT = '''
UNWIND $rows AS row
MATCH (prompt:ContextMessage {id:row.prompt_id})
MERGE (rej:ContextIntentRejection {prompt_id:row.prompt_id,query_id:row.query_id,validator_version:$validator})
  ON CREATE SET rej.first_seen=datetime()
SET rej.reason=row.reason,rej.last_seen=datetime(),rej.embedding_model=$model
MERGE (prompt)-[:REJECTED_AS]->(rej)
RETURN count(*) AS rejected
'''
EMBED = '''CYPHER 25
CALL ai.text.embedBatch($texts, 'OpenAI', {token:$token, model:$model, dimensions:$dimensions})
YIELD index, vector
WITH $rows[index] AS row, vector
MERGE (i:ContextIntent {id:row.id})
SET i.question=row.question,i.search_text=row.search_text,i.project=row.project,
    i.mcp_source_id=row.mcp_source_id,i.scope_key=row.scope_key,
    i.embedding=vector,i.embedding_model=$model,i.embedding_dimensions=$dimensions,
    i.updated_at=datetime(),i.validation='client_reported_unverified'
RETURN i.id AS id,size(toFloatList(i.embedding)) AS dimensions
'''
LINK = '''CYPHER 25
UNWIND $rows AS row
MATCH (i:ContextIntent {id:row.id,project:row.project,mcp_source_id:row.mcp_source_id})
MATCH (p:ContextProject {name:row.project})-[:HAS_SESSION]->(:ContextSession)
      -[:HAS_TURN]->(t:ContextTurn {id:row.turn_id})-[:HAS_MESSAGE]->(m:ContextMessage {id:row.prompt_id})
MATCH (t)-[:HAS_MESSAGE]->(:ContextMessage)-[:EXECUTES_QUERY]->(q:ContextQuery {id:row.query_id})
WHERE q.mcp_source_id IS NULL OR q.mcp_source_id=row.mcp_source_id
SET q.mcp_source_id=row.mcp_source_id,q.project=row.project
MERGE (m)-[:EXPRESSES]->(i)
MERGE (i)-[r:RESOLVED_BY]->(q)
SET r.association='same_recorded_turn',r.validation='client_reported_unverified'
MERGE (i)-[:IN_PROJECT]->(p)
RETURN count(*) AS linked
'''
ENN = '''CYPHER 25
MATCH (i:ContextIntent)
WHERE i.mcp_source_id=$mcp_source_id AND i.project=$project
  AND i.embedding_model=$model AND i.embedding IS NOT NULL
WITH i,vector.similarity.cosine(i.embedding,$vector) AS score
RETURN i.id AS id,i.question AS question,i.validation AS validation,score
ORDER BY score DESC,id LIMIT $limit
'''
ANN = '''CYPHER 25
MATCH (i:ContextIntent)
SEARCH i IN (VECTOR INDEX context_intents_vector FOR $vector
  WHERE i.mcp_source_id=$mcp_source_id AND i.project=$project AND i.embedding_model=$model
  LIMIT $limit) SCORE AS score
RETURN i.id AS id,i.question AS question,i.validation AS validation,score
ORDER BY score DESC,id
'''
FULLTEXT = '''CYPHER 25
MATCH (i:ContextIntent)
SEARCH i IN (FULLTEXT INDEX context_intents_text FOR $lucene LIMIT $limit) SCORE AS score
WHERE i.mcp_source_id=$mcp_source_id AND i.project=$project AND i.embedding_model=$model
RETURN i.id AS id,i.question AS question,i.validation AS validation,score
ORDER BY score DESC,id
'''
REFERENCES = '''CYPHER 25
UNWIND $ids AS id
MATCH (i:ContextIntent {id:id,project:$project,mcp_source_id:$mcp_source_id})
OPTIONAL MATCH (i)-[:RESOLVED_BY]->(q:ContextQuery {project:$project,mcp_source_id:$mcp_source_id})
RETURN i.id AS id,collect(q{.id,.cypher,.parameters,.source,validation:'client_reported_unverified'})[0..5] AS queries
'''
GET_QUERY = '''CYPHER 25
MATCH (i:ContextIntent {project:$project,mcp_source_id:$mcp_source_id})-[:RESOLVED_BY]->
      (q:ContextQuery {id:$query_id,project:$project,mcp_source_id:$mcp_source_id})
RETURN DISTINCT q.id AS id,q.cypher AS cypher,q.parameters AS parameters,
       q.rationale AS rationale,q.result_summary AS result_summary,q.timestamp AS timestamp
LIMIT 1
'''
EVIDENCE = '''CYPHER 25
MATCH (i:ContextIntent {id:$intent_id,project:$project,mcp_source_id:$mcp_source_id})
MATCH (m:ContextMessage)-[:EXPRESSES]->(i)
MATCH (s:ContextSession)-[:HAS_MESSAGE]->(m)
RETURN DISTINCT m.id AS message_id,substring(m.body,0,4000) AS question,
       m.timestamp AS timestamp,s.source AS agent,s.session_id AS session,
       'same_recorded_turn' AS association
ORDER BY timestamp DESC LIMIT $limit
'''


class IntentRecall:
    def __init__(self, sources=None):
        self.sources = sources
        self.driver = None
        self.database = os.environ.get('AURA_DATABASE') or 'neo4j'
        self.lock = threading.Lock()
        self.ready = False
        self.status = {'state':'pending','model':MODEL,'dimensions':DIMENSIONS}
        self.intent_stats = {'state':'pending','groups':[]}

    def refresh_intent_statistics(self):
        if not self.enabled():
            self.intent_stats={'state':'disabled','groups':[]}
            return self.intent_stats
        try:
            groups=self.run('''MATCH(i:ContextIntent)
                OPTIONAL MATCH(m:ContextMessage)-[:EXPRESSES]->(i)
                OPTIONAL MATCH(s:ContextSession)-[:HAS_MESSAGE]->(m)
                WITH i,collect(DISTINCT s.source) AS agents
                RETURN i.project AS project,agents,count(i) AS intents''')
            self.intent_stats={'state':'ready','groups':groups}
        except Exception as exc:
            self.intent_stats={'state':'unavailable','groups':[],'error':type(exc).__name__}
        return self.intent_stats

    def enabled(self):
        return all(os.environ.get(name) for name in ('AURA_URI','AURA_USERNAME','AURA_PASSWORD','OPENAI_API_KEY'))

    def connect(self):
        if not all(os.environ.get(name) for name in ('AURA_URI','AURA_USERNAME','AURA_PASSWORD')):
            raise ValueError('Shared recall requires AURA_* credentials in the console environment')
        if self.driver is None:
            from neo4j import GraphDatabase
            self.driver = GraphDatabase.driver(os.environ['AURA_URI'],
                auth=(os.environ['AURA_USERNAME'],os.environ['AURA_PASSWORD']),
                connection_timeout=10,connection_acquisition_timeout=15)

    def run(self, query, **parameters):
        self.connect()
        with self.driver.session(database=self.database) as session:
            # No automatic retries: embedding queries invoke a paid external service.
            with session.begin_transaction(timeout=90) as tx:
                rows = tx.run(query,**parameters).data()
                tx.commit()
                return rows

    def initialize(self):
        if self.ready:
            return
        self.run('CREATE CONSTRAINT context_intent_id IF NOT EXISTS FOR (i:ContextIntent) REQUIRE i.id IS UNIQUE')
        self.run('''CYPHER 25 CREATE VECTOR INDEX context_intents_vector IF NOT EXISTS
            FOR (i:ContextIntent) ON i.embedding
            WITH [i.mcp_source_id,i.project,i.embedding_model]
            OPTIONS {indexConfig:{`vector.dimensions`:1536,`vector.similarity_function`:'cosine'}}''')
        self.run('''CREATE FULLTEXT INDEX context_intents_text IF NOT EXISTS
            FOR (i:ContextIntent) ON EACH [i.search_text,i.scope_key]''')
        self.run('CREATE INDEX context_intent_scope IF NOT EXISTS FOR (i:ContextIntent) ON (i.mcp_source_id,i.project,i.embedding_model)')
        self.run('CREATE INDEX context_rejection_scope IF NOT EXISTS FOR (r:ContextIntentRejection) ON (r.validator_version,r.query_id)')
        self.run('CALL db.awaitIndexes(60)')
        self.ready = True

    def promote(self, limit=20):
        with self.lock:
            self.initialize()
            raw = self.run(CANDIDATES,model=MODEL,validator=VALIDATOR_VERSION,limit=limit)
            rows,rejected = [],[]
            for row in raw:
                value = candidate(row)
                if value:
                    rows.append(value)
                else:
                    rejected.append({'prompt_id':row.get('prompt_id'),'query_id':row.get('query_id'),
                                     'reason':rejection_reason(row)})
            # Persist rejections so permanently-invalid rows stop refilling the
            # oldest-first scan window and starving newer valid candidates.
            if rejected:
                self.run(REJECT,rows=rejected,validator=VALIDATOR_VERSION,model=MODEL)
            existing = {r['id'] for r in self.run('''MATCH(i:ContextIntent)
                WHERE i.id IN $ids AND i.embedding_model=$model AND i.embedding IS NOT NULL
                RETURN i.id AS id''',ids=[row['id'] for row in rows],model=MODEL)}
            missing = {row['id']:row for row in rows if row['id'] not in existing}
            if missing:
                batch = list(missing.values())
                generated = self.run(EMBED,texts=[row['search_text'] for row in batch],rows=batch,
                    token=os.environ['OPENAI_API_KEY'],model=MODEL,dimensions=DIMENSIONS)
                if len(generated)!=len(batch) or any(r['dimensions']!=DIMENSIONS for r in generated):
                    raise ValueError('Embedding batch dimensions/count mismatch')
            linked = self.run(LINK,rows=rows)[0]['linked'] if rows else 0
            self.status = {'state':'ready','model':MODEL,'dimensions':DIMENSIONS,
                'last_run':datetime.now(timezone.utc).isoformat(),'embedded':len(missing),
                'linked':linked,'rejected':len(rejected),'skipped_unscoped':len(raw)-len(rows)}
            return self.status

    def local_candidates(self, scoped, query_id=None):
        rows,errors=[],[]
        if not self.sources:
            return rows,errors
        for source in self.sources.public():
            if source['kind']!='relate' or source['id']=='capture':
                continue
            try:
                result=self.sources.query(source['id'],'''MATCH(p:ContextProject {name:$project})
                    -[:HAS_SESSION]->(s:ContextSession {source:'claude-desktop'})-[:HAS_TURN]->(t:ContextTurn)
                    -[:HAS_MESSAGE]->(prompt:ContextMessage {kind:'prompt',role:'user'})
                    MATCH(t)-[:HAS_MESSAGE]->(message:ContextMessage)-[:EXECUTES_QUERY]->(q:ContextQuery)
                    WHERE $query_id IS NULL OR q.id=$query_id
                    RETURN p.name AS project,s.session_id AS session,t.id AS turn_id,
                      prompt.id AS prompt_id,prompt.body AS question,prompt.timestamp AS timestamp,
                      message.tool AS tool,message.mcp_source_id AS mcp_source_id,
                      q.id AS query_id,q.cypher AS cypher,q.parameters AS parameters,
                      q.rationale AS rationale,q.result_summary AS result_summary
                    ORDER BY timestamp DESC LIMIT 100''',
                    {'project':scoped['project'],'query_id':query_id})
                for row in result['rows']:
                    value=candidate(row)
                    if value and value['mcp_source_id']==scoped['mcp_source_id']:
                        rows.append({**value,'origin':source['name']})
            except Exception as exc:
                errors.append({'source':source['name'],'error':type(exc).__name__})
        return rows,errors

    def pending(self, scoped, query, limit):
        """Local fallback is lexical and explicitly labeled; no guessed semantic score."""
        rows,errors=self.local_candidates(scoped)
        matches={}
        words=set(query.casefold().split())
        for value in rows:
            overlap=len(words & set(value['search_text'].casefold().split()))
            if overlap:
                matches[value['id']]={'id':value['id'],'question':value['question'][:1200],
                    'query_id':value['query_id'],'origin':value['origin'],
                    'validation':'client_reported_unverified','lexical_overlap':overlap}
        return sorted(matches.values(),key=lambda row:-row['lexical_overlap'])[:limit],errors

    def request(self, operation, body):
        if body.get('context_kind')=='Code':
            from .coding_recall import CodingRecall
            return CodingRecall(self.run,self.sources).request(operation,body)
        scoped = scope(body.get('project'),body.get('mcp_source_id'))
        limit = body.get('limit',5)
        if type(limit) is not int or not 1<=limit<=20:
            raise ValueError('limit must be between 1 and 20')
        max_chars = body.get('max_chars',12000)
        # Validate budget before any external provider call.
        bounded({},max_chars)
        result={'receipt_id':str(uuid.uuid4()),'scope':scoped,'origin':'aura',
                'embedding_model':MODEL,'dimensions':DIMENSIONS,'state':'ready'}
        if operation=='recall':
            query = body.get('query')
            if not isinstance(query,str) or not query.strip() or len(query)>8000:
                raise ValueError('query must be between 1 and 8000 characters')
            mode = body.get('mode','hybrid')
            if mode not in ('enn','ann','hybrid'):
                raise ValueError('mode must be enn, ann or hybrid')
            result['mode']=mode
            vector_rows,text_rows=[],[]
            try:
                self.initialize()
                # Return the vector only inside Aura; no large vector payload crosses MCP.
                prefix="WITH ai.text.embed($question,'OpenAI',{token:$token,model:$model,dimensions:1536}) AS queryVector\n"
                vector_query=(ANN if mode=='ann' else ENN).replace('CYPHER 25\n','').replace('$vector','queryVector')
                vector_rows=self.run('CYPHER 25\n'+prefix+vector_query,**scoped,question=query,
                    token=os.environ['OPENAI_API_KEY'],model=MODEL,limit=min(20,limit*3))
                if mode=='hybrid':
                    lexical=lucene_query(query,scoped['scope_key'])
                    if lexical:
                        text_rows=self.run(FULLTEXT,**scoped,lucene=lexical,model=MODEL,limit=min(20,limit*3))
                matches=fuse(vector_rows,text_rows,limit)
                references={r['id']:r['queries'] for r in self.run(REFERENCES,
                    **scoped,ids=[r['id'] for r in matches])} if matches else {}
                for item in matches:
                    item['question']=item['question'][:1200]
                    item['queries']=[]
                    for reference in references.get(item['id'],[]):
                        text=reference.get('cypher')
                        if query_integrity(text)!='not_validated':
                            continue
                        reference.pop('preview',None)
                        reference['retrieval_required']=False
                        item['queries'].append(reference)
                    item['origin']='aura'
                result['matches']=matches
                result['next_step']='Use a relevant returned full Cypher after checking applicability. If retrieval_required is true, fetch get_query; never reconstruct omitted query text. Recall again for each new user intent.'
            except Exception as exc:
                result.update(state='degraded',matches=[],errors=[{'source':'aura','error':type(exc).__name__}])
            local,errors=self.pending(scoped,query,limit)
            known={row['id'] for row in result['matches']}
            result['pending_local']=[row for row in local if row['id'] not in known]
            if errors:
                result.setdefault('errors',[]).extend(errors)
                result['state']='degraded'
        elif operation=='query':
            query_id=body.get('query_id')
            if not isinstance(query_id,str) or not query_id or len(query_id)>200:
                raise ValueError('query_id is required')
            try:
                rows=self.run(GET_QUERY,**scoped,query_id=query_id)
                result['query']={**rows[0],'validation':'client_reported_unverified'} if rows else None
            except Exception as exc:
                rows=[]
                result.update(state='degraded',query=None,errors=[{'source':'aura','error':type(exc).__name__}])
            if not rows:
                local,errors=self.local_candidates(scoped,query_id)
                if local:
                    row=local[0]
                    result['origin']=row['origin']
                    result['query']={key:row.get(key) for key in ('cypher','parameters','rationale','result_summary','timestamp')}
                    result['query'].update(id=query_id,validation='client_reported_unverified')
                    if result['state']=='ready': result['state']='pending_replication'
                elif result['state']=='ready': result['state']='not_found'
                if errors: result.setdefault('errors',[]).extend(errors)
        elif operation=='evidence':
            intent_id=body.get('intent_id')
            if not isinstance(intent_id,str) or not intent_id or len(intent_id)>200:
                raise ValueError('intent_id is required')
            try:
                result['evidence']=self.run(EVIDENCE,**scoped,intent_id=intent_id,limit=limit)
            except Exception as exc:
                result.update(state='degraded',evidence=[],errors=[{'source':'aura','error':type(exc).__name__}])
            if not result['evidence']:
                local,errors=self.local_candidates(scoped)
                found={row['prompt_id']:row for row in local if row['id']==intent_id}
                result['evidence']=[{'message_id':row['prompt_id'],'question':row['question'][:4000],
                    'timestamp':row.get('timestamp'),'session':row.get('session'),'origin':row['origin'],
                    'association':'same_recorded_turn'} for row in list(found.values())[:limit]]
                if result['evidence']:
                    result['origin']='local'
                    if result['state']=='ready': result['state']='pending_replication'
                elif result['state']=='ready': result['state']='not_found'
                if errors: result.setdefault('errors',[]).extend(errors)
        else:
            raise ValueError('Unknown memory operation')
        if operation=='query' and result.get('query'):
            integrity=query_integrity(result['query']['cypher'])
            result['query']['integrity']=integrity
            if integrity!='not_validated':
                result['state']='incomplete'
                result['query']['reusable']=False
                result['errors']=[{'source':'stored_query','error':'Stored query is abbreviated or descriptive text; retrieve the original transport capture.'}]
        return bounded(result,max_chars)

    def close(self):
        if self.driver:
            self.driver.close()
