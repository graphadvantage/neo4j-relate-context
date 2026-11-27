"""Manual Aura acceptance test. Synthetic scoped data only; provider calls are paid.

Run with the current source tree and .env:
 docker compose run --rm --no-deps -T -v "$PWD:/work:ro" -w /work --entrypoint python3 console - < tests/smoke_intent_recall.py
"""
import json
import uuid
from relate_tap.intent_recall import IntentRecall, FULLTEXT
from relate_tap.intent_model import scope, lucene_query, MODEL


def main():
    recall=IntentRecall()
    prefix='recall-smoke-'+uuid.uuid4().hex
    project=prefix
    question='Which synthetic supplier shares ownership with the sample vendor?'
    try:
        recall.initialize()
        for source in ('fixture-a','fixture-b'):
            key=prefix+'-'+source
            recall.run('''
                MERGE(p:ContextProject {name:$project})
                CREATE(s:ContextSession {id:$key,session_id:$key,source:'claude-desktop'})
                CREATE(t:ContextTurn {id:$key+'-turn'})
                CREATE(m:ContextMessage {id:$key+'-prompt',kind:'prompt',role:'user',body:$question,timestamp:toString(datetime())})
                CREATE(tool:ContextMessage {id:$key+'-tool',kind:'tool_call',tool:$source+'__read_cypher'})
                CREATE(q:ContextQuery {id:$key+'-query',cypher:'RETURN $fixture AS fixture',parameters:'{}'})
                CREATE(p)-[:HAS_SESSION]->(s),(s)-[:HAS_TURN]->(t),(s)-[:HAS_MESSAGE]->(m),
                  (t)-[:HAS_MESSAGE]->(m),(t)-[:HAS_MESSAGE]->(tool),(tool)-[:EXECUTES_QUERY]->(q)
                ''',project=project,key=key,question=question,source=source)
        recall.promote(100)
        results={}
        intent_id=None
        for mode in ('enn','ann','hybrid'):
            result=recall.request('recall',{'project':project,'mcp_source_id':'fixture-a','query':question,'mode':mode})
            if result['state']!='ready' or len(result['matches'])!=1:
                raise RuntimeError('Recall mode failed: '+mode+' '+json.dumps(result))
            match=result['matches'][0]
            if [q['id'] for q in match['queries']] != [prefix+'-fixture-a-query']:
                raise RuntimeError('Cross-MCP query association leaked')
            query=match['queries'][0]
            if query.get('cypher')!='RETURN $fixture AS fixture' or query.get('retrieval_required') is not False:
                raise RuntimeError('Recall did not supply the complete query')
            intent_id=match['id']
            results[mode]={'matches':1,'query_scope':'fixture-a'}
        scoped=scope(project,'fixture-a')
        lexical=recall.run(FULLTEXT,**scoped,lucene=lucene_query(question,scoped['scope_key']),model=MODEL,limit=5)
        if len(lexical)!=1 or lexical[0]['id']!=intent_id:
            raise RuntimeError('Full-text scope or visibility failed')
        valid=recall.request('query',{'project':project,'mcp_source_id':'fixture-a','query_id':prefix+'-fixture-a-query'})
        if valid.get('query',{}).get('cypher')!='RETURN $fixture AS fixture':
            raise RuntimeError('Exact Cypher retrieval failed')
        wrong=recall.request('query',{'project':project,'mcp_source_id':'fixture-b','query_id':prefix+'-fixture-a-query'})
        if wrong.get('query') is not None:
            raise RuntimeError('Query ID bypassed MCP scope')
        wrong_project=recall.request('evidence',{'project':project+'-other','mcp_source_id':'fixture-a','intent_id':intent_id})
        if wrong_project['evidence']:
            raise RuntimeError('Evidence ID bypassed project scope')
        evidence=recall.request('evidence',{'project':project,'mcp_source_id':'fixture-a','intent_id':intent_id})
        if len(evidence['evidence'])!=1 or evidence['evidence'][0]['question']!=question:
            raise RuntimeError('Evidence retrieval failed')
        print(json.dumps({'status':'passed','model':MODEL,'dimensions':1536,'modes':results,
            'scoped_fulltext':True,'exact_cypher':True,'cross_scope_query_blocked':True,
            'cross_project_evidence_blocked':True,'evidence':True},indent=2))
    finally:
        try:
            recall.run('''MATCH(n) WHERE n.id STARTS WITH $prefix
                OR (n:ContextIntent AND n.project=$project)
                OR (n:ContextProject AND n.name=$project) DETACH DELETE n''',prefix=prefix,project=project)
        finally:
            recall.close()


if __name__=='__main__':
    main()
