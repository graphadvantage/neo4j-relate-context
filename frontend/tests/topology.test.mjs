import test from 'node:test';
import assert from 'node:assert/strict';
import { topologyView, topologyGraph } from '../src/topology-model.ts';

const now=Date.now();
const route={connection:'current',server:'neo4j-mcp-wam',agent:'claude-desktop',host:'laptop',username:'user',database:'claude-desktop.rl',last_seen:new Date(now).toISOString(),active:true,identity_source:'mcp_client',persisted_offset:100,database_state:'ready',replication:{state:'disabled'}};
const old={id:'old',connection:'closed',server:'neo4j-mcp',host:'laptop',username:'user',kind:'request',timestamp:new Date(now-60000).toISOString()};
const current={...old,id:'current',connection:'current',server:'neo4j-mcp-wam'};
const snapshot={events:[old,current],routes:[route],enterprise:{name:'enterprise-relate-memory',kind:'aura'},replication:{state:'disabled'}};

test('current topology uses live metadata and excludes historical names',()=>{
 const view=topologyView(snapshot,false,'','',now);
 assert.deepEqual(view.events.map(e=>e.server),['neo4j-mcp-wam']);
 assert.equal(view.events[0].agent,'claude-desktop');
 const graph=topologyGraph(view.records,snapshot.enterprise,'disabled');
 assert(graph.boxes.some(n=>n.title==='claude-desktop.rl'));
 assert(graph.boxes.some(n=>n.title==='enterprise-relate-memory' && n.subtitle.includes('Aura instance')));
 assert(!graph.boxes.some(n=>n.title==='neo4j-mcp'));
 assert(graph.edges.some(e=>e.targets[0]==='aura' && e.disabled));
 assert.equal(snapshot.events[1].agent,undefined); // Historical evidence is unchanged.
});
test('history remains available and heartbeat expiry removes a dead connection',()=>{
 assert.equal(topologyView(snapshot,true,'','',now).events.length,2);
 assert.equal(topologyView(snapshot,false,'','',now+21000).records.length,0);
});
test('same-named servers on two agents have distinct database routes',()=>{
 const other={...route,connection:'code',agent:'claude-code',database:'claude-code.rl'};
 const view=topologyView({...snapshot,routes:[route,other]},false,'','',now);
 const graph=topologyGraph(view.records,snapshot.enterprise,'disabled');
 assert.equal(graph.boxes.filter(n=>n.kind==='server').length,2);
 assert.equal(graph.boxes.filter(n=>n.kind==='store').length,2);
});

test('switching MCP servers halts the previous path and activates the new one',()=>{
 const a={...route,connection:'a',server:'wam'};
 const b={...route,connection:'b',server:'counterparty-risk'};
 const events=[{...current,connection:'a',server:'wam',call_id:'call-a',timestamp:new Date(now-1000).toISOString()},
   {...current,id:'next',connection:'b',server:'counterparty-risk',call_id:'call-b',timestamp:new Date(now).toISOString()}];
 const view=topologyView({...snapshot,routes:[a,b],events},false,'','',now);
 const graph=topologyGraph(view.records,snapshot.enterprise,'disabled',now);
 const server=name=>graph.boxes.find(n=>n.kind==='server' && n.title===name).id;
 assert(!graph.edges.some(e=>e.targets[0]===server('wam') && e.active));
 assert(graph.edges.some(e=>e.targets[0]===server('counterparty-risk') && e.active));
});

test('local-write dots use committed events and push dots use actual push receipts',()=>{
 const r={...route,commits:[{id:'commit',call_id:'call',kind:'request',at:new Date(now).toISOString()}],
   replication:{state:'pushing',push_id:'push-1',started_at:new Date(now).toISOString()}};
 const event={...current,call_id:'call',timestamp:new Date(now).toISOString()};
 const view=topologyView({...snapshot,routes:[r],events:[event]},false,'','',now);
 const graph=topologyGraph(view.records,snapshot.enterprise,'pushing',now);
 assert(graph.edges.some(e=>e.targets[0].startsWith('database:') && e.active && e.activityId==='commit'));
 assert(graph.edges.some(e=>e.targets[0]==='aura' && e.active && e.activityId==='push-1'));
});

test('finished calls and old push receipts stop animating when idle',()=>{
 const r={...route,replication:{state:'synced',push_id:'old-push',last_pushed_at:new Date(now-20000).toISOString()}};
 const request={...current,call_id:'call',timestamp:new Date(now-20000).toISOString()};
 const response={...request,id:'response',kind:'response',timestamp:new Date(now-10000).toISOString()};
 const view=topologyView({...snapshot,routes:[r],events:[request,response]},false,'','',now);
 const graph=topologyGraph(view.records,snapshot.enterprise,'synced',now);
 assert(!graph.edges.some(e=>e.active));
});


test('repository paths originate at the host and pass through agent then repository',()=>{
 const r={...route,agent:'claude-code',project:'neo4j-semantic-robot',database:'claude-code.rl'};
 const request={...current,call_id:'repo-call',timestamp:new Date(now).toISOString()};
 const view=topologyView({...snapshot,routes:[r],events:[request]},false,'','',now);
 const graph=topologyGraph(view.records,snapshot.enterprise,'disabled',now);
 const id=kind=>graph.boxes.find(n=>n.kind===kind).id;
 for (const [from,to] of [['host','agent'],['agent','project'],['project','server']]) {
  assert(graph.edges.some(e=>e.sources[0]===id(from) && e.targets[0]===id(to) && e.active));
 }
 assert(!graph.edges.some(e=>e.targets[0]===id('host')));
 assert(!graph.edges.some(e=>e.sources[0]===id('project') && e.targets[0]===id('agent')));
});


test('Desktop inserts one project per named MCP source and animates only the active path',()=>{
 const other={...route,connection:'risk',server:'counterparty-risk'};
 const request={...current,call_id:'desktop-call',timestamp:new Date(now).toISOString()};
 const view=topologyView({...snapshot,routes:[route,other],events:[request]},false,'','',now);
 const graph=topologyGraph(view.records,snapshot.enterprise,'disabled',now);
 const projects=graph.boxes.filter(n=>n.kind==='project');
 assert.deepEqual(projects.map(n=>n.title).sort(),['counterparty-risk','neo4j-mcp-wam']);
 for (const project of projects) {
  const server=graph.boxes.find(n=>n.kind==='server' && n.title===project.title);
  const path=graph.edges.find(e=>e.sources[0]===project.id && e.targets[0]===server.id);
  assert(path);
  assert.equal(!!path.active,project.title==='neo4j-mcp-wam');
 }
 assert(!graph.edges.some(e=>e.sources[0].startsWith('agent:') && e.targets[0].startsWith('server:')));
 assert.equal(route.project,undefined); // Display fallback does not change captured attribution.
});

test('Desktop prefers supplied project context over the MCP server name',()=>{
 const r={...route,project:'Risk analysis'};
 const view=topologyView({...snapshot,routes:[r],events:[]},false,'','',now);
 const graph=topologyGraph(view.records,snapshot.enterprise,'disabled',now);
 assert.deepEqual(graph.boxes.filter(n=>n.kind==='project').map(n=>n.title),['Risk analysis']);
 assert(graph.boxes.some(n=>n.kind==='server' && n.title==='neo4j-mcp-wam'));
});
