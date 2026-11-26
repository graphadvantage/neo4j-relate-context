import { useEffect, useRef, useState } from 'react';
import { QueryWorkspace } from '../vendor/relate-web/components/QueryWorkspace';
import type { ScriptRun } from '../vendor/relate-web/lib/engine';
import { engine } from '../vendor/relate-web/lib/engine';
import { Topology, type Snapshot } from './Topology';
import { reportConsoleError, flushConsoleErrors } from './diagnostics';

type Source = { id: string; name: string; kind: string };
const initial = 'MATCH (n) RETURN n LIMIT 100';
export function App() {
  const [tab, setTab] = useState('topology');
  const [snapshot, setSnapshot] = useState<Snapshot>({ events: [], errors: [], window: 1000, replication: { state: 'disabled' } });
  const [live, setLive] = useState(false);
  const [sources, setSources] = useState<Source[]>([]);
  const [source, setSource] = useState('capture');
  const [query, setQuery] = useState(initial), [params, setParams] = useState('{}');
  const [run, setRun] = useState<ScriptRun | null>(null), [error, setError] = useState<string | null>(null);
  const [elapsed, setElapsed] = useState<number | null>(null), [running, setRunning] = useState(false);
  const [view, setView] = useState<'graph'|'table'|'json'>('graph');
  const [history, setHistory] = useState<string[]>([]), [truncated, setTruncated] = useState(false);
  const [timings, setTimings] = useState<unknown>(null);
  const generation = useRef(0);
  useEffect(() => {
    const refresh = () => fetch('/api/sources').then(async r => { if (!r.ok) throw Error('Cannot load sources'); setSources(await r.json()); }).catch(e => {setError(e.message); reportConsoleError('Source inventory', e.message);});
    void refresh();
    const timer = window.setInterval(refresh, 5000);
    const feed = new EventSource('/api/events');
    feed.onmessage = e => {
      try {setSnapshot(JSON.parse(e.data));setLive(true);void flushConsoleErrors();}
      catch {reportConsoleError('Live topology', 'Invalid event-stream snapshot');}
    };
    feed.onerror = () => {setLive(false);reportConsoleError('Live topology', 'Collector connection interrupted; reconnecting.');};
    return () => { feed.close(); window.clearInterval(timer); };
  }, []);
  function changeSource(id: string) {
    generation.current++;
    engine.source = id; engine.kind = sources.find(s => s.id === id)?.kind ?? 'relate';
    setSource(id); setRun(null); setError(null); setElapsed(null); setRunning(false); setTimings(null); setTruncated(false);
  }
  async function execute(text: string) {
    const current = ++generation.current;
    setRunning(true); setError(null); setQuery(text); setRun(null); setTruncated(false); setTimings(null);
    try {
      const result = await engine.execute(text, params);
      if (current !== generation.current) return;
      setRun({ last: result, stats: result.stats, statements: 1 }); setElapsed(result.elapsed_ms);
      setTruncated(result.truncated); setTimings(result.timings ?? null);
      setHistory(h => [text, ...h.filter(x => x !== text)].slice(0, 50));
    } catch (e) { if (current === generation.current) {setError((e as Error).message);reportConsoleError('Playground query', (e as Error).message);} }
    finally { if (current === generation.current) setRunning(false); }
  }
  return <div className="console">
    <header><a className="brand" href="/">relate<span> / context</span></a><span className={'live ' + (live ? 'online' : '')}><i/>{live ? 'Collector connected' : 'Reconnecting to collector'}</span></header>
    <nav aria-label="Console views"><button aria-selected={tab === 'topology'} onClick={() => setTab('topology')}>01 <span>Live topology</span></button><button aria-selected={tab === 'playground'} onClick={() => setTab('playground')}>02 <span>Playground</span></button><span className="nav-note">MCP → Relate → Aura</span></nav>
    {tab === 'topology' ? <Topology snapshot={snapshot} /> : <main className="playground">
      <div className="section-heading"><div><div className="eyebrow">EXPLORE THE CAPTURE</div><h1>Query your context.</h1><p>Relate playground · persistent local graphs and enterprise Aura.</p></div>
      <label>Source<select aria-label="Database source" value={source} onChange={e => changeSource(e.target.value)}>{sources.map(s => <option key={s.id} value={s.id}>{s.name} · {s.kind}</option>)}</select></label></div>
      <div className="query-presets">{['MATCH (n) RETURN n LIMIT 100', 'MATCH (q:ContextQuery) RETURN q.cypher, q.parameters, q.timestamp LIMIT 50', 'MATCH p=(:CaptureConnection)-[:HAS_EVENT]->(:CaptureEvent) RETURN p LIMIT 50'].map((q, i) => <button key={q} onClick={() => setQuery(q)}>{['All context','Captured Cypher','Capture graph'][i]}</button>)}<span>Read only · one statement · 1,000 records maximum</span></div>
      <details className="parameters"><summary>Query parameters</summary><textarea aria-label="Query parameters JSON" value={params} onChange={e => setParams(e.target.value)} spellCheck={false}/></details>
      {error && !run && <div className="notice error" role="alert">{error}</div>}
      {truncated && <div className="notice">Result limited to 1,000 records. Add filters or a smaller LIMIT.</div>}
      <div className="workspace"><QueryWorkspace query={query} onQueryChange={setQuery} onRun={execute} engineReady={sources.length > 0} running={running} tab={view} onTabChange={setView} run={run} error={null} elapsed={elapsed} paramsNote={null} history={history}/></div>
      {timings !== null && <details className="parameters"><summary>Relate query timings (not model telemetry)</summary><pre>{JSON.stringify(timings, null, 2)}</pre></details>}
      <footer>Adapted from the <a href="https://github.com/relatedb/relate-web" target="_blank" rel="noreferrer">Relate web playground</a> · Apache 2.0</footer>
    </main>}
  </div>;
}
