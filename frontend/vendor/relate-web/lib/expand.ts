// Console adaptation: expand against the selected backend, preserving Aura element IDs.
import { engine } from './engine';
import { forEachEntity, nodeId } from './extract-graph';
import type { CLNode, CLRelationship, WireResult } from './wire';
export const EXPAND_LIMIT = 100;
export function engineIds(result: WireResult): Map<string, string> {
  const ids = new Map<string, string>();
  forEachEntity(result, n => ids.set(nodeId(n), String(n.elementId ?? n.id)), () => {});
  return ids;
}
export async function expandFromEngine(id: string, _drawn: string[]) {
  const aura = engine.kind === 'aura';
  const result = await engine.execute(`MATCH (n)-[r]-(m) WHERE ${aura ? 'elementId(n)' : 'id(n)'} = $id RETURN n,r,m LIMIT ${EXPAND_LIMIT}`,
    JSON.stringify({ id: aura ? id : Number(id) }));
  const nodes = new Map<string, CLNode>(), rels = new Map<string, CLRelationship>();
  forEachEntity(result, n => nodes.set(nodeId(n), n), r => rels.set(String(r.elementId ?? r.id), r));
  return { nodes: [...nodes.values()], rels: [...rels.values()], truncated: result.rows.length >= EXPAND_LIMIT };
}
