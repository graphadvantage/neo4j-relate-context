# Freshness and supersession

Status: **implemented after canonical repository identity**. The original
proposal is retained below as design history. Implementation choices:

- Git anchors are immutable per turn. Server observation requires the capture
  project to match the current canonical Git identity; otherwise fields remain
  unknown unless explicitly client-reported. Partial client reports do not get
  silently completed from the server's potentially different checkout.
- Session anchors are derived from the latest recorded turn at read time,
  avoiding stale session properties under skip-existing Aura replication.
- `record_turn` returns new `memory_ids`. New memories can specify `resolves`
  or `supersedes`; `memory_events` can explicitly record `resolved`, `superseded`
  or `reopened`, with a reason and a replacement ID for supersession.
- Lifecycle targets and replacements must be in the same project (or be a new
  memory in the recording turn). Code transitions cannot target Chat-only or
  ambiguous mixed sessions. Invalid references reject the whole capture.
- Events are append-only `ContextMemoryEvent` nodes, linked to the memory and
  recording turn, with a `SUPERSEDES` edge for explicit replacement. Retry IDs
  are stable; original evidence remains intact. The new label has an Aura merge
  key. Existing historical nodes are never backfilled with guessed Git state.
- Recall returns derived status and latest event provenance. Open/reopened
  memories rank before resolved, then superseded memories; none are deleted.
  Freshness is `unknown`, `working_tree_modified`, `working_tree_unknown` or
  `requires_live_git_verification`. There is no automatic ancestry check or
  assertion that a historical claim is current.

## Original proposal

## Problem

Coding recall returns history with no sense of *when* it was true or whether it
still is. Two concrete gaps:

1. **No freshness anchor.** `memory_graph.exchange()` hardcodes
   `"git_commit": None` (`memory_graph.py:305`), and that single value feeds both
   `ContextSession.git_commit` (`memory_graph.py:161`) and `ContextTurn.git_commit`.
   Every recalled turn carries `git_commit: null`, so the guidance to "verify
   against live Git" and support supersession has nothing to anchor on.
2. **No lifecycle.** `ContextMemory {kind: fact|decision|open_item}` has no
   resolved/superseded state. A recall returned the open item *"Repository-scoped
   coding recall"* next to the later turn that implemented it. Stale open items
   and outdated decisions resurface indefinitely.

Design principles carried over from the codebase: additive and backward
compatible; **append rather than mutate** (the snapshot replication skips
existing nodes, so in-place status edits would not reach Aura); evidence is
never dropped; and classification/derivation only *suggests* — it never asserts
over live Git (mirrors the inference-free stance of `project_labels.py`).

## Part 1 — Capture commit, branch, and dirty state

### Source of truth

Add a `git_state(directory=None)` helper in `relate_tap/bootstrap.py`, a sibling
of `discover_project()`/`context_kind()`, using the same timeout-guarded
subprocess pattern:

- `commit` — `git -C <dir> rev-parse HEAD` (full SHA; `None` on failure).
- `branch` — `git -C <dir> rev-parse --abbrev-ref HEAD` (`HEAD` when detached).
- `dirty` — `git -C <dir> status --porcelain` is non-empty → `True`.

All fields degrade to `None`/unknown on any error. A **dirty** working tree means
the commit is *not* authoritative for what the agent saw; recall must surface that
rather than present the commit as exact.

### Two provenance paths (never invented)

Follow the existing telemetry-provenance convention (`client_reported` /
`unavailable`, never zero-filled):

- **client_reported** — `record_turn` accepts optional `git_commit`,
  `git_branch`, `git_dirty`. The agent observes real git in its own checkout;
  this is the most accurate source.
- **server_observed** — when the client omits them, the memory server computes
  `git_state(Path.cwd())` at capture, exactly as `discover_project()` already
  resolves the project from CWD.
- **unavailable** — neither path yields a commit (e.g. non-git context).

