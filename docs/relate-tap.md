# Relate tap and context console

The tap wraps a vanilla **stdio MCP** command without changing its server:

```sh
relate-tap --name neo4j-fraud -- node server.js
```

It forwards the original JSONL transport bytes and writes redacted capture copies
to a private append-only spool. On the first MCP client identity, the tap opens
or creates the agent's database and registers its route with the console.
Native taps use a detached native writer; the Docker console reads their files
read-only. Docker-hosted taps delegate writing to the console's Linux runtime.
The console also keeps `/data/capture.rl` as a history index. There is no
additional database engine.

## Start the console and a host-native tap

From this repository, with Python 3.10+, Relate 1.3, Node 24.11.1+ (only needed for local UI
development) and Docker Desktop / Docker Engine available:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
export RELATE_TAP_SPOOL="$HOME/.relate-tap/spool"
mkdir -p "$RELATE_TAP_SPOOL"
mkdir -p "$HOME/.relate-tap/databases"
docker compose up --build -d
relate-tap --name neo4j-fraud -- node server.js
```

Open <http://localhost:8080>. `docker compose down` stops the collector; its named
data volume retains the `.rl` graph and ingestion checkpoints. Use the same spool
directory in every tap and in Compose. If omitted in a native tap, the spool
defaults to `~/.relate-tap/spool`; Compose uses the same user-home default.
The collector can start before any MCP server is enrolled. It catches up after
restarts and displays ingestion errors without advancing past the failed event.

PowerShell activation and spool setup:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
$env:RELATE_TAP_SPOOL = Join-Path $HOME '.relate-tap/spool'
New-Item -ItemType Directory -Force $env:RELATE_TAP_SPOOL
docker compose up --build -d
relate-tap --name neo4j-fraud -- node server.js
```

Windows uses the dependency-free native tap; the collector runs Linux in Docker.
The tap inherits the MCP server's working directory, environment and stderr.
Its stdout contains only server output. A failed capture reports to stderr and
continues forwarding; this is observable best-effort capture, not an audit gate.
Already persisted spool events remain available even if the collector is offline.

Install `relate-tap` on the launching app's PATH. For application MCP
configuration, preserve the original command and arguments after `--`:

```json
{
  "mcpServers": {
    "neo4j-fraud": {
      "command": "relate-tap",
      "args": ["--name", "neo4j-fraud", "--", "neo4j-mcp"]
    }
  }
}
```

Keep the server's original environment entries as well. Enroll each desired
connection in each agent's configuration; this does not automatically discover
future servers. No existing Claude configuration is modified by installation.
Remote Streamable HTTP/SSE MCP connections are not proxied in this first version.

## Codex memory connection

Configure the installed native memory server in `~/.codex/config.toml`:

```toml
[mcp_servers.relate-memory-mcp]
command = "/usr/local/bin/relate-tap"
args = ["--name", "relate-memory-mcp", "--", "/usr/local/bin/relate-tap", "memory-server", "--agent", "codex"]
startup_timeout_sec = 30
```

The outer tap discovers the MCP client identity. The inner `--agent codex`
selects the memory server's `~/.relate-tap/databases/codex.rl`; it does not
set a global agent identity. The project defaults to a canonical Git origin key,
such as `git:github.com/graphadvantage/neo4j-relate-context`. Host and full path
are retained; URL credentials, query strings and fragments are not. SSH and HTTPS
transport variants share a key. Without a usable origin, a `git-local:` hash of
the host name and resolved Git common directory keeps worktrees together without publishing
private paths. Outside Git the working-directory name remains an unclassified
fallback. MCP tools can omit `project` to use this default.

The first connection installs `~/.codex/skills/relate-tap-memory/SKILL.md` if
missing. When ongoing capture is enabled in applicable Codex instructions, the
skill recalls context and records completed exchanges through the memory MCP
tools. It respects tool permissions, more specific repository instructions,
and requests to stop capture. Standalone notes are saved only when requested.
The skill is model-directed capture, not a guaranteed transcript hook.

Reload Codex after configuring the connection to make its tools available.
The topology shows a current route while the MCP client remains connected;
a command-line verification that exits leaves only a historical route.

## Run the tap itself in Docker

