"""Incremental Aura full-text indexing of immutable recorded Code turns."""
import re
import threading

INDEX = 'context_code_turns_text'
CREATE_INDEX = '''CREATE FULLTEXT INDEX context_code_turns_text IF NOT EXISTS
FOR (t:ContextTurn) ON EACH [t.search_text]'''
BACKFILL = '''
MATCH (:ContextProject:Code)-[:HAS_SESSION]->(:ContextSession:Code)-[:HAS_TURN]->(t:ContextTurn)
WHERE t.search_text IS NULL
MATCH (t)-[:HAS_MESSAGE]->(u:ContextMessage {kind:'prompt'})
MATCH (t)-[:HAS_MESSAGE]->(a:ContextMessage {kind:'final_response'})
WITH DISTINCT t,u,a ORDER BY t.id LIMIT $batch
OPTIONAL MATCH (t)-[:HAS_REASONING]->(r:ContextReasoning)
SET t.search_text=u.body+' '+a.body+' '+coalesce(r.summary,'')
RETURN count(DISTINCT t) AS indexed
'''
INDEX_STATE = '''SHOW INDEXES YIELD name, state
WHERE name='context_code_turns_text' RETURN state'''


def search_query(question):
    """Quote analyzed words; user input never becomes Lucene operators."""
    words = list(dict.fromkeys(re.findall(r'\w+', question, re.UNICODE)))[:32]
    return ' OR '.join('"'+word+'"' for word in words) if words else None


class CodingSearch:
    def __init__(self, run):
        self.run = run
        self.lock = threading.Lock()
        self.created = False
        self.status = {'state':'pending'}

    def prepare(self, batch=200):
        """Bound each maintenance pass; rejected/partial turns cannot block it."""
        with self.lock:
            try:
                if not self.created:
                    self.run(CREATE_INDEX)
                    self.created = True
                rows = self.run(BACKFILL, batch=batch)
                count = rows[0]['indexed'] if rows else 0
                states = self.run(INDEX_STATE)
                online = bool(states) and states[0]['state'] == 'ONLINE'
                self.status = {'state':'ready' if online and count < batch else 'building',
                               'indexed':count}
            except Exception as exc:
                self.created = False
                self.status = {'state':'unavailable', 'error':type(exc).__name__}
            return dict(self.status)

    def available(self):
        if self.status['state'] == 'pending':
            self.prepare()
        return self.status['state'] == 'ready'

    def invalidate(self, error):
        with self.lock:
            self.created = False
            self.status = {'state':'unavailable','error':error}
