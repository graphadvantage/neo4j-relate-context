import { pushActivityKey, VISIBLE_ACTIVITY_MS, type ActivityTimes } from './activity-observer.ts';
type PushState = {state: string; push_id?: string; started_at?: string; last_pushed_at?: string};
type Commit = {id: string; call_id?: string; kind: string; at: string};
export type CaptureEvent = { id: string; kind: string; timestamp: string; connection: string; host: string; server: string; username: string; agent?: string; project?: string; tool?: string; arguments?: Record<string,unknown>; call_id?: string; cypher?: string; parameters?: unknown; status?: string; duration_ms?: number; row_count?: number; redacted?: boolean; response?: unknown; database?: string; identity_source?: string; persisted_offset?: number; database_state?: string; replication?: PushState; client_info?: {name: string}; commits?: Commit[] };
export type RecallActivity = {id: string; agent: string; host: string; project?: string; tool: string; started_at: string; finished_at?: string; state: string};
export type IntentStatistics = {state: string; groups: {project?: string; agents: string[]; intents: number}[]};
export type Route = { connection: string; server: string; agent: string; host: string; username: string; project?: string; database: string; last_seen: string; active: boolean; identity_source: string; persisted_offset: number; database_state: string; replication: PushState; client_info?: {name: string}; commits?: Commit[] };
export type Diagnostic = {id: string; source?: string; file?: string; offset?: number; error: string; first_seen: string; active: boolean; resolved_at?: string | null};
export type Snapshot = { revision?: number; events: CaptureEvent[]; recalls?: RecallActivity[]; intent_stats?: IntentStatistics; stats?: {groups: {agent?: string; project?: string; requests: number; cyphers: number}[]} | null; error_history?: Diagnostic[]; errors: {file: string; error: string; offset: number}[]; window: number; replication: {state: string}; routes?: Route[]; enterprise?: {name: string; kind: string} | null };
export type Box = { id: string; title: string; subtitle: string; kind: string; x?: number; y?: number };
export type Edge = { id: string; sources: string[]; targets: string[]; active?: boolean; activityId?: string; disabled?: boolean; actor?: string; kind?: 'recall'; detail?: string };