The image entrypoint is `relate-tap`. Use `-i` and **no TTY** for MCP stdio:

```sh
docker run --rm -i --init \
  --mount "type=bind,source=$RELATE_TAP_SPOOL,target=/spool" \
  --mount "type=bind,source=$PWD/my-mcp-server,target=/server,readonly" \
  --workdir /server \
  relate-tap:local --name neo4j-fraud -- node server.js
```

The command must exist **inside the container**. The image includes Node and
Python; install any server dependencies in a derived image and pass the server's
environment explicitly. Containers cannot launch arbitrary host-native commands;
use the host launcher for those. Both modes use the same capture format.
The Dockerfile supports native Linux arm64/amd64 builds through Relate's signed
platform releases. To publish both architectures, use your registry and
`docker buildx build --platform linux/arm64,linux/amd64 ... --push`.

## Agent and project attribution

The tap reads `initialize.params.clientInfo` or the newer per-request
`params._meta["io.modelcontextprotocol/clientInfo"]` without changing the message.

| Reported client name | Canonical agent | Database |
| --- | --- | --- |
| `claude-ai`, `claude-desktop`, `local-agent-mode-*` | `claude-desktop` | `claude-desktop.rl` |
| `claude-code` | `claude-code` | `claude-code.rl` |
| `codex-mcp-client`, `codex` | `codex` | `codex.rl` |

Native files default to `~/.relate-tap/databases`. Existing databases are opened
in place, never replaced by bootstrap. Multiple MCP servers for one agent use
the same database and one native writer process. The native memory MCP service
uses this same directory and lock. Docker-hosted taps use `/data/agents` in the
console's volume. Native and container writers never write the same file.

`--agent` / `RELATE_TAP_AGENT` override discovery for an ambiguous or unsupported
client. Unrecognized clients remain visible as unresolved routes; they do not
silently acquire another agent's database. `--project` / `RELATE_TAP_PROJECT`
provide project attribution. The server name comes from `--name`; username comes from
the process's OS user. In Docker that is the container user, not automatically
the host user. Host name is likewise the host/container name observed by the tap.
Without project configuration the UI shows an unspecified project. The tap cannot
infer Claude's active project, chat, model, token usage or private reasoning.
Restart a tap with a new project setting when changing a fixed project association.
No project memory notes are created automatically.

The first Claude Code or Codex connection installs `relate-tap-memory` in the
agent's skills directory if absent. Existing skills, including user edits,
are preserved. `CLAUDE_CONFIG_DIR` and `CODEX_HOME` are respected. A new skill
may become available on the agent's next skill scan or session. Claude Desktop
uses its memory MCP connector and receives no filesystem skill. The installed
skill uses a matching memory MCP connector or `relate-tap memory` for native
recall, requested notes, and authorized exchange capture. No additional MCP
connection is required for Claude Code or Codex. These commands use the same
agent database and lock as capture; exact tool transport works independently.
For container-hosted taps, skill installation is inside the container's configured
agent home; mount that directory if the client needs to read it from the host.

The topology defaults to **Current connections**, using connection heartbeats
that expire after 20 seconds. Renamed/disconnected servers remain in **Capture
history**. It shows the actual agent `.rl` filename and the configured
`enterprise-relate-memory` Aura instance. A dashed Aura edge means the latest
push is not confirmed. After upgrading the launcher, reconnect existing MCP
servers once so they use discovery and live registration.

Bright green dots follow the latest active MCP path for each agent. Calling a
different MCP server halts the previous path's animation and starts the new
one. Tool-request/response events drive the incoming path, native commit receipts
drive the local database edge, and actual snapshot push start/completion receipts
drive the database-to-Aura edge. An unchanged-file polling check does not animate
a push. Chat turns that produce no wrapped MCP traffic produce no MCP-path pulse.

## Playground sources and Aura replication

The built-in source is the collector's capture graph. Compose also mounts
`~/.relate-tap/databases` read-only and inventories its `.rl` files automatically. The
dropdown uses their filenames, including `claude-code.rl`, `claude-desktop.rl`
and `codex.rl`. The inventory refreshes automatically as files are created.
Lock files and symlinks are excluded.

