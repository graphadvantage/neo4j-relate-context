"""Manual Aura/OpenAI smoke test; uses synthetic questions and writes two test nodes.

From the repo root (Compose loads .env):
  docker compose run --rm --no-deps -T --entrypoint python3 console - < tests/smoke_aura_embeddings.py

Uses ai.text.embedBatch and native VECTOR<FLOAT32>(3072) properties.
Never prints credentials, questions from real chats, or embedding coordinates.
Not part of automatic unit-test discovery: this makes a paid provider request.
"""
import json
import os
import re
import sys

MODEL = 'text-embedding-3-large'
DIMENSIONS = 3072
QUESTIONS = [
    'Which projects have shared work across coding agents?',
    'What decisions should another coding agent recall for this repository?',
]
TEST_ID = 'ai-text-embed-batch-3072'
QUERY = """
CYPHER 25
CALL ai.text.embedBatch($listToEncode, 'OpenAI', {
    token: $token,
    model: $model,
    dimensions: $dimensions
})
YIELD index, vector AS embedding
WITH index, $listToEncode[index] AS resource, embedding, $test_id + ':' + toString(index) AS test_key
MERGE (n:ContextEmbeddingTest {id: test_key})
SET n.question = resource,
    n.embedding = embedding,
    n.embedding_model = $model,
    n.embedding_dimensions = $dimensions,
    n.updated_at = datetime()
RETURN index, n.id AS id, valueType(n.embedding) AS property_type,
       size(toFloatList(n.embedding)) AS dimensions
ORDER BY index
"""


def main():
    from neo4j import GraphDatabase

    required = ('AURA_URI', 'AURA_USERNAME', 'AURA_PASSWORD', 'OPENAI_API_KEY')
    missing = [name for name in required if not os.environ.get(name, '').strip()]
    if missing:
        print('Missing environment variables: ' + ', '.join(missing), file=sys.stderr)
        return 1
    with GraphDatabase.driver(os.environ['AURA_URI'],
            auth=(os.environ['AURA_USERNAME'], os.environ['AURA_PASSWORD']),
            connection_timeout=10, connection_acquisition_timeout=15) as driver:
        with driver.session(database=os.environ.get('AURA_DATABASE') or 'neo4j') as session:
            available = session.run(
                'CYPHER 25 SHOW PROCEDURES YIELD name WHERE name = $name RETURN name',
                name='ai.text.embedBatch').single()
            if not available:
                raise RuntimeError('ai.text.embedBatch is unavailable on this Aura instance')
            # Explicit transaction: do not automatically retry paid embedding requests.
            with session.begin_transaction(timeout=90) as tx:
                rows = tx.run(QUERY, listToEncode=QUESTIONS,
                    token=os.environ['OPENAI_API_KEY'], model=MODEL,
                    dimensions=DIMENSIONS, test_id=TEST_ID).data()
                expected_type = f'VECTOR<FLOAT32>({DIMENSIONS})'
                if ([row['index'] for row in rows] != list(range(len(QUESTIONS)))
                        or any(row['dimensions'] != DIMENSIONS
                            or row['property_type'].replace(' NOT NULL', '') != expected_type for row in rows)):
                    raise RuntimeError('Unexpected embedding metadata: ' + json.dumps(rows))
                tx.commit()
            # Verify committed properties independently, returning metadata only.
            saved = session.run('''CYPHER 25
                MATCH (n:ContextEmbeddingTest) WHERE n.id IN $ids
                RETURN n.id AS id, valueType(n.embedding) AS property_type,
                       size(toFloatList(n.embedding)) AS dimensions ORDER BY id''',
                ids=[f'{TEST_ID}:{i}' for i in range(len(QUESTIONS))]).data()
            if len(saved) != len(QUESTIONS) or any(row['dimensions'] != DIMENSIONS
                    or row['property_type'].replace(' NOT NULL', '') != expected_type for row in saved):
                raise RuntimeError('Committed embedding verification failed')
            print(json.dumps({'status': 'passed', 'model': MODEL, 'nodes': saved}, indent=2))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as exc:
        # Provider/driver errors can echo configuration. Redact known credentials
        # and API-key-shaped strings before emitting a bounded diagnostic.
        message = str(exc)
        for name in ('OPENAI_API_KEY', 'AURA_PASSWORD', 'AURA_URI', 'AURA_USERNAME'):
            secret = os.environ.get(name)
            if secret:
                message = message.replace(secret, '[REDACTED]')
        message = re.sub(r'sk-[A-Za-z0-9_-]+', '[REDACTED]', message)
        code = getattr(exc, 'code', None)
        print('Embedding smoke test failed: ' + (code or type(exc).__name__), file=sys.stderr)
        print(message[:1600], file=sys.stderr)
        sys.exit(1)
