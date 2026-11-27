export const schemaQueries = {
  nodes: 'MATCH (n) RETURN count(n) AS value',
  relationships: 'MATCH ()-[r]->() RETURN count(r) AS value',
  labels: 'MATCH (n) UNWIND labels(n) AS value RETURN DISTINCT value ORDER BY value LIMIT 1000',
  types: 'MATCH ()-[r]->() RETURN DISTINCT type(r) AS value ORDER BY value LIMIT 1000',
  properties: 'MATCH (n) UNWIND keys(n) AS value RETURN DISTINCT value UNION MATCH ()-[r]->() UNWIND keys(r) AS value RETURN DISTINCT value',
};
export const identifier = (name: string) => '`' + name.replaceAll('`','``') + '`';
export const defaultQueries = [
  {name:'All context',query:'MATCH p = (n)-[* 1..5]-() RETURN p LIMIT 100'},
  {name:'See messages',query:'MATCH p = (n)-[:NEXT *1..]-() RETURN p LIMIT 250'},
  {name:'Get Cyphers',query:'MATCH p = (n:ContextMessage)-[]-() WHERE n.cypher IS NOT NULL RETURN p LIMIT 250'},
  {name:'See Cowork',query:`MATCH p = (h:CaptureHost)--(a1:CaptureConnection)-[:IN_PROJECT]-(cp:ContextProject)-[:IN_PROJECT]-(a2:CaptureConnection)--(h)
WHERE a1.agent = "codex" AND a2.agent = "claude-code"
MATCH p1 = (cp)-[:HAS_SESSION]-(s:ContextSession) WHERE NOT s.source = "claude-desktop"
MATCH p2 = (s)-[:HAS_MESSAGE]->(m0:ContextMessage)-[:NEXT *1..15]->(m:ContextMessage) WHERE NOT (m0)<-[:NEXT]-()
RETURN p,p1,p2 LIMIT 250`},
  {name:'Capture graph',query:'MATCH p=(:CaptureConnection)-[:HAS_EVENT]->(:CaptureEvent) RETURN p LIMIT 50'},
];