`enterprise-relate-memory` is the Aura source defined in `docker/sources.json`.
Compose reads the private root `.env` into the console container. Use
`.env.example` as the template for a new deployment, populate `AURA_URI`,
`AURA_USERNAME`, `AURA_PASSWORD` and `AURA_DATABASE`, and protect it with
`chmod 600 .env`. Both Git and the Docker build context exclude `.env`.
After editing it, run `docker compose up -d` to recreate the container with the
new credentials. These enterprise credentials are independent of MCP servers'
`NEO4J_*` settings for their own data sources.

To customize the registry further, copy `docker/sources.example.json` to a
private local config, remove unwanted entries and mount it into the console.
An example Compose override:

```yaml
services:
  console:
    environment:
      RELATE_CONSOLE_SOURCES: /config/sources.json
      AURA_URI: ${AURA_URI}
      AURA_USERNAME: ${AURA_USERNAME}
      AURA_PASSWORD: ${AURA_PASSWORD}
    volumes:
      - ./local-sources.json:/config/sources.json:ro
      - ./relate-dbs:/sources:ro
```

Use `docker compose -f compose.yaml -f compose.local.yaml up -d`. Credential
values stay on the server; `/api/sources` only returns IDs, display names, kinds
and read-only flags. URI/path selection is controlled by the mounted registry,
not by arbitrary browser input. An Aura read-only account is recommended.
The playground accepts one bounded read statement, rejects writes/procedure calls
and external loading, and uses Relate's read-only open or an Aura read transaction.
Its conservative clause filter can reject property/parameter names that match
write keywords. Results are capped at 1,000 records, with a visible limit notice.
The 15-second query timeout is a backend execution timeout, not model telemetry.

The collector serializes reads of its own Linux `.rl` files with ingestion. Native agent files
are opened briefly read-only, without creating lock files on the read-only mount.
A busy host writer can prevent a `.rl` open; retry or use a stable snapshot.
Do not assume native macOS/Windows and Docker Linux advisory locks coordinate
across bind mounts. The reference `relate-web` browser WASM is replaced with an
HTTP source adapter so queries actually reach the selected persistent database.

Aura **querying** and **replication** are separate operations. Container-hosted
agent databases use the configured `AURA_*` environment for background snapshot
pushes. Native writers use a private Relate connection file, configured once
in `~/.relate-tap/config.json`:

```json
{"aura_credentials": "/absolute/path/to/connections/enterprise-relate-memory.txt"}
```

This is enterprise connection configuration, not a global agent identity.
The native workers for all discovered agents share that enterprise destination.
An optional `database_dir` setting changes the native database directory; set
Compose's `RELATE_TAP_DATABASE_DIR` to the same host directory.
To override the container's connection file explicitly:

```yaml
services:
  console:
    environment:
      RELATE_TAP_AURA_CREDENTIALS: /run/secrets/aura.txt
    volumes:
      - ./connections/enterprise-relate-memory.txt:/run/secrets/aura.txt:ro
```

The connection file must have mode 0600 or stricter as seen inside the container.
The existing snapshot/push pipeline uses explicit merge keys for capture and
context labels; replay and repeated pushes are idempotent. Native replication is
disabled until an enterprise connection is configured. The UI reports the last background push status, not per-event remote
commit acknowledgement. Credentials are never copied into the image.

## Evidence and retention

`CaptureHost` → `CaptureConnection` → `CaptureEvent` records observed activity.
`CaptureCall` correlates request/response events across matching JSON-RPC IDs.
Every wrapped connection has a distinct UUID. Requests link to `ContextTool`;
recognized Cypher links to `ContextQuery` with its submitted text, parameters,
original-text SHA256 and redaction flag. A transport observation is never labeled
as a verified database execution. Tool errors and JSON-RPC errors remain distinct.
Reported row counts are stored only when the response explicitly provides them.

The detector recognizes Cypher beginning with conventional clauses in arguments
named `query`, `cypher` or `statement`. It cannot see queries generated internally
by servers, encrypted strings, or custom argument schemas. Database query logs or
server instrumentation are still needed for database-executed evidence. The tap
captures no model token counts and no hidden chain of thought. Transport duration
and Relate query timings are measured separately.

