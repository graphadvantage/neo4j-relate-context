import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import ELK from 'elkjs/lib/elk.bundled.js';
import { reportConsoleError } from './diagnostics';
import { topologyLayoutInput } from './topology-layout';
import { topologyGraph, topologyView, type Box, type CaptureEvent as Event, type Snapshot } from './topology-model';
export type { Snapshot } from './topology-model';
const elk = new ELK();

function GreenDots({path,activity,x,y}: {path: string; activity: string; x: number; y: number}) {
  const group=useRef<SVGGElement>(null);
  const animations=useRef<(SVGAnimateMotionElement | null)[]>([]);
  const count=4, duration=1.8;
  useLayoutEffect(()=>{
    // Start motion before paint. Keep uninitialized particles hidden and anchor
    // their local origin at the actual edge source, never the canvas origin.
    group.current?.setAttribute('visibility','hidden');
    try {
      animations.current.forEach((animation,index)=>{
        if(!animation) throw Error('Missing animation element');
        animation.beginElementAt(-index*duration/count);
      });
      group.current?.setAttribute('visibility','visible');
    } catch {
      reportConsoleError('Topology animation','Could not initialize path particles.');
    }
    return ()=>group.current?.setAttribute('visibility','hidden');
  },[path,activity]);
  return <g ref={group} transform={`translate(${x},${y})`} visibility="hidden" aria-hidden="true">{Array.from({length:count},(_,index)=><circle key={index} r="3.5" className="flow-particle" fill="#39ff14">
    <animateMotion ref={element=>{animations.current[index]=element as SVGAnimateMotionElement | null;}} path={path} dur={`${duration}s`} calcMode="paced" repeatCount="indefinite" begin="indefinite"/>
  </circle>)}</g>;
}

