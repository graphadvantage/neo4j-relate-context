import test from 'node:test';
import assert from 'node:assert/strict';
import ELK from 'elkjs/lib/elk.bundled.js';
import {topologyGraph} from '../src/topology-model.ts';
import {topologyLayoutInput} from '../src/topology-layout.ts';

test('ELK orders whole routes as Codex, Claude Code, then Claude Desktop',async()=>{
 const records=['codex','claude-desktop','claude-code'].flatMap(agent=>
  (agent==='claude-desktop' ? ['risk','fraud','robot-twin'] : ['relate-memory-mcp']).map(server=>({
   id:agent+server,kind:'route',timestamp:new Date().toISOString(),connection:agent+server,
   host:'laptop',username:'user',agent,project:agent==='claude-desktop' ? undefined : 'shared-repository',
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