Secrets in recognized keys and common credential patterns are redacted in capture
copies. This is not a universal DLP system: tool results can contain business data
and unrecognized secrets. Restrict the spool, volume and console to authorized
users. Spool files are created with mode 0600. The spool and `.rl` history have no
automatic retention/deletion policy yet; monitor disk usage. Keep spool files
append-only while ingestion is active. The live UI retains only the latest 1,000
saved events; use the playground for older records. Filtering does not change the
underlying graph or purge history.

The default published port binds only localhost. For a shared deployment, use an
authenticated TLS reverse proxy and set `RELATE_CONSOLE_TOKEN` for HTTP Basic
authentication (username `relate`). The browser remembers Basic credentials for
the SSE feed. The bundled healthcheck needs customization when authentication is
enabled. Cross-origin requests are refused; do not expose an unauthenticated port
to a network. Set `RELATE_CONSOLE_ALLOWED_HOSTS` to a comma-separated host allowlist
when serving through a proxy with a different hostname.

## Development and checks

```sh
python -m pip install -e '.[console,test]'
python -B -m unittest discover -s tests -v
cd frontend
nvm install
nvm use
npm ci
npm run typecheck
npm run build
```

For the disposable Docker integration check: `python tests/smoke_docker.py`.

For local backend development set `RELATE_CONSOLE_DATA` to a private writable
directory and `RELATE_TAP_SPOOL` to your spool, then run `relate-console`.
`npm run dev` proxies API traffic to localhost:8080.
The collector requires the Relate 1.3 CLI on PATH. The Docker build installs the
pinned signed release. The native tap needs no Relate binary or third-party Python
packages. The collector runs as a single process/worker per data volume.
Local builds use readable frontend bundles. Optional minification uses Terser
with one worker: `docker build --build-arg RELATE_MINIFY=1 -t relate-tap:local .`.

Upstream playground code and the signed installer are adapted from
`relatedb/relate-web` commit `42a2a28` under Apache 2.0. See
`frontend/vendor/relate-web/LICENSE` and its README for adaptations.

## Console error history

The topology retains recent error incidents with timestamps, source, file and
byte offset where available, and recovery status. Errors remain visible after a
retry succeeds. Use **Copy errors** or **Download log** to inspect them.
**Clear log** clears retained history and the current download, preserving active
failures. Cleared logs are archived under `/data/errors-archive/` for recovery;
new errors continue to be logged.
Capture backlog and Aura replication errors are logged server-side. Visible
source-loading, query and connection notifications are also reported to the log;
notifications raised while disconnected are queued in browser session storage
until the event stream reconnects.

The console keeps the latest 200 incidents in `/data/errors.json` and appends
error and recovery records to `/data/errors.jsonl` in its persistent Docker
volume. Download the full log at `/api/errors/log`; messages are redacted before
server-side storage. Logging starts when this version is deployed and cannot
recover previously dismissed messages. These diagnostics are separate from
captured chat memory and are not replicated to Aura.

## Shared repository topology

Coding routes are grouped by their existing Git-derived project key. A repository
appears once when multiple agents work on that key, with incoming agent edges and
outgoing edges to each agent's own MCP server and local database. The rule applies
to every repository and any number of agents; Desktop project-name nodes keep
their per-agent scope. A single agent can still connect to multiple repositories
or Desktop knowledge-graph sources.

Each topology edge retains its owning agent for animation and ELK route ordering.
Activity through a shared repository lights only the submitting agent's path;
concurrent agents can animate their own paths through the same junction. The
repository stays outlined while any of its connected paths is active.

## Shared intent recall (Cypher 25)

The existing console hosts the retrieval backend; no additional MCP connection is
needed. Set `AURA_URI`, `AURA_USERNAME`, `AURA_PASSWORD`, `AURA_DATABASE`, and
`OPENAI_API_KEY` in `.env`, then recreate the console to load them. Native Aura
pushes use their configured private four-line credentials file independently;
keep that file aligned with `.env` when changing the Aura destination.

