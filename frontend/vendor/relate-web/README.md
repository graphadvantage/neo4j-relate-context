Vendored from https://github.com/relatedb/relate-web, commit 42a2a28.
Copyright 2026 The Relate Authors. Apache 2.0; see LICENSE.

The console reuses the playground editor, graph, results table, JSON view,
wire types and mobile hooks. The console replaces the WASM engine bridge with
an HTTP bridge to selected persistent sources. Expansion is adapted to support
Relate numeric IDs and Aura element IDs; graph view ID maps preserve strings.
The site, cookbook, WASM binary, seed data and reset UI are not bundled.

The console exposes a source-aware database schema sidebar and graph styling
controls. Style rules pass through the workspace to the graph renderer.
