// Adapted for the context console: server-selected persistent sources replace WASM.
import type { ScriptRun } from './protocol';
import type { WireResult } from './wire';
export type { ScriptRun };

export type RemoteResult = WireResult & { elapsed_ms: number; truncated: boolean; timings?: unknown };
class RemoteEngine {
  source = 'capture';
  kind = 'relate';
  async execute(query: string, paramsJson: string | null, source = this.source): Promise<RemoteResult> {
    const response = await fetch('/api/query', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ source, query, parameters: JSON.parse(paramsJson || '{}') }) });
    const result = await response.json();
    if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : 'Query request failed');
    return result;
  }
}
export const engine = new RemoteEngine();