On startup, a background worker creates `ContextIntent` indexes and promotes
eligible Desktop user questions with Cypher explicitly recorded in the same
`ContextTurn`. Qualified tool names (e.g. `robot-twin__read_cypher`) or explicit
`tool_calls[].mcp_source_id` supply stable connection names across restarts.
Unqualified ambiguous tools, missing Cypher, and very short acknowledgements are
skipped. No timestamps are used to infer causality. This is an initial Desktop
intent/query slice. Shared coding recall separately reads recorded Code turns and decisions; automatic coding handoff/decision extraction is not implemented.

Intents deduplicate by project, MCP name and normalized question; original user
messages link via `EXPRESSES`, and recorded query executions via `RESOLVED_BY`.
Associations are labeled `same_recorded_turn` and `client_reported_unverified`;
transport delivery does not establish answer correctness. Questions are embedded
with `text-embedding-3-small`, using native `VECTOR` values of 1,536 dimensions.
Long embedding inputs are bounded to 6,000 UTF-8 bytes; original questions remain
available as evidence. The legacy 3,072-dimension smoke test is separate.

Shared MCP tools:

- `recall_context(project, query, mcp_source_id, mode?, limit?, max_chars?)`
- `get_query(project, mcp_source_id, query_id, max_chars?)`
- `get_evidence(project, mcp_source_id, intent_id, limit?, max_chars?)`

`project` can be omitted when the coding server has discovered its Git project.
Shared Chat intent retrieval requires a domain MCP source; Chat query-free `recall_context`
keeps its legacy local-only behavior. Recall mode defaults to `hybrid`: exact
scoped cosine (ENN) plus scoped lexical search, combined with reciprocal rank
fusion. `ann` uses filtered vector-index search; `enn` uses exact cosine alone.
Full-text search embeds a generated scope key in the Lucene query before its
limit. Source/project guards are repeated for direct query and evidence lookup.
Distinct data sources must have distinct MCP names; names are the initial logical
identity, not globally authenticated tenant identities. This local console is
not an enterprise authorization boundary.

Responses include origin, validation, scope, receipt ID, and truncation state.
`max_chars` is a character budget (2,000–64,000), not a measured token count;
shared limits are 1–20. Recall includes complete Cypher and parameters in each query reference when
they fit the budget. Otherwise the query ID remains with
`retrieval_required: true`, requiring `get_query`; no executable preview is
substituted. An oversized standalone `get_query` response is withheld with
`required_chars`, never returned as a truncated executable statement. Pending
local evidence uses scoped lexical matching over the latest 100 query-associated
Desktop records per local database; it is explicitly separate from Aura results.
Aura failures report `degraded`, never silently widen scope. Default native
backend URL is `http://127.0.0.1:8080`; configure `RELATE_MEMORY_CONSOLE_URL` or
`console_url` in `~/.relate-tap/config.json` when needed. An optional
`RELATE_CONSOLE_TOKEN`/`console_token` uses the console's existing Basic auth.

`GET /api/memory/status` reports promotion status. MCP clients must restart their
existing memory-server connection after updating the package to discover the new
tools. This deployment verifies MCP stdio and the backend; it does not prove each
client will automatically invoke recall on every turn.

Manual scoped acceptance test (paid embeddings; isolated fixtures are removed):

```sh
docker compose run --rm --no-deps -T -v "$PWD:/work:ro" -w /work \
  --entrypoint python3 console - < tests/smoke_intent_recall.py
```

Reported Cypher must contain the complete submitted statement and match query
text supplied in tool arguments. `record_turn` rejects prose descriptions and obvious ellipsis
placeholders outside literals/comments instead of saving them as queries.
Natural-language search arguments do not create `ContextQuery` records.
Retry the exchange with the full observed statement. Existing incomplete query
records remain intact for evidence, but `get_query` marks them `incomplete` and
`reusable: false`; recall query references omit them. These are prefix/placeholder
checks, not a Cypher parser or proof that a query executes successfully. Exact
transport-to-turn correlation remains a separate integration step.

## Recall animation

The topology shows one bright-cyan enterprise-to-agent return lane per agent,
aggregating its projects. It uses the same line width and four particles as
capture activity; hover the lane for its memory MCP tool, project, and state.
Each lane is routed above its own agent’s node blocks, using ELK positions, so
return traffic occupies the gaps between agent routes without reordering layers.
Shared repository junctions do not pull another agent’s recall lane upward.

