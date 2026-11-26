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
set a global agent identity. The project defaults to the repository name from
Git's `origin` URL, falling back to the Git common directory and then the
working directory. Renamed checkouts and worktrees with the same origin share
the project name. MCP memory tools can omit `project` to use this default.

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