Store `git_source` alongside the values so recall can weight them. Record git
state on the **turn** (per exchange) and roll the latest non-dirty commit up to
the session for quick scoping.

### Schema (additive)

On `ContextTurn` (and mirrored on `ContextSession` for the latest turn):

| Property | Meaning |
| --- | --- |
| `git_commit` | full HEAD SHA (existing property, now populated) |
| `git_branch` | branch or `HEAD` when detached |
| `git_dirty` | boolean; `true` means commit is approximate |
| `git_source` | `client_reported` \| `server_observed` \| `unavailable` |

Existing rows keep `git_commit = null` and read as `git_source = unavailable`;
freshness annotations degrade to "unknown", never a false "current".

## Part 2 — Resolved and superseded memories

### Append-only lifecycle

Do **not** mutate `ContextMemory.status` in place (snapshot replication skips
existing nodes, so the change would be local-only). Instead append:

- `(:ContextMemoryEvent {kind: 'resolved'|'superseded'|'reopened', reason,
  at, git_commit})`, linked `(memory)-[:HAS_STATUS_EVENT]->(event)` and
  `(event)-[:RECORDED_IN]->(turn)` for evidence.
- When one memory supersedes another: `(new)-[:SUPERSEDES {reason, at}]->(old)`
  plus a `superseded` status event on `old`.

Both the old and new `ContextMemory` nodes and all their `SUPPORTED_BY` evidence
remain intact. **Current status is derived** from the latest status event at read
time (`COALESCE(latest.kind, 'open')`), so there is no property to replicate-update.

### Who sets status

- **Explicit (authoritative).** `record_turn` memories may carry
  `resolves: <memory_id>` or `supersedes: <memory_id>`. This is evidence-backed:
  the turn that resolved the work records the transition. Primary mechanism.
- **Suggested (never automatic).** A conservative reader may *propose* that a new
  `decision`/`fact` resolves an `open_item` with a matching subject, but it only
  surfaces the candidate with evidence for the agent to confirm via a subsequent
  `record_turn`. No auto-resolution — consistent with "distinguish recorded
  decisions from your interpretation."

### Recall behavior

Extend the `MEMORIES` projection in `relate_tap/coding_recall.py:34` to compute
derived status and, by default, rank `open`/`resolved` ahead of `superseded`
while still returning superseded items as expandable evidence (never silently
dropped). A memory whose `git_commit` is an ancestor of current `HEAD`, or whose
newer sibling exists, is annotated `possibly_stale` — an annotation, not an
assertion. Live Git stays authoritative; recall reports, the agent verifies.

## Part 3 — Compatibility and replication

- **Backward compatible.** All turn properties and all memory status
  nodes/edges are additive. Existing turns and memories read as
  `unavailable`/`open` with no migration.
- **Replication.** New properties ride existing node replication (as the
  `Chat`/`Code` labels did). Crucially, lifecycle is modeled as **new
  `ContextMemoryEvent` nodes and `SUPERSEDES`/`HAS_STATUS_EVENT` edges**, not as
  mutations of existing memory nodes — so it propagates under the current
  append-oriented, skip-existing snapshot push. If/when the replication policy
  gains existing-node updates (the open "corrections/backfills/deletion" item),
  the populated `git_*` turn properties on *already-replicated* turns would also
  need that policy to reach Aura; until then, only turns captured after rollout
  carry git state remotely. This dependency is called out for the identity/
  replication track to resolve.

## Open questions

- Record git state on every turn, or only when `HEAD`/dirty changes within a
  session? (Per-turn is simplest and append-friendly.)
- Supersession scope: within a session, within a repository, or across the
  canonical repo identity once identity lands?
- Dirty-state detail: bare boolean vs a short changed-paths digest (bounded,
  redaction-safe) for stronger staleness signals.
- Should `possibly_stale` require an ancestry check against live `HEAD` at recall
  time (needs the server to run `git merge-base --is-ancestor`), or only compare
  recorded commits? The former is more accurate but reintroduces a live-git
  dependency in the recall path.