Shared `recall_context` (with a query), `get_query`, and `get_evidence` calls
activate recall activity. Local-only legacy recall and `get_session` do not.
Wrapped connections use tap events; the native memory forwarder also supplies
agent/host metadata to the console so unwrapped Desktop memory connections are
visible. These bounded, in-memory receipts contain no question, query or result
payload. Tool activity is not proof that recall succeeded; the tooltip reports
the returned state. Completed activity stays visible for six seconds after
observation and is not replayed on page reload. Reconnect existing memory MCP
processes after updating to enable forwarding metadata.

Saved tool-request and Cypher-submission metrics use the collector's persisted
capture graph across current and previous connections, independently of the
1,000-event display window. Agent/project filters apply to these totals.
Cypher submissions count distinct transport query records, not model-reported
copies or distinct query strings; submitting the same statement twice counts
as two submissions. A short cache refresh also reflects external graph resets.

The **User intents saved** metric counts distinct `ContextIntent` nodes in
enterprise Aura memory, grouped by project and originating agents. Multiple
queries or supporting messages do not multiply an intent. Counts refresh in
the background with intent promotion; pending, disabled, or unavailable totals
display a dash instead of zero. **Cypher queries saved** counts the persisted
transport submissions described above.

The memory skill and MCP instructions require shared recall for each new
substantive domain-data question, not just once per session. Applicable full
queries should be reused; omitted query text must be fetched before reuse.
A model can still choose to generate a query, so its reason and provenance
should be reported honestly; this does not enforce or prove query reuse.


### Chat and Code classification

Projects and sessions retain their existing base labels and receive secondary
`Chat` or `Code` labels. Repository work uses `:ContextProject:Code` and
`:ContextSession:Code`; chatbot/MCP projects use `:ContextProject:Chat` and
`:ContextSession:Chat`. A project can accumulate both labels across distinct
sessions. Labels do not change existing IDs, uniqueness keys, or project names.

The tap and memory writer discover `Code` when the project matches the Git-origin
repository identity of their working directory. Desktop defaults to `Chat` even if
its process starts in a checkout. The tap accepts `--context-kind Chat|Code` and
`record_turn` accepts `context_kind` for explicit attribution, including work in
an external directory. Unknown attribution is left unclassified. Merely using
MCP or a coding agent does not establish repository provenance.

Labels are written locally and follow the existing snapshot push to Aura.
Existing records can be labeled with `relate_tap.project_labels.backfill`, using
an explicitly verified project inventory. This additive operation preserves
records and skips unclassified sessions in mixed projects. It does not rename
or split legacy projects that already share the same name.

These labels establish the recall classification boundary. Shared coding recall
reads Code projects/sessions by repository across agents; it uses indexed full-text
relevance and recency with an explicit lexical fallback. Automatic handoff generation and semantic coding embeddings remain
separate work.


### Claude Code across repositories

Register the memory MCP once at user scope:

```sh
claude mcp add --scope user relate-memory-mcp -- /usr/local/bin/relate-tap --name relate-memory-mcp -- /usr/local/bin/relate-tap memory-server --agent claude-code
```

Start Claude Code from each repository. Both tap and memory server discover its
canonical Git-origin key from the working directory. No per-repository MCP
entry or fixed `--project` is needed. All Claude Code projects use the same
`claude-code.rl`, with separate `ContextProject` attribution. A more specific
project/local entry with the same server name can override the user entry;
check `/mcp` when diagnosing a route. Restart an existing Claude Code session to
load the changed configuration.

Automatic exchange capture also requires the corresponding user/project memory
instructions; configuring MCP alone records only calls actually made through it.


### Shared coding recall

`recall_context(context_kind="Code", query="design decisions")` searches existing
Aura coding turns and returns author/session/turn IDs, prompt/response excerpts,
reasoning summaries when recorded, recent sessions and structured memories.
Repository-attributed requests default to this mode even without a query. The Git project is the
scope; no domain MCP identifier is required. `source_agent` optionally narrows
authorship, while the default includes all agents. Code reads require Aura
credentials but do not require an OpenAI key or make embedding requests.

