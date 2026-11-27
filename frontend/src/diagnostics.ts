// Queue visible console errors while disconnected, then persist them on reconnect.
type Report = {source: string; error: string};
const storageKey='relate-console-pending-errors';
let pending: Report[]=[];
try { pending=JSON.parse(sessionStorage.getItem(storageKey) || '[]'); } catch { /* Storage may be disabled. */ }
let flushing=false;
const recent=new Map<string,number>();
function checkpoint() {
  try { sessionStorage.setItem(storageKey,JSON.stringify(pending)); } catch { /* Keep the in-memory queue. */ }
}
export function reportConsoleError(source: string,error: string) {
  const report={source:source.slice(0,120),error:error.slice(0,4000)};
  const key=JSON.stringify(report),now=Date.now();
  if (now-(recent.get(key) || 0)<60000) return;
  recent.set(key,now);
  if(recent.size>200) recent.delete(recent.keys().next().value!);
  pending.push(report);pending=pending.slice(-100);checkpoint();
  void flushConsoleErrors();
}
export async function flushConsoleErrors() {
  if(flushing) return;
  flushing=true;
  try {
    while(pending.length) {
      const response=await fetch('/api/errors',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(pending[0])});
      if(!response.ok) break;
      pending.shift();checkpoint();
    }
  } catch { /* Retain pending messages until the event stream reconnects. */ }
  finally { flushing=false; }
}
