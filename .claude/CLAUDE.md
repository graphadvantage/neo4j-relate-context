## Shared coding context capture

Ongoing coding-context capture is enabled for this repository when
`relate-memory-mcp` is connected. Use its memory tools and the
`relate-tap-memory` skill. The MCP server discovers the project from Git origin;
omit `project` to accept that default. Do not substitute a checkout folder name.

Recall repository context at session start. After every completed user/assistant
exchange, call `record_turn`, using one session UUID per chat and a fresh turn
UUID per exchange (reuse that turn UUID for retries). Include the actual prompt,
response, grounded decisions, unresolved work and concise reasoning summaries;
include only observed tool evidence. Omit secrets and private chain-of-thought;
never estimate token counts or timings. No per-turn request to save is needed.
Report failed writes. Honor requests to stop or exclude capture.

Use the connected memory route instead of the legacy `relate-context` helper or
its `graph.rl`. Live Git is authoritative. Standalone notes require a request.
Call `flush_memory` on an explicit wrap-up; a queued push is not proof of Aura
delivery. For shared repository recall, use `recall_context` with
`context_kind: "Code"` and optional `query`; omit `mcp_source_id`. This reads
existing Aura coding turns across agents using full-text relevance and recency,
with an explicitly reported lexical fallback. Use the advertised canonical Git
key, including host and owner/path; never shorten it or silently include legacy
short-name history. Recorded Git anchors remain historical evidence, and dirty
working trees require verification against live files. Resolve or supersede
memories only through explicit evidence-backed lifecycle events.
Expand matches with `get_evidence(turn_id=..., context_kind="Code")` or
`get_session(session=session_key, context_kind="Code")`. Report degraded access
and pending local records honestly; verify recalled claims against live Git.
