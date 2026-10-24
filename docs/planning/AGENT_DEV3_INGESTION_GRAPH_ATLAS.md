# Agent Brief — Dev 3: Ingestion, Dependency Graph & MongoDB Atlas

> **Dev 3 is Caroline (`Caroline3110`).** Who is who: [TEAM_SPLIT](TEAM_SPLIT.md).

You are the implementation agent for **Dev 3** on Axolotl (Encode London 2026). You have three documents:
1. `Axolotl_Product_Requirements_and_Implementation_Handoff.md` — the Spec
2. `SHARED_CONTRACTS.md` — names, shapes, ownership
3. This brief

---

## Your mission

Turn real Python source into a trustworthy architecture graph and make MongoDB Atlas the foundational brain of Axolotl: every repository, symbol, edge and code chunk lives there, and Atlas Search + Vector Search (+ native Hybrid if available) power real retrieval for both users and Argon. The MongoDB bounty judges should see Atlas doing real work.

## You own

- `backend/app/persistence/**` (PyMongo `AsyncMongoClient`, `get_db`, idempotent `indexes.py`, Atlas Search/Vector index definitions)
- `backend/app/ingestion/**` (safe ZIP + demo-fixture import, snapshots)
- `backend/app/analysis/**` (AST extraction)
- `backend/app/graph/**` (graph build, `get_graph`, `load_networkx`)
- `backend/app/search/**` (chunking, embeddings, keyword/vector/hybrid queries)
- `backend/app/api/repositories.py`, `api/search.py`
- Collections `repositories`, `symbols`, `edges`, `code_chunks`
- Tests T-01–T-07; `docs/mongodb-atlas.md`; MongoDB bounty write-up text for the README

## You do NOT own

Running any code (you never execute imported code — parsing only), migration state, verification, UI.

---

## Components