export function Topology({ snapshot }: { snapshot: Snapshot }) {
  const [agent, setAgent] = useState(''), [project, setProject] = useState('');
  const [history, setHistory] = useState(false);
  const [copyStatus, setCopyStatus] = useState('');
  const [clearingErrors, setClearingErrors] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [layout, setLayout] = useState<{ positions: Map<string, Box>; width: number; height: number; structure: string }>({ positions: new Map(), width: 1200, height: 380, structure: '' });
  const [layoutError, setLayoutError] = useState(false);
  const [clock, setClock] = useState(Date.now());
  useEffect(() => { const timer = window.setInterval(() => setClock(Date.now()), 1000); return () => window.clearInterval(timer); }, []);
  const { events, records } = useMemo(() => topologyView(snapshot, history, agent, project, clock), [snapshot, history, agent, project, clock]);
  const diagram = topologyGraph(records, snapshot.enterprise, snapshot.replication.state, clock);
  const activeNodes = new Set(diagram.edges.filter(e => e.active && e.activityId).flatMap(e => [...e.sources, ...e.targets]));
  const structure = JSON.stringify({ nodes: diagram.boxes.map(b => b.id).sort(), edges: diagram.edges.map(e => e.id).sort() });
  useEffect(() => {
    let canceled = false;
    elk.layout(topologyLayoutInput(diagram.boxes, diagram.edges)).then(result => { if (!canceled) { setLayout({ positions: new Map((result.children || []).map(n => [n.id, { id: n.id, title: '', subtitle: '', kind: '', x: n.x, y: n.y }])), width: result.width || 1200, height: result.height || 380, structure }); setLayoutError(false); } }).catch(() => { if (!canceled) {setLayoutError(true);reportConsoleError('Topology layout','Could not lay out the graph.');} });
    return () => { canceled = true; };
    // Counts and response statuses don't move the topology.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [structure]);
  const requests = events.filter(e => e.kind === 'request');
  const detail = requests.find(e => e.id === selected);
  const response = detail ? events.find(e => e.kind === 'response' && e.call_id === detail.call_id) : undefined;
  const state = (e: Event) => events.find(r => r.kind === 'response' && r.call_id === e.call_id);
  return <main className="topology">
    <div className="section-heading"><div><div className="eyebrow">OBSERVE THE FLOW</div><h1>Context, in motion.</h1><p>{history ? 'Historical capture routes, including disconnected servers.' : 'Current connections → agent databases → enterprise context.'}</p></div><div className="filters"><label>View<select aria-label="Topology view" value={history ? 'history' : 'current'} onChange={e => {setHistory(e.target.value === 'history'); setAgent(''); setProject('');}}><option value="current">Current connections</option><option value="history">Capture history</option></select></label><label>Agent<select aria-label="Filter agent" value={agent} onChange={e => setAgent(e.target.value)}><option value="">All agents</option>{[...new Set((history ? snapshot.events : snapshot.routes || []).map(e => e.agent || 'Unspecified'))].sort().map(a => <option key={a}>{a}</option>)}</select></label><label>Project<select aria-label="Filter project" value={project} onChange={e => setProject(e.target.value)}><option value="">All projects</option>{[...new Set((history ? snapshot.events : snapshot.routes || []).map(e => e.project || 'Unspecified'))].sort().map(p => <option key={p}>{p}</option>)}</select></label></div></div>
    <div className="metrics"><div><strong>{new Set(records.map(e => e.host)).size}</strong><span>Observed hosts</span></div><div><strong>{new Set(records.map(e => JSON.stringify([e.host,e.agent,e.server]))).size}</strong><span>MCP sources</span></div><div><strong>{requests.length}</strong><span>Tool requests saved</span></div><div><strong>{requests.filter(e => e.cypher).length}</strong><span>Cypher submissions saved</span></div><div><strong className="replication">{snapshot.replication.state}</strong><span>Aura snapshot replication</span></div></div>
    {snapshot.errors.length > 0 && <div className="notice error" role="alert">Capture backlog: {snapshot.errors.map(e => `${e.file} at byte ${e.offset} (${e.error})`).join('; ')}. The collector will retry; offsets advance only after persistence.</div>}
    {!!snapshot.error_history?.length && <details className="error-log" open><summary>Error log · {snapshot.error_history.length} retained incidents</summary>
      <div className="error-log-actions"><button onClick={()=>{void navigator.clipboard.writeText(JSON.stringify(snapshot.error_history,null,2)).then(()=>setCopyStatus('Copied'),()=>setCopyStatus('Copy unavailable; use Download log'));}}>Copy errors</button><button disabled={clearingErrors} onClick={async()=>{
        setClearingErrors(true);
        try {
          const response=await fetch('/api/errors/clear',{method:'POST'});
          if(!response.ok) throw Error('Could not clear the log; try again.');
          const result=await response.json();
          setCopyStatus(result.active ? 'History cleared; active errors retained.' : 'Log cleared.');
        } catch(error) {setCopyStatus((error as Error).message);reportConsoleError('Error log', (error as Error).message);}
        finally {setClearingErrors(false);}
      }}>{clearingErrors ? 'Clearing…' : 'Clear log'}</button><a href="/api/errors/log" download>Download log</a><span role="status">{copyStatus}</span></div>
      <div className="error-log-entries">{snapshot.error_history.map(e=><div key={e.id}><time dateTime={e.first_seen}>{new Date(e.first_seen).toLocaleString()}</time> · {e.active ? 'Retrying' : e.resolved_at ? 'Recovered' : 'Reported'} · {e.source || 'Capture'}<pre>{e.error}{e.file ? `\n${e.file}${e.offset !== undefined ? ` at byte ${e.offset}` : ''}` : ''}</pre></div>)}</div>
    </details>}
    <section className="diagram-panel"><div className="panel-heading"><h2>Harvesting topology</h2><span>ELK layout · latest {snapshot.window.toLocaleString()} saved events</span></div>
      {records.length === 0 && <div className="empty"><h2>Waiting for discovered MCP connections.</h2><p>Reconnect a wrapped server to register its agent and database automatically.</p><code>relate-tap --name neo4j-mcp -- neo4j-mcp</code><p>Older server names remain available under Capture history.</p></div>}
      <div className="diagram-scroll">{layoutError ? <div role="alert">Could not lay out the graph. Tool activity remains available below.</div> : <svg role="img" aria-label="Live context harvesting topology" viewBox={`0 0 ${layout.width} ${Math.max(layout.height, 240)}`} style={{ minWidth: Math.min(layout.width, 1050), height: 960 }}>
        <defs><marker id="arrow" markerWidth="7" markerHeight="7" refX="6" refY="3.5" orient="auto"><path d="M0 0 L7 3.5 L0 7" fill="#4a6a70"/></marker></defs>
        {diagram.edges.map(e => { const a = layout.positions.get(e.sources[0]), b = layout.positions.get(e.targets[0]); if (layout.structure !== structure || !a || !b || ![a.x,a.y,b.x,b.y].every(Number.isFinite)) return null; const x = a.x! + 208, y = a.y! + 38, bx = b.x!, by = b.y! + 38; const path=`M${x} ${y} C${(x+bx)/2} ${y},${(x+bx)/2} ${by},${bx} ${by}`; return <g key={e.id} data-flow-active={!!e.active} data-flow-target={e.targets[0]}><path className={'flow-line ' + (e.active ? 'hot' : e.disabled ? 'disabled' : '')} d={path} fill="none" markerEnd="url(#arrow)"><title>{e.active ? 'Observed activity' : e.disabled ? 'Destination configured; remote push not confirmed' : 'Capture route'}</title></path>{e.active && e.activityId && <GreenDots key={e.activityId} path={`M0 0 C${(bx-x)/2} 0,${(bx-x)/2} ${by-y},${bx-x} ${by-y}`} x={x} y={y} activity={e.activityId}/>}</g>; })}
        {diagram.boxes.map(b => { const p = layout.positions.get(b.id); return layout.structure === structure && p && <g key={b.id} transform={`translate(${p.x},${p.y})`} data-node-active={activeNodes.has(b.id)} className={'graph-node ' + b.kind + (activeNodes.has(b.id) ? ' active' : '')}><title>{b.title} · {b.subtitle}</title><rect width="208" height="76" rx="9"/><circle cx="18" cy="23" r="4"/><text x="32" y="28" className="node-title">{b.title.length > 28 ? b.title.slice(0, 27) + '…' : b.title}</text><text x="16" y="54" className="node-subtitle">{b.subtitle.length > 32 ? b.subtitle.slice(0,31) + '…' : b.subtitle}</text></g>; })}
      </svg>}</div>
      <div className="diagram-key"><span><i className="dot" style={{background:'#39ff14'}}/>Active capture path</span><span>Latest MCP path per agent · Aura pushes animate independently</span><span>Local writes and pushes use observed commit status.</span></div>
    </section>
    <div className="activity-grid"><section className="activity"><div className="panel-heading"><h2>Tool activity</h2><span>{requests.length > 100 ? 'Latest 100 requests' : `${requests.length} requests in window`}</span></div><div className="activity-list">{requests.slice(-100).reverse().map(e => { const r = state(e); return <button key={e.id} className={'activity-row ' + (selected === e.id ? 'selected' : '')} onClick={() => setSelected(e.id)}><span className={'event-icon ' + (r?.status?.includes('error') ? 'failed' : '')}>{e.cypher ? '⌘' : '↗'}</span><span className="activity-name"><strong>{e.tool || 'Tool call'}</strong><small>{e.agent || 'Unknown agent'} / {e.server} · {e.project || 'Unspecified project'}</small></span><span className="event-status">{r?.status?.replaceAll('_',' ') || 'Awaiting response'}<small>{new Date(e.timestamp).toLocaleTimeString()}</small></span></button>; })}{requests.length === 0 && <p className="muted padded">Tool calls will appear here as the collector saves them.</p>}</div></section>
    <section className="details"><div className="panel-heading"><h2>Capture detail</h2><span>Submitted transport evidence</span></div>{detail ? <div className="detail-body"><div className="detail-meta"><span>{detail.server} / {detail.tool}</span><span>{detail.username} · OS identity</span></div><p className="muted">{response?.duration_ms !== undefined ? `${response.duration_ms.toFixed(1)} ms transport round trip` : 'Response not observed in this window'}{response?.row_count != null ? ` · ${response.row_count} rows reported by tool` : ''}</p>{detail.redacted && <div className="notice">Secrets were redacted in the stored copy. The original MCP bytes were forwarded unchanged.</div>}<h3>Cypher submitted</h3><pre>{detail.cypher || 'No Cypher recognized in this tool’s arguments.'}</pre><h3>Parameters</h3><pre>{JSON.stringify(detail.parameters ?? {}, null, 2)}</pre><details><summary>Full captured request</summary><pre>{JSON.stringify(detail, null, 2)}</pre></details><details><summary>Captured response</summary><pre>{response ? JSON.stringify(response, null, 2) : 'Pending or outside retained event window'}</pre></details><p className="muted">A response confirms transport delivery. Queries generated inside the MCP server require database-side instrumentation.</p></div> : <div className="detail-empty"><span>↖</span><p>Select a tool call to inspect its Cypher, parameters and response.</p></div>}</section></div>
    <footer>Current connections use a live registration heartbeat. History retains up to {snapshot.window.toLocaleString()} events here; the full history remains in Relate. Agent identity is discovered from MCP or explicitly overridden.</footer>
  </main>;
}
