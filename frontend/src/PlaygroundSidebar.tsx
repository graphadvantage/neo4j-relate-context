import { useEffect, useState } from 'react';
import type { GraphStyleRule } from '@neo4j-ndl/react-graph';
import { engine } from '../vendor/relate-web/lib/engine';
import { identifier, schemaQueries } from './playground-schema';
import { reportConsoleError } from './diagnostics';

type Schema = {nodes: number; relationships: number; labels: string[]; types: string[]; properties: string[]};
type Appearance = {color: string; size: number; caption: string};
export function PlaygroundSidebar({source,ready,onQuery,onStyles}: {source:string;ready:boolean;onQuery:(query:string)=>void;onStyles:(rules:GraphStyleRule[])=>void}) {
  const [schema,setSchema]=useState<Schema|null>(null), [error,setError]=useState(''), [loading,setLoading]=useState(false);
  const [refresh,setRefresh]=useState(0), [target,setTarget]=useState('nodes');
  const [styles,setStyles]=useState<Record<string,Appearance>>({});
  useEffect(()=>{
    if(!ready) return;
    let canceled=false;
    setLoading(true);setSchema(null);setError('');
    void Promise.all(Object.entries(schemaQueries).map(async([key,query])=>{
      const result=await engine.execute(query,null,source);
      if(result.error) throw Error(result.error);
      return [key,key==='nodes'||key==='relationships' ? Number(result.rows[0]?.value ?? 0) : [...new Set(result.rows.map(r=>String(r.value ?? '')).filter(Boolean))].sort()];
    })).then(entries=>{if(!canceled) setSchema(Object.fromEntries(entries) as Schema);})
      .catch(e=>{if(!canceled){setError((e as Error).message);reportConsoleError('Playground schema',(e as Error).message);}})
      .finally(()=>{if(!canceled) setLoading(false);});
    return ()=>{canceled=true;};
  },[source,ready,refresh]);
  function update(change:Partial<Appearance>) {
    const next={...styles,[target]:{...appearance,...change}};
    setStyles(next);
    onStyles(Object.entries(next).sort(([a],[b])=>Number(a.includes(':'))-Number(b.includes(':'))).map(([key,value])=>({
      match:key==='nodes'?{label:null}:key==='relationships'?{reltype:null}:key.startsWith('label:')?{label:key.slice(6)}:{reltype:key.slice(5)},
      apply:{color:{value:value.color},...(key==='relationships'||key.startsWith('type:')?{width:{value:value.size}}:{size:{value:value.size}}),
        ...(value.caption==='auto'?{}:{captions:value.caption==='none'?{isDisabled:true}:{text:[{value:value.caption==='type'?{useType:true}:{property:value.caption}}]}})},
    })));
  }
  const isRelationship=target==='relationships'||target.startsWith('type:');
  const appearance=styles[target] ?? {color:isRelationship?'#8da3b1':'#64b5db',size:isRelationship?2:25,caption:'auto'};
  return <aside className="playground-sidebar" aria-label="Database schema and graph styling">
    <div className="panel-heading"><h2>Database schema</h2><button onClick={()=>setRefresh(n=>n+1)} disabled={loading}>Refresh</button></div>
    {loading && <p role="status">Loading schema…</p>}
    {error && <p role="alert" className="schema-error">{error}</p>}
    {schema && <>
      <section><h3>Node labels <span>({schema.nodes.toLocaleString()} nodes)</span></h3><div className="schema-items">{schema.labels.map(label=><button key={label} onClick={()=>onQuery(`MATCH (n:${identifier(label)}) RETURN n LIMIT 250`)}>{label}</button>)}{!schema.labels.length && <span>No labels</span>}</div></section>
      <section><h3>Relationships <span>({schema.relationships.toLocaleString()})</span></h3><div className="schema-items">{schema.types.map(type=><button key={type} onClick={()=>onQuery(`MATCH p=()-[:${identifier(type)}]->() RETURN p LIMIT 250`)}>{type}</button>)}{!schema.types.length && <span>No relationships</span>}</div></section>
      <section><h3>Properties</h3><div className="schema-items">{schema.properties.map(key=><button key={key} onClick={()=>onQuery(`MATCH (n) WHERE n.${identifier(key)} IS NOT NULL RETURN n LIMIT 250`)}>{key}</button>)}{!schema.properties.length && <span>No properties</span>}</div></section>
    </>}
    <section className="graph-styling"><h3>Graph styling</h3>
      <label>Apply to<select aria-label="Style target" value={target} onChange={e=>setTarget(e.target.value)}><option value="nodes">All nodes</option><option value="relationships">All relationships</option>{schema?.labels.map(l=><option key={'label:'+l} value={'label:'+l}>{l}</option>)}{schema?.types.map(t=><option key={'type:'+t} value={'type:'+t}>Relationship · {t}</option>)}</select></label>
      <label>Color<input aria-label="Graph color" type="color" value={appearance.color} onInput={e=>update({color:e.currentTarget.value})} onChange={e=>update({color:e.target.value})}/></label>
      <label>{isRelationship?'Line width':'Node size'} · {appearance.size}<input aria-label={isRelationship?'Relationship width':'Node size'} type="range" min={isRelationship?1:10} max={isRelationship?10:60} value={appearance.size} onChange={e=>update({size:Number(e.target.value)})}/></label>
      <label>Caption<select aria-label="Graph caption" value={appearance.caption} onChange={e=>update({caption:e.target.value})}><option value="auto">Automatic</option><option value="none">Hidden</option><option value="type">Label / type</option>{schema?.properties.filter(p=>!['auto','none','type'].includes(p)).map(p=><option key={p} value={p}>{p}</option>)}</select></label>
      <button onClick={()=>{setStyles({});onStyles([]);}}>Reset styling</button>
    </section>
  </aside>;
}
