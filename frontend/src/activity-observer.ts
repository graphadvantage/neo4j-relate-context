import type { CaptureEvent, Snapshot } from './topology-model';

export type ActivityTimes = ReadonlyMap<string, number | null>;
export const VISIBLE_ACTIVITY_MS=6000;

export function pushActivityKey(event: Pick<CaptureEvent,'host'|'agent'|'database'|'replication'>) {
  const push=event.replication;
  if(!push?.push_id) return undefined;
  return JSON.stringify(['push',event.host,event.agent,event.database,push.push_id,push.state,push.last_pushed_at]);
}

/** Receipt times affect display only; captured timestamps remain authoritative. */
export class ActivityObserver {
  private seen=new Map<string,number|null>();
  private initialized=false;
  observe(snapshot: Snapshot,now: number): ActivityTimes {
    const baseline=!this.initialized;
    const mark=(key?: string)=>{
      if(key && !this.seen.has(key)) this.seen.set(key,baseline ? null : now);
    };
    for(const event of snapshot.events) mark('event:'+event.id);
    for(const recall of snapshot.recalls || []) {
      mark('recall:'+recall.id);
      if(recall.finished_at) mark('recall:'+recall.id+':finished');
    }
    for(const route of snapshot.routes || []) {
      for(const commit of route.commits || []) mark('commit:'+commit.id);
      mark(pushActivityKey(route));
    }
    // The React placeholder is not the first real server snapshot.
    if(snapshot.revision!==undefined || snapshot.events.length || snapshot.routes?.length) this.initialized=true;
    while(this.seen.size>10000) this.seen.delete(this.seen.keys().next().value!);
    return new Map(this.seen);
  }
}
