# neo4j-relate-context
A federated context engine using relate.ai with replication to Neo4j Aura

Claude Desktop uses `services/relate-memory_mcp.py` over MCP stdio. Each
connection serializes local operations on `~/.relate-tap/databases/claude-desktop.rl`;
Aura pushes are serialized separately. Saved notes and captured exchanges
record the OS account name as `username` with `identity_source: os`.

The Claude skill is in `skills/relate-chat-memory/SKILL.md`. Upload that file
or a ZIP containing its folder through Customize → Skills → Add skill →
Upload skill, and leave it enabled. It organizes coding context by repository,
other project context by Claude project name, and general context using POLE-O
entities.

In Claude's global Instructions for Claude, add this routing instruction once:

> When the relate-memory-mcp connector is available, use my enabled
> relate-chat-memory skill in every conversation to recall and record context
> automatically, following its project scoping, privacy, and retry rules. No
> separate request to save memory is needed; honor any request to pause or
> exclude capture.

The skill guides model tool calls; it is not a guaranteed transcript hook.
Start a new chat after installation to test selection. A successful
`record_turn` confirms local capture; `flush_memory` queues background
replication. Verify an exact record in Aura before claiming remote delivery.

Captured tool-call messages link through `USES_TOOL` to project-scoped
`ContextTool` catalog nodes. Each visible query execution has its own
`ContextQuery`, reached through `EXECUTES_QUERY`, containing Cypher, parameters,
and any supplied rationale and observed result summary.

Each turn links through `HAS_REASONING` to a `ContextReasoning` node with a
concise evidence-backed summary. Its `SUPPORTED_BY` links reference captured
tool calls and the final response. Optional telemetry includes `model`,
`model_version`, `model_effort`, `input_tokens`, `output_tokens`, `total_tokens`,
and `elapsed_ms`. The service sums input and output tokens when both are known,
and rejects an inconsistent supplied total. Model, usage, and timing provenance
are `client_reported` or `unavailable`; unknown values are absent, never zero
estimates. The MCP service cannot independently measure the entire model turn
or retrieve Claude's private usage telemetry. A UI label such as "Opus 5.5"
with "Medium" effort does not establish an exact backend model version.

To materialize tool/query nodes from existing captured messages, run
`python3 backfill_tool_context.py --database relate-dbs/claude-desktop.rl`.
This additive, idempotent backfill leaves historical model/usage/timing unknown
and does not invent historical reasoning summaries. All three new labels are
included in Aura replication.

Run the graph integration tests with `python3 -B -m unittest discover -s tests -v`.
# Relate tap console

The `codex/relate-tap-console` branch adds a Docker-hosted live topology and
Relate playground, plus a transparent MCP command wrapper:

```sh
relate-tap --name neo4j-fraud -- node server.js
```

See [setup, source selection, Aura replication and capture semantics](docs/relate-tap.md).

On this Mac, `/usr/local/bin/relate-tap` is a symlink to the user-owned
`~/.local/share/uv/tools/relate-tap/bin/relate-tap` installation, alongside
`/usr/local/bin/neo4j-mcp`. This is a `uv` installation, not a Homebrew package.
Create the link once with administrator access:

```sh
sudo ln -s "$HOME/.local/share/uv/tools/relate-tap/bin/relate-tap" /usr/local/bin/relate-tap
```

Subsequent builds update the user-owned installation behind that link; they
do not require administrator access or changes to the MCP executable path.
From this repository, reinstall the current build with:

```sh
UV_TOOL_BIN_DIR="$HOME/.local/bin" uv tool install --reinstall .
```

The `/usr/local/bin/relate-tap` link continues to point to the updated tool.
Use that path in MCP configurations, or `relate-tap` when `/usr/local/bin`
is on the client's `PATH`.

The first MCP connection discovers its agent, opens or creates `<agent>.rl`,
installs the memory skill for Claude Code or Codex if missing, and registers
its route with the console. Claude Desktop uses its memory MCP connector.
The topology separates current connections from capture history and shows
the per-agent database followed by the enterprise Aura destination.