export function isAuraRecall(event: CaptureEvent) {
  const tool=event.tool?.split(/__|\//).at(-1)?.split('.').at(-1);
  return event.kind==='request' && (tool==='get_query' || tool==='get_evidence' ||
    (tool==='recall_context' && typeof event.arguments?.query==='string' && !!event.arguments.query.trim()));
}

export function topologyView(snapshot: Snapshot, history: boolean, agent: string, project: string, now: number) {
  const matches = (e: {agent?: string; project?: string}) => (!agent || (e.agent || 'Unspecified') === agent) && (!project || (e.project || 'Unspecified') === project);
  const routes = (snapshot.routes || []).filter(r => r.active && now-Date.parse(r.last_seen) < 20000);
  const current = new Map(routes.map(r => [r.connection, r]));
  const identities = history ? new Map((snapshot.routes || []).map(r => [r.connection, r])) : current;
  const events = snapshot.events.filter(e => history || current.has(e.connection))
    .map(e => ({...e, ...identities.get(e.connection)})).filter(matches);
  const records = history ? events : [
    ...routes.filter(matches).map(r => ({...r, id: r.connection, kind: 'route', timestamp: r.last_seen})), ...events,
  ];
  return { events, records, routes };
}

export function savedCaptureCounts(snapshot: Snapshot, agent: string, project: string, now=Date.now()) {
  if(snapshot.stats) return snapshot.stats.groups
    .filter(group=>(!agent || (group.agent || 'Unspecified')===agent) && (!project || (group.project || 'Unspecified')===project))
    .reduce((total,group)=>({requests:total.requests+group.requests,cyphers:total.cyphers+group.cyphers}),{requests:0,cyphers:0});
  const requests=topologyView(snapshot,true,agent,project,now).events.filter(e=>e.kind==='request');
  return {requests:new Set(requests.map(e=>e.id)).size,
    cyphers:new Set(requests.filter(e=>e.cypher).map(e=>e.call_id || e.id)).size};
}

export function savedUserIntentCount(snapshot: Snapshot, agent: string, project: string) {
  if(snapshot.intent_stats?.state!=='ready') return null;
  return snapshot.intent_stats.groups.filter(group=>(!project || (group.project || 'Unspecified')===project)
    && (!agent || group.agents.includes(agent) || (agent==='Unspecified' && !group.agents.length)))
    .reduce((total,group)=>total+group.intents,0);
}

export function topologyGraph(records: CaptureEvent[], enterprise: Snapshot['enterprise'], replication: string, now=Date.now(), received?: ActivityTimes, recalls: RecallActivity[]=[]) {
  const boxes = new Map<string, Box>(), edges = new Map<string, Edge>();
  const actorKey = (e: {host: string; agent?: string}) => JSON.stringify([e.host,e.agent || '?']);
  const latest = new Map<string,CaptureEvent>();
  const latestRecall = new Map<string,CaptureEvent>();
  const receipts = new Map<string,RecallActivity>();
  for(const activity of recalls) {
    const key=actorKey(activity), previous=receipts.get(key);
    if(!previous || Date.parse(activity.started_at)>=Date.parse(previous.started_at)) receipts.set(key,activity);
  }
  for (const e of records) {
    if (e.kind === 'request' && (!latest.has(actorKey(e)) || Date.parse(e.timestamp) >= Date.parse(latest.get(actorKey(e))!.timestamp))) latest.set(actorKey(e),e);
    if(isAuraRecall(e) && (!latestRecall.has(actorKey(e)) || Date.parse(e.timestamp)>=Date.parse(latestRecall.get(actorKey(e))!.timestamp))) latestRecall.set(actorKey(e),e);
  }
  const recent = (at?: string, duration=3000) => !!at && now-Date.parse(at) < duration && now-Date.parse(at) >= -2000;
  const visible = (at: string | undefined, key: string | undefined, duration=3000) => {
    const observed=key ? received?.get(key) : undefined;
    return observed!=null ? recent(new Date(observed).toISOString(), VISIBLE_ACTIVITY_MS) : recent(at,duration);
  };
  function node(id: string, title: string, subtitle: string, kind: string) { boxes.set(id, { id, title, subtitle, kind }); return id; }
  function edge(from: string, to: string, active=false, activityId?: string, disabled=false, actor?: string) {
    const id = JSON.stringify([from, to]);
    const previous=edges.get(id);
    edges.set(id, { id, sources: [from], targets: [to], active: active || previous?.active,
      activityId: active ? activityId : previous?.activityId, disabled, actor });
  }
  const aura = enterprise ? node('aura', enterprise.name, 'Aura instance · ' + (replication === 'synced' ? 'last push confirmed' : replication === 'disabled' ? 'replication disabled' : replication), 'aura') : null;
  for (const event of records) {
    const host = node('host:' + event.host, event.host.replace(/\.local$/i, ''), 'Host · ' + event.username, 'host');
    const agentId = actorKey(event);
    const agent = node('agent:' + agentId, event.agent || event.client_info?.name || 'Historical client', event.identity_source === 'mcp_client' ? 'Discovered from MCP client' : event.agent ? 'Configured agent' : event.client_info ? 'Client needs an agent mapping' : 'Identity not recorded in history', 'agent');
    if(aura) {
      const recall=latestRecall.get(agentId), receipt=receipts.get(agentId);
      let active=false, activityId: string | undefined;
      if(receipt && (!recall || Date.parse(receipt.started_at)>=Date.parse(recall.timestamp))) {
        activityId=receipt.id;
        const key='recall:'+receipt.id+(receipt.finished_at ? ':finished' : '');
        active=receipt.finished_at
          ? !(received?.has(key) && received.get(key)===null) && visible(receipt.finished_at,key,VISIBLE_ACTIVITY_MS)
          : recent(receipt.started_at,600000);
      } else if(recall) {
        activityId=recall.call_id || recall.id;
        const response=records.find(r=>r.kind==='response' && r.call_id===recall.call_id && r.connection===recall.connection);
        active=response ? !(received?.has('event:'+response.id) && received.get('event:'+response.id)===null) && visible(response.timestamp,'event:'+response.id,VISIBLE_ACTIVITY_MS)
          : recent(recall.timestamp,600000);
      }
      const id=JSON.stringify(['recall',aura,agent]);
      edges.set(id,{id,sources:[aura],targets:[agent],active,activityId,actor:agent,kind:'recall',detail:receipt ? `Recall via relate-memory-mcp · ${receipt.tool} · ${receipt.project || 'Project unspecified'} · ${receipt.state}` : recall ? `Recall via ${recall.server} · ${recall.tool} · ${recall.project || 'Project unspecified'}` : 'Recall via relate-memory-mcp'});
    }
    const server = node('server:' + JSON.stringify([agentId, event.server]), event.server, 'MCP server · observed transport', 'server');
    const database = event.database;
    const persisted = !!event.persisted_offset;
    const destination = database ? node('database:' + JSON.stringify([event.host, database]), database,
      event.database_state === 'error' ? 'Relate · bootstrap error' : event.database_state === 'retrying' ? 'Relate · write retry pending' : persisted ? 'Relate database · locally saved' : 'Relate database · opening', 'store')
      : event.database_state ? node('unresolved:'+agentId, 'Awaiting agent identity', 'Database routing pending', 'store')
      : node('legacy-index', 'capture.rl', 'Legacy capture index', 'store');
    const call=latest.get(agentId);
    const response=call && records.find(r=>r.kind==='response' && r.call_id===call.call_id && r.connection===call.connection);
    const current=!!call && call.server===event.server && call.project===event.project;
    const inFlight=!!call && (!response ? now-(received?.get('event:'+call.id) ?? Date.parse(call.timestamp))<600000 : visible(response.timestamp,'event:'+response.id));
    const active=current && inFlight;
    const callId=call?.call_id || call?.id;
    edge(host, agent, active, callId, false, agent);
    const desktop = event.agent === 'claude-desktop';
    const projectName = event.project || (desktop ? event.server : undefined);
    if (projectName) {
      const subtitle = desktop ? 'Project · Project Name' : 'Project · GitHub Repo';
      const projectKey = desktop ? [agentId, projectName] : ['repository', projectName];
      const project = node('project:' + JSON.stringify(projectKey), projectName, subtitle, 'project');
      edge(agent, project, active, callId, false, agent);
      edge(project, server, active, callId, false, agent);
    } else {
      edge(agent, server, active, callId, false, agent);
    }
    const commit=current && callId ? event.commits?.filter(c=>c.call_id===callId).at(-1) : undefined;
    const commitObserved=commit ? received?.get('commit:'+commit.id) : undefined;
    const requestObserved=call ? received?.get('event:'+call.id) : undefined;
    const localVisible=commit && (commitObserved!=null || requestObserved!=null
      ? recent(new Date(Math.max(commitObserved ?? -Infinity,requestObserved ?? -Infinity)).toISOString(),VISIBLE_ACTIVITY_MS)
      : recent(commit.at));
    edge(server, destination, !!localVisible, commit?.id, false, agent);
    if (aura && database) {
      const push=event.replication;
      const pushing=push?.state==='pushing' || (push?.state==='synced' && visible(push.last_pushed_at,pushActivityKey(event),4000));
      edge(destination, aura, pushing, push?.push_id, push?.state!=='synced', agent);
    }
  }
  return { boxes: [...boxes.values()], edges: [...edges.values()] };
}
