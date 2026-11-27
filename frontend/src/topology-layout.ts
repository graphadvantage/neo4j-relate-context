import type { Box, Edge } from './topology-model';

export function topologyLayoutInput(boxes: Box[], edges: Edge[]) {
  // Return lanes use these ELK node positions but must not turn the layered
  // harvesting graph into a cycle or change agent/repository ordering.
  edges=edges.filter(edge=>edge.kind!=='recall');
  const kinds=new Map(boxes.map(box=>[box.id,box.kind]));
  const order=['codex','claude-code','claude-desktop'];
  const ranks=new Map(boxes.filter(box=>box.kind==='agent' && order.includes(box.title)).map(box=>[box.id,order.indexOf(box.title)]));
  const priority=(id: string)=>ranks.get(id) ?? order.length;
  // Keep each downstream branch tied to its own agent when repositories converge.

  let changed=true;
  while(changed) {
    changed=false;
    for(const edge of edges) {
      const rank=edge.actor ? priority(edge.actor) : Math.min(...edge.sources.map(priority));
      for(const id of edge.targets) {
        if(kinds.get(id)!=='aura' && rank<priority(id)) {ranks.set(id,rank);changed=true;}
      }
    }
  }
  const children=[...boxes].sort((a,b)=>priority(a.id)-priority(b.id) || a.id.localeCompare(b.id));
  const edgePriority=(edge: Edge)=>edge.actor ? priority(edge.actor) : Math.min(...[...edge.sources,...edge.targets].map(priority));
  const orderedEdges=[...edges].sort((a,b)=>edgePriority(a)-edgePriority(b) || a.id.localeCompare(b.id));
  return {
    id:'root',width:0,height:0,
    layoutOptions:{
      'elk.algorithm':'layered', 'elk.direction':'RIGHT',
      'elk.spacing.nodeNode':'40', 'elk.layered.spacing.nodeNodeBetweenLayers':'64',
      'elk.padding':'[top=48,left=32,bottom=48,right=32]',
      'elk.layered.nodePlacement.favorStraightEdges':'false',
      'elk.layered.nodePlacement.bk.fixedAlignment':'BALANCED',
      'elk.layered.considerModelOrder.strategy':'NODES_AND_EDGES',
      'elk.layered.crossingMinimization.forceNodeModelOrder':'true',
    },
    children:children.map(({id})=>({id,width:208,height:76})),
    edges:orderedEdges.map(({id,sources,targets})=>({id,sources,targets})),
  };
}

export function recallLaneHeight(actor: string, boxes: Box[], edges: Edge[], positions: Map<string,Box>) {
  const owners=new Map<string,Set<string>>();
  for(const edge of edges) {
    if(edge.kind==='recall' || !edge.actor) continue;
    for(const id of [...edge.sources,...edge.targets]) {
      if(!owners.has(id)) owners.set(id,new Set());
      owners.get(id)!.add(edge.actor);
    }
  }
  const tops=boxes.filter(box=>box.kind!=='host' && box.kind!=='aura' && owners.get(box.id)?.has(actor)
    // A shared repository can sit in another agent's band. Its return lane
    // follows its own agent/MCP/database rows rather than that shared junction.
    && !(box.kind==='project' && owners.get(box.id)!.size>1))
    .map(box=>positions.get(box.id)?.y).filter((y): y is number=>Number.isFinite(y));
  return tops.length ? Math.min(...tops)-20 : 12;
}

export function padRecallRows(boxes: Box[], edges: Edge[], positions: Map<string,Box>, padding=24) {
  // Insert space only before recall bands. Nodes sharing an ELK row receive
  // the same offset; ordinary rows within each agent's branch stay unchanged.
  const actors=[...new Set(edges.filter(edge=>edge.kind==='recall').map(edge=>edge.actor!))];
  const cuts=[...new Set(actors.map(actor=>recallLaneHeight(actor,boxes,edges,positions)+20))];
  return {
    positions:new Map([...positions].map(([id,box])=>[id,{...box,
      y:box.y!+padding*cuts.filter(cut=>box.y!>=cut).length}])),
    extraHeight:padding*cuts.length,
  };
}

export function recallLanePath(from: Box, to: Box, laneY: number) {
  const x=from.x!+208, y=from.y!+38, bx=to.x!+104, by=to.y!;
  // Each lane sits above its own agent band, then enters the top center.
  const radius=10, outside=x+32;
  const direction=laneY>=y ? 1 : -1, startCorner=laneY-direction*radius;
  const path=`M${x} ${y} H${outside-radius} Q${outside} ${y} ${outside} ${y+direction*radius} V${startCorner} Q${outside} ${laneY} ${outside-radius} ${laneY} H${bx+radius} Q${bx} ${laneY} ${bx} ${laneY+radius} V${by}`;
  const relative=`M0 0 H${outside-radius-x} Q${outside-x} 0 ${outside-x} ${direction*radius} V${startCorner-y} Q${outside-x} ${laneY-y} ${outside-radius-x} ${laneY-y} H${bx+radius-x} Q${bx-x} ${laneY-y} ${bx-x} ${laneY+radius-y} V${by-y}`;
  return {x,y,path,relative};
}