### 1. Persistence
- `get_db()` returns a PyMongo `AsyncDatabase` from `MONGODB_URI`. Connection failure → raise `DatabaseUnavailable` → API 503. **No in-memory fallback** (Spec §11.2: disabling Atlas must break the journeys).
- `indexes.py` creates all regular indexes in SHARED_CONTRACTS §9 at startup (yours + others' — they tell you what they need) and creates/updates Atlas Search & Vector indexes via `create_search_index` (or documents the Atlas UI steps if the tier forbids driver creation).
- `search_status()` polls index status → `ok | building | unavailable`; Dev 2 surfaces it in `/system/status`. Search endpoints return 503 `SEARCH_INDEX_NOT_READY` while building — never pretend.

### 2. Safe import (Spec §5.1)
- Sources: allowlisted demo fixture (`demo/legacy_orders`, id `legacy_orders`) → `executable=True`; ZIP upload → `executable=False` (static only).
- Limits: 10 MB compressed, 50 MB extracted, 500 files, depth 12. Reject absolute paths, `..`, symlinks, decompression bombs (check declared and actual sizes while streaming). Ignore `.git`, venvs, `node_modules`, `__pycache__`, `.env`, key files, binaries.
- Copy accepted files to `WORKSPACE_ROOT/snapshots/<repo_id>/` and make the tree read-only (chmod). `snapshot_path(repo_id)` returns it. Dev 4 copies from here; nobody writes here again.
- `snapshot_hash` per SHARED_CONTRACTS §3.
- Exclude `demo/challenge_cases/` from anything you index or chunk — held-out cases must never be searchable (they aren't inside the fixture folder anyway; assert it).

### 3. AST analysis (Spec §5.2)
- `ast.parse` per `.py`; syntax errors → file-level warning, repository `analysis_status="partial"`, keep going.
- Extract modules, classes (+ bases), functions, async functions, methods, nested functions, decorators, signatures (`ast.unparse` of args), docstrings, line spans.
- Imports: `import x`, `from x import y`, relative imports resolved against package path. Local module if it resolves to a file in the snapshot; else `ext_<pkg>` node (stdlib may be grouped or hidden by default — expose a flag).
- Dynamic patterns (`importlib.import_module`, `__import__`, `getattr` on modules, string imports) → `unknown_dynamic` edge or warning with `resolution="unresolved"`.
- `calls` edges: best-effort only, `resolution="heuristic"`, never used for scheduling.
- `test_targets`: test files importing a module.
- Deterministic IDs per SHARED_CONTRACTS §3.

### 4. Graph
- `get_graph(repo_id, run_id=None)`: module-level nodes + `imports`/`from_imports` edges (deduped per module pair, keep first line), function/class counts, warnings. If `run_id`, overlay status from Dev 2 `get_unit_statuses(run_id)`.
- `load_networkx(repo_id)`: local modules only, `confirmed` import edges only.
- **T-03**: assert exact expected edge set for the fixture (get the list from Dev 4) — no extra, no missing, correct direction.

### 5. Search (Spec §8.5–8.7)
- Chunk per function/method/class (plus a module-level chunk). Fields: `chunk_id, repository_id, snapshot_hash, symbol_id, module_id, path, start_line, end_line, source, description, embedding`.
- `description`: a short natural-language summary of what the code does. Generate it with a **real** model call (a cheap Gemini model is fine — tell Dev 1) at import time; treat source as untrusted in that prompt. If generation fails, leave it null and record a warning — don't invent.
- Embeddings: embed `description + signature + identifiers`; dimensions consistent with the vector index.
- **Keyword**: Atlas Search `$search` on `source`, `description`, `qualified_name` (use a keyword/`lucene.standard` mix so exact names like `apply_discount` and error strings hit).
- **Vector**: `$vectorSearch` with a `repository_id` filter field.
- **Hybrid**: native `$rankFusion` combining both pipelines if the cluster supports it → response `native_hybrid=true`. Otherwise Python reciprocal-rank fusion → `native_hybrid=false` and the UI says so honestly. Return `keyword_rank` / `vector_rank` contributions.
- **T-07 demo query**: "where does the final customer bill get rounded?" / "customer payment rounding" must return the pricing rounding function even though its name doesn't contain those words. Coordinate with Dev 4 so the fixture function is named something like `finalise_amount` rather than `round_bill`.

### 6. Repository API
Implement the Dev 3 rows of SHARED_CONTRACTS §6, including `GET /demo-fixtures` and module source in `ModuleDetail`.

---

## Tasks with checks

| # | When | Task | Done when |
|---|---|---|---|
| 1 | Fri 17:00–18:00 | `get_db`, ping, indexes skeleton | `/system/status` shows real Mongo ok (**gate 18:00**) |
| 2 | Fri 18:00–21:00 | Fixture import, AST, edges, persisted, `GET /graph` + `/modules/{id}` | Real graph from Atlas in UI (**gate 21:00**); T-01, T-03, T-04 |
| 3 | Fri 21:00–23:30 | ZIP import hardening (T-02), chunks + Atlas Search index + keyword endpoint (T-06) | Exact name search returns correct symbol |
| 4 | Sat 08:00–11:00 | Descriptions + embeddings + Vector index + vector mode (T-07); cycle fixture for T-05 (a tiny separate test fixture, not the demo app) | Semantic query finds the rounding function |
| 5 | Sat 11:00–14:00 | Hybrid (`$rankFusion` or labelled fallback); `run_id` status overlay | **Gate 14:00** search live in UI |
| 6 | Sat 14:00–17:00 | Help Dev 2 integrate; verify disabling `MONGODB_URI` breaks graph/search/history with 503s (not silent fakes) | Cold-start E2E passes |
| 7 | Sat 17:00–22:00 | Fixes only; warm indexes before demo | |
| 8 | Sun 07:30–10:45 | `docs/mongodb-atlas.md` (collections, indexes, queries, which features are native), MongoDB 3-min video script points for Dev 5 | Handed to Dev 2/Dev 5 |

## Definition of done

Spec §8.7 demo works live: persisted graph → semantic search for "customer payment rounding" → result links to `pricing.py` node and its dependants → (via Dev 2/4) its counterexample and verification event, all read from Atlas.

## Pitfalls

- Atlas Search indexes build asynchronously — create them early Saturday morning, not at 17:00.
- Don't use the graph to claim runtime call accuracy; label heuristics.
- Keep embeddings out of API responses (large).
