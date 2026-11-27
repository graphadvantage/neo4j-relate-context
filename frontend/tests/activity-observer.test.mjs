import test from 'node:test';
import assert from 'node:assert/strict';
import { ActivityObserver } from '../src/activity-observer.ts';
import { topologyView, topologyGraph } from '../src/topology-model.ts';

const now=Date.now();
const at=n=>new Date(n).toISOString();
const route={connection:'a',server:'memory',agent:'codex',host:'laptop',username:'user',project:'repo',database:'codex.rl',last_seen:at(now),active:true,identity_source:'mcp_client',persisted_offset:100,database_state:'ready',replication:{state:'synced',push_id:'old',last_pushed_at:at(now-60000)}};
const request={id:'request',connection:'a',server:'memory',host:'laptop',username:'user',kind:'request',call_id:'call',timestamp:at(now-20000)};
const response={...request,id:'response',kind:'response',timestamp:at(now-19000)};
const data={revision:1,events:[request,response],routes:[{...route,commits:[{id:'commit',call_id:'call',kind:'request',at:at(now-18000)}]}],enterprise:{name:'enterprise',kind:'aura'},replication:{state:'synced'}};
const empty={...data,revision:0,events:[],routes:[]};
function graph(snapshot,observer,time=now) {
 const received=observer.observe(snapshot,time);
 return topologyGraph(topologyView(snapshot,false,'','',time).records,snapshot.enterprise,snapshot.replication.state,time,received);
}

test('initial snapshot does not replay historical completed activity',()=>{
 assert(!graph(data,new ActivityObserver()).edges.some(e=>e.active));
});

test('delayed completed calls and local commits remain visible from receipt, without polling replay',()=>{
 const observer=new ActivityObserver();
 observer.observe({...empty,revision:undefined},now-1000); // React placeholder
 observer.observe({...empty,routes:[route]},now-500); // First real snapshot seeds idle push receipt
 const first=graph(data,observer);
 assert(first.edges.filter(e=>e.active).length>=4);
 assert(first.edges.some(e=>e.active && e.targets[0].startsWith('database:')));
 assert(!first.edges.some(e=>e.active && e.targets[0]==='aura'));
 assert(graph(data,observer,now+5000).edges.some(e=>e.active));
 assert(!graph(data,observer,now+6001).edges.some(e=>e.active));
 // Reconnect with a new snapshot object but the same evidence IDs.
 assert(!graph({...data},observer,now+7000).edges.some(e=>e.active));
});

test('new MCP call switches the upstream path immediately',()=>{
 const observer=new ActivityObserver();
 observer.observe(empty,now-1000);
 graph(data,observer);
 const next={...data,events:[...data.events,{...request,id:'next',call_id:'next',connection:'b',server:'other',timestamp:at(now)}],routes:[...data.routes,{...route,connection:'b',server:'other'}]};
 const result=graph(next,observer,now+100);
 const oldServer=result.boxes.find(b=>b.kind==='server' && b.title==='memory').id;
 const newServer=result.boxes.find(b=>b.kind==='server' && b.title==='other').id;
 assert(!result.edges.some(e=>e.active && (e.sources[0]===oldServer || e.targets[0]===oldServer)));
 assert(result.edges.some(e=>e.active && e.targets[0]===newServer));
});

test('Aura receipt visibility is independent and retrying does not animate an old success',()=>{
 const observer=new ActivityObserver();
 graph(data,observer);
 const updated=replication=>({...data,routes:[{...data.routes[0],replication}]});
 assert(graph(updated({state:'pushing',push_id:'new'}),observer).edges.some(e=>e.active && e.targets[0]==='aura'));
 const complete=updated({state:'synced',push_id:'new',last_pushed_at:at(now-10000)});
 assert(graph(complete,observer).edges.some(e=>e.active && e.targets[0]==='aura'));
 assert(!graph(complete,observer,now+6001).edges.some(e=>e.active && e.targets[0]==='aura'));
 assert(!graph(updated({state:'retrying',push_id:'failed',last_pushed_at:at(now)}),observer).edges.some(e=>e.active && e.targets[0]==='aura'));
});

test('a real empty snapshot initializes observation before the first call',()=>{
 const observer=new ActivityObserver();
 observer.observe(empty,now-1000);
 assert(graph(data,observer).edges.some(e=>e.active && e.targets[0].startsWith('server:')));
});

test('unwrapped memory MCP receipts activate correct agent, survive polling, then expire',()=>{
 const observer=new ActivityObserver();
 observer.observe(empty,now-1000);
 const receipt={id:'recall-1',host:'laptop',agent:'codex',project:'repo',tool:'get_query',started_at:at(now-20000),finished_at:at(now-19000),state:'ready'};
 const snapshot={...data,events:[],recalls:[receipt]};
 const make=time=>topologyGraph(topologyView(snapshot,false,'','',time).records,snapshot.enterprise,'synced',time,observer.observe(snapshot,time),snapshot.recalls);
 assert(make(now).edges.some(e=>e.kind==='recall' && e.active));
 assert(make(now+5000).edges.some(e=>e.kind==='recall' && e.active));
 assert(!make(now+6001).edges.some(e=>e.kind==='recall' && e.active));
 const baseline=new ActivityObserver();const times=baseline.observe(snapshot,now);
 assert(!topologyGraph(topologyView(snapshot,false,'','',now).records,snapshot.enterprise,'synced',now,times,snapshot.recalls).edges.some(e=>e.kind==='recall' && e.active));
});
