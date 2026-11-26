import test from 'node:test';
import assert from 'node:assert/strict';
import ELK from 'elkjs/lib/elk.bundled.js';
import {topologyGraph} from '../src/topology-model.ts';
import {topologyLayoutInput} from '../src/topology-layout.ts';

test('ELK orders whole routes as Codex, Claude Code, then Claude Desktop',async()=>{
 const records=['codex','claude-desktop','claude-code'].flatMap(agent=>
  (agent==='claude-desktop' ? ['risk','fraud','robot-twin'] : ['relate-memory-mcp']).map(server=>({
   id:agent+server,kind:'route',timestamp:new Date().toISOString(),connection:agent+server,
   host:'laptop',username:'user',agent,project:agent==='claude-desktop' ? undefined : agent+'-repository',
   server,database:agent+'.rl',identity_source:'mcp_client',database_state:'ready',
  })));
 const diagram=topologyGraph(records,{name:'enterprise-relate-memory',kind:'aura'},'disabled');
 for(const reverse of [false,true]) {
  const boxes=reverse ? [...diagram.boxes].reverse() : diagram.boxes;
  const edges=reverse ? [...diagram.edges].reverse() : diagram.edges;
  const result=await new ELK().layout(topologyLayoutInput(boxes,edges));
  const positions=new Map(result.children.map(n=>[n.id,n]));
  for(const kind of ['agent','project','server','store']) {
   const ordered=['codex','claude-code','claude-desktop'].map(agent=>
    boxes.filter(b=>b.kind===kind && b.id.includes(agent)).map(b=>positions.get(b.id).y));
   assert(ordered.every(ys=>ys.length),`Each agent route includes ${kind}`);
   for(let i=0;i<ordered.length-1;i++) {
    assert(Math.max(...ordered[i])<Math.min(...ordered[i+1]),`${kind} follows requested route order`);
   }
  }
  assert(result.children.every(n=>Number.isFinite(n.x) && Number.isFinite(n.y)));
 }
});


test('ELK preserves agent branch order after convergence on a shared repository',async()=>{
 const records=['claude-desktop','claude-code','codex'].map(agent=>({
  id:agent,kind:'route',timestamp:new Date().toISOString(),connection:agent,
  host:'laptop',username:'user',agent,project:agent==='claude-desktop' ? undefined : 'shared-repo',
  server:'memory',database:agent+'.rl',database_state:'ready',
 }));
 const diagram=topologyGraph(records,{name:'enterprise',kind:'aura'},'disabled');
 assert.equal(diagram.boxes.filter(b=>b.kind==='project' && b.title==='shared-repo').length,1);
 const result=await new ELK().layout(topologyLayoutInput(diagram.boxes,diagram.edges));
 const positions=new Map(result.children.map(n=>[n.id,n]));
 for(const kind of ['agent','server','store']) {
  const ys=['codex','claude-code','claude-desktop'].map(agent=>
   positions.get(diagram.boxes.find(b=>b.kind===kind && b.id.includes(agent)).id).y);
  assert(ys[0]<ys[1] && ys[1]<ys[2],`${kind} order survives shared repo convergence`);
 }
 const repo=diagram.boxes.find(b=>b.title==='shared-repo');
 const incoming=diagram.edges.filter(e=>e.targets.includes(repo.id));
 const outgoing=diagram.edges.filter(e=>e.sources.includes(repo.id));
 assert.equal(incoming.length,2);assert.equal(outgoing.length,2);
 assert(result.children.every(n=>Number.isFinite(n.x) && Number.isFinite(n.y)));
});


test('balanced fan-out centers the agent and its database on the source rows',async()=>{
 const records=['alpha','beta','delta','gamma','omega'].map(server=>({
  id:server,kind:'route',timestamp:new Date().toISOString(),connection:server,
  host:'laptop',username:'user',agent:'claude-desktop',server,database:'claude-desktop.rl',database_state:'ready',
 }));
 const graph=topologyGraph(records,{name:'enterprise',kind:'aura'},'disabled');
 const result=await new ELK().layout(topologyLayoutInput(graph.boxes,graph.edges));
 const positions=new Map(result.children.map(n=>[n.id,n]));
 const sourceRows=graph.boxes.filter(b=>b.kind==='server').map(b=>positions.get(b.id).y).sort((a,b)=>a-b);
 const middle=sourceRows[Math.floor(sourceRows.length/2)];
 for(const kind of ['agent','store']) {
  const node=positions.get(graph.boxes.find(b=>b.kind===kind).id);
  assert(Math.abs(node.y-middle)<1,`${kind} aligns with the center source row`);
 }
 for(let i=1;i<sourceRows.length;i++) assert(sourceRows[i]-sourceRows[i-1]>=116-0.01);
});
