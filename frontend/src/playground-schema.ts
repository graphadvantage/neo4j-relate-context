export const schemaQueries = {
  nodes: 'MATCH (n) RETURN count(n) AS value',
  relationships: 'MATCH ()-[r]->() RETURN count(r) AS value',
  labels: 'MATCH (n) UNWIND labels(n) AS value RETURN DISTINCT value ORDER BY value LIMIT 1000',
  types: 'MATCH ()-[r]->() RETURN DISTINCT type(r) AS value ORDER BY value LIMIT 1000',
  properties: 'MATCH (n) UNWIND keys(n) AS value RETURN DISTINCT value UNION MATCH ()-[r]->() UNWIND keys(r) AS value RETURN DISTINCT value',
};
export const identifier = (name: string) => '`' + name.replaceAll('`','``') + '`';
export const defaultQueries = [
  {name:'All context',query:'MATCH (n) RETURN n LIMIT 100'},
  {name:'See messages',query:'MATCH p = (n)-[:NEXT *1..]-() RETURN p LIMIT 250'},
  {name:'Get Cyphers',query:'MATCH p = (n:ContextMessage)-[]-() WHERE n.cypher IS NOT NULL RETURN p LIMIT 250'},
  {name:'Captured Cypher',query:'MATCH (q:ContextQuery) RETURN q.cypher, q.parameters, q.timestamp LIMIT 50'},
  {name:'Capture graph',query:'MATCH p=(:CaptureConnection)-[:HAS_EVENT]->(:CaptureEvent) RETURN p LIMIT 50'},
];
