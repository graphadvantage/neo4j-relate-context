import type { Box, Edge } from './topology-model';

export function topologyLayoutInput(boxes: Box[], edges: Edge[]) {
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