`get_session(context_kind="Code", session=<session id>)` reads a shared session;
`before_position` pages backward. `get_evidence(context_kind="Code", turn_id=...)`
expands an individual turn. Both enforce repository and Code-session scope.
Responses report their full-text or fallback retrieval mode, carry unverified evidence provenance,
and explicitly distinguish failed retrieval, no topic match, existing sessions
and pending local records. No historical decision or commit is invented.

Recall routing uses explicit `context_kind` first, then matching Git provenance,
then an unambiguous locally stored Code-only project label. It does not maintain
a coding-agent allowlist. Desktop defaults to Chat; mixed or unknown projects
require explicit `context_kind: "Code"` for shared coding recall. Supplying a
domain `mcp_source_id` keeps the Chat retrieval path unless Code is explicitly
selected (which rejects that incompatible scope). Transport client discovery and
skill installation still have their own supported-client mappings; an unfamiliar
MCP client can use `--agent` for capture routing.

#### Coding full-text indexing

The console builds `context_code_turns_text` on `ContextTurn.search_text`, derived
from the original prompt, final response and recorded reasoning summary. Old
turns require no embeddings or generated summaries. Each background pass indexes
up to 200 complete turns and runs independently of chatbot intent promotion and
the OpenAI key. Progress is persisted in the derived properties, so restarting
continues from missing turns. Indexing runs at startup and on a roughly 30-second
maintenance cycle; newly replicated records may await that cycle.
When an index is already ready, a newly replicated turn may be absent until
the next maintenance pass; index readiness alone does not trigger a substring
scan for each new turn. Available pending local evidence is still returned
separately. Substring fallback applies while building or after index failure.

Ranked recall uses `db.index.fulltext.queryNodes`, retained for Neo4j 5/Cypher 25
compatibility; see the [Neo4j full-text index documentation](https://neo4j.com/docs/cypher-manual/current/indexes/search-indexes/full-text-indexes/).
Query terms are quoted, and canonical repository plus optional author membership
are filtered before the result limit, so other projects cannot crowd out the
requested scope. Scores order relevance first and timestamp/turn ID break ties.
Query-free recall still returns recent recorded history.

`retrieval_mode` is `fulltext_recency` when the index is ready, otherwise
`lexical_recency`. `search_index` reports `ready`, `building` or `unavailable`.
Missing privileges or index failures produce a disclosed, scoped fallback;
unavailability is degraded rather than a false empty result. Local pending
records always use read-only substring retrieval, never index writes. This
index is a derived Aura projection, separate from source-evidence replication.

### Repository identity and legacy history

New automatic scopes include the Git host and full owner/repository path:
`git:github.com/team/backend` and `git:github.com/other/backend` are distinct.
GitHub paths are normalized to lowercase; other hosts preserve path case.
Non-default ports remain part of the host identity. SSH aliases, repository
renames and separate origin-free clones are not automatically equated; use a
shared explicit canonical `--project` value when equivalence is verified.
Project keys support up to 512 characters. Use the key advertised by the memory
MCP instead of supplying the former short repository name.

Existing projects, sessions and evidence IDs are unchanged. Canonical recall
never falls back to a same-named legacy project. Explicit requests using the old
project key can still inspect legacy history. Restart MCP clients after upgrading
to load the new default, and start a new session when switching project keys.

To make verified history available in a canonical scope, inspect the evidence
and select individual `ContextSession.id` values. This prints a dry-run plan;
add `--apply` to create the links in a local database:

```sh
python -m relate_tap.project_identity \
  --database "$HOME/.relate-tap/databases/codex.rl" \
  --legacy-project neo4j-relate-context \
  --origin https://github.com/graphadvantage/neo4j-relate-context.git \
  --session-id VERIFIED_CONTEXT_SESSION_ID
```

Repeat `--session-id` for each verified session. Every selection must belong to
the legacy project and carry `Code` without `Chat`; otherwise nothing is linked.
Binding is additive and idempotent: original membership, messages, turns,
memories and IDs remain. New `HAS_SESSION` links replicate through the normal
snapshot push without updates to existing Aura nodes. Capture events, standalone
notes and chatbot intents are not reattributed. Already mixed sessions cannot
be safely split. No live database is migrated automatically.
