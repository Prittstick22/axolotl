# MongoDB Atlas

Owner: Dev 3. Collections, Search / Vector / Hybrid indexes (SHARED_CONTRACTS §9).

Cluster: Atlas M10, MongoDB 8.0.34 (native `$rankFusion` available; `$scoreFusion` is not).
Database: `axolotl` (`MONGODB_DATABASE`). Driver: PyMongo `AsyncMongoClient`, not Motor
(Motor reached end of life in May 2026, see BUILD_LOG.md).

## How the database sets itself up

On every startup, `main.py` calls `app/persistence/indexes.py::ensure_indexes()`. It:

1. creates any of the 12 §9 collections that do not exist yet;
2. creates any regular index from `INDEX_SPECS` that is missing;
3. **never drops, renames or modifies** an existing index. If one exists with a different
   shape or name, it is left as it is, logged as a conflict, and startup carries on;
4. raises 503 `DATABASE_UNAVAILABLE` if Atlas is unreachable. There is no in-memory
   fallback.

Running it a second time creates nothing. `tests/integration/test_persistence_live.py`
proves this against the real cluster, using a throwaway `axolotl_test_*` database.

Dev 2's and Dev 4's code (`orchestration/store.py`, `orchestration/events.py`,
`verification/store.py`) also creates some of these indexes at startup. Every name below
is the one already live on Atlas, so all of them can run side by side.

## Collections and regular indexes

All index names are explicit and must never change: MongoDB refuses to create the same
keys under a second name.

| Collection | Writer | Index name | Keys | Unique |
|---|---|---|---|---|
| `repositories` | Dev 3 | `repository_id_unique` | `repository_id` | yes |
| | | `repository_created` | `created_at` | |
| `symbols` | Dev 3 | `symbol_unique` | `repository_id, symbol_id` | yes |
| | | `symbol_qualified_name` | `repository_id, qualified_name` | |
| | | `symbol_module` | `repository_id, module_id` | |
| `edges` | Dev 3 | `edge_source` | `repository_id, source` | |
| | | `edge_target` | `repository_id, target` | |
| `code_chunks` | Dev 3 | `chunk_unique` | `repository_id, chunk_id` | yes |
| `search_index_status` | Dev 3 | `index_status_repository_unique` | `repository_id` | yes |
| `migration_plans` | Dev 2 | `plan_id_unique` | `plan_id` | yes |
| | | `plan_repository` | `repository_id` | |
| `migration_runs` | Dev 2 | `run_id_unique` | `run_id` | yes |
| | | `run_state_created` | `state, created_at (desc)` | |
| `run_events` | Dev 2 | `run_sequence_unique` | `run_id, sequence` | yes |
| `counters` | Dev 2 | *(built-in `_id`)* | `_id` | yes |
| `behaviour_cases` | Dev 4 | `case_repo_unit` | `repository_id, unit_id` | |
| | | `case_repo_case_unique` | `repository_id, case_id` | yes |
| `verification_results` | Dev 4 | `verification_id_unique` | `verification_id` | yes |
| | | `verification_run_unit` | `run_id, unit_id` | |
| `counterexamples` | Dev 4 | `counterexample_id_unique` | `counterexample_id` | yes |
| | | `counterexample_run_unit` | `run_id, unit_id, status` | |
| `artefacts` | Dev 2 | `artefact_id_unique` | `artefact_id` | yes |
| | | `artefact_run` | `run_id` | |

`counters` needs no index of its own: §9 lists `_id`, which MongoDB creates on every
collection automatically.

`search_index_status` is not in §9 yet: it holds each repository's background search
indexing progress (#28, below). It is Dev 3-only and additive; the §9 row has been
proposed to Dev 2.

Two more collections exist on the cluster but are not in §9, so `ensure_indexes()`
leaves them to their owners: `baseline_outcomes` (Dev 4, index `baseline_run_case`) and
`run_reports` (Dev 2).

## Deviations from SHARED_CONTRACTS §9

- **Edges are stored with `source` / `target`, not `source_symbol_id` /
  `target_symbol_id`.** Stored edge documents are `GraphEdge` models
  (`id, repository_id, source, target, type, resolution, basis, source_path,
  source_line`), and real data on the cluster already uses those names. The edge
  indexes are therefore on `(repository_id, source)` and `(repository_id, target)`.
  Agreed by Dev 3 on Fri 23 Oct; a §11 contract update will follow.
- **`behaviour_cases`:** "case_id unique per repo" is implemented as a unique
  `(repository_id, case_id)` index (awaiting Dev 4's confirmation).
- **Extra indexes:** `repository_created`, `symbol_module` and `plan_repository` are not
  in §9. Other code on `main` already created them, and they are kept.

## `code_chunks` documents

One document per function, method, class and module. Built by
`app/search/chunking.py`.

```
chunk_id         deterministic: sha256(snapshot_hash + symbol_id)[:16]
repository_id    every query filters on this
snapshot_hash
symbol_id        joins back to `symbols`
module_id, qualified_name, kind, path, start_line, end_line
source           the lines of this definition only, capped at 8000 chars
identifiers      qualified name + bare name + decorators + bases + signature
docstring        indexed as text, never obeyed
description      one sentence from Gemini, or null — never invented
embedding        768 floats, or null if embedding failed
```

Unique index: `(repository_id, chunk_id)`. `chunk_id` is derived from the snapshot
hash, so **two imports of the same fixture produce the same chunk ids**. They are
unique within a repository, not globally — the same convention `symbol_id` already
follows. Do not use a bare `chunk_id` as a global key.

`description` and `embedding` are deliberately nullable. A failed Gemini call leaves
them null and records a warning on the repository; it never writes a guessed sentence
or a zero vector. A zero vector would sit at a fixed distance from every query and
quietly pollute every result, which is worse than being absent.

---

## The three search modes

`GET /api/v1/repositories/{repo_id}/search?q=&mode=keyword|vector|hybrid&limit=`

| mode | stage | good at |
|---|---|---|
| `keyword` | `$search` | exact identifiers, error strings, anything you can spell |
| `vector` | `$vectorSearch` | intent, when you do not know the name |
| `hybrid` | `$rankFusion` | both at once — the default |

The response says which engine answered:

```json
{ "mode_used": "hybrid", "native_hybrid": true, "hits": [ ... ] }
```

`native_hybrid` is **measured, not assumed**: `supports_native_hybrid()` reads the
server version, and if `$rankFusion` fails for any reason the code falls back to
reciprocal rank fusion in Python and reports `false`. Claiming native fusion while
running the fallback would misrepresent how the answer was produced.

Each hit carries `keyword_rank` and `vector_rank` where known, so the UI can say *why*
something ranked where it did. Embeddings are never returned — 768 floats per hit
would dwarf the payload and help nobody.

### Degrading, and the difference from "no matches"

- One index missing → the other one answers, and `mode_used` says which.
- The query cannot be embedded → `hybrid` falls back to `keyword`; explicit `vector`
  mode returns **503**, because silently answering a different question is worse.
- An index missing or still building → **503 `SEARCH_INDEX_NOT_READY`**, with
  `details.state`. Never an empty list: "nothing matched" and "the index is not ready
  yet" look identical to a UI otherwise, and they are completely different answers.
- An unknown repository → **404**, for the same reason.

---

## The two indexes

Created and kept up to date automatically at startup by `ensure_search_indexes()`.
It is idempotent, and it will:

- create either index if absent;
- **update** an index whose definition has drifted from the code (an index answering
  queries under last week's rules, with nothing saying so, is worse than a missing
  one);
- **drop and recreate** an index in `FAILED` status. A failed index is not queryable
  and never retries by itself, so skipping it because the name exists would leave
  search permanently dead. The index holds no data of its own, only a derived view of
  `code_chunks`.

Both builds are **asynchronous**: creation returns immediately and readiness takes
seconds to minutes. `search_status()` reports `ok | building | unavailable`, and
`/system/status` surfaces it.

### `code_chunks_text` (Atlas Search)

```json
{
  "analyzers": [{
    "name": "identifier_parts",
    "tokenizer": { "type": "regexSplit", "pattern": "[^a-zA-Z0-9]+" },
    "tokenFilters": [{ "type": "lowercase" }]
  }],
  "mappings": {
    "dynamic": false,
    "fields": {
      "repository_id": { "type": "token" },
      "qualified_name": {
        "type": "string", "analyzer": "identifier_parts",
        "multi": {
          "exact": { "type": "string", "analyzer": "lucene.keyword" },
          "prose": { "type": "string", "analyzer": "lucene.standard" }
        }
      },
      "identifiers":  { "…as qualified_name…" },
      "description":  { "type": "string", "analyzer": "lucene.standard" },
      "docstring":    { "type": "string", "analyzer": "lucene.standard" },
      "source":       { "type": "string", "analyzer": "lucene.standard" },
      "path":         { "type": "string", "analyzer": "lucene.keyword" }
    }
  }
}
```

`dynamic: false` on purpose: `embedding` is 768 floats and indexing it as text would
bloat the index for nothing.

**Three things here were learned by running it, not by reading documentation.** They
are the reason the definition looks the way it does, and all three are easy to
reintroduce by "simplifying" it:

1. **`lucene.standard` does not split `pricing.finalise_amount` on the dot.** It
   indexes the whole dotted name as one token. Searching `finalise_amount` matched
   that field *not at all*, and `finalise` matched nothing anywhere in the index. The
   custom `identifier_parts` analyser splits on every non-alphanumeric character, so
   `pricing.finalise_amount` yields `pricing`, `finalise`, `amount` — which is how
   people actually search for code. This is what T-06 depends on.
2. **Two `string` definitions on one field is rejected outright.** The first build of
   this index came back `FAILED` with *"field contained multiple [string] type
   definitions"*, and a failed index is not queryable at all. Alternate analysers go
   in `multi`, never in a list.
3. **Atlas Search is eventually consistent.** Freshly inserted documents are not
   searchable for a few seconds, so the first search after an import returned nothing.
   Import now waits for its chunks to appear (`wait_until_searchable`, 45s cap) and
   warns if they have not.

The query side mirrors the mapping, most precise first:

| clause | boost | matches |
|---|---|---|
| `qualified_name` `multi: exact` | 10 | the whole dotted name, one token |
| `qualified_name` (`identifier_parts`) | 6 | `finalise_amount`, or just `finalise` |
| `identifiers` | 4 | decorators, bases, signature |
| `description` | 3 | the generated sentence |
| `docstring` | 2 | |
| `source` | 1 | last resort |

### `code_chunks_vector` (Vector Search)

```json
{ "fields": [
  { "type": "vector", "path": "embedding", "numDimensions": 768,
    "similarity": "cosine" },
  { "type": "filter", "path": "repository_id" }
] }
```

`numDimensions` is read from `EMBEDDING_DIMENSIONS`, the same setting the embedding
call uses, so the two cannot silently disagree. Changing it means an index rebuild
**and** a re-index of every repository — `embed_one` checks the returned length and
fails loudly rather than letting `$vectorSearch` reject the query with no explanation.

`repository_id` is a filter *inside* the stage, not a `$match` after it, so one
repository's chunks cannot crowd another's out of the candidate list.

---

## Embeddings and descriptions

| | |
|---|---|
| model | `gemini-embedding-001` (`GEMINI_EMBEDDING_MODEL`) |
| dimensions | 768 (`EMBEDDING_DIMENSIONS`; 1536 and 3072 also available) |
| task types | `RETRIEVAL_DOCUMENT` for chunks, `RETRIEVAL_QUERY` for queries |

Documents and queries use different task types deliberately: a question and the code
that answers it are not the same kind of text, and the model is trained to embed them
differently.

`description` is one sentence per chunk from `GEMINI_MODEL`. It is what bridges
vocabulary gaps — T-07 asks "where does the final customer bill get rounded?" and the
answer is a function called `finalise_amount`.

**The source being summarised is untrusted input.** The fixture deliberately carries a
prompt injection in `customers.category_for` (T-18). The prompt frames the code as
data to be described and never as instructions; in practice the model described the
injection as *present* rather than acting on it, which is the behaviour we want.

Both calls retry transient failures (429, 500, 502, 503, 504, timeouts) with jittered
exponential backoff — see `app/search/retry.py`. This exists because a real import
lost all forty descriptions to `503 UNAVAILABLE — "This model is currently
experiencing high demand"`. Nothing was wrong with the code; the model was busy.

A **spent quota** (429 with "quota" or "billing") is treated differently from a
momentary rate limit: the remaining chunks are skipped immediately with one warning,
rather than each retrying four times to produce forty copies of the same message.

---

## Running it

Indexes and chunks both maintain themselves; these are the manual levers.

```bash
# status of everything, including both indexes
curl -s localhost:8000/api/v1/system/status | python -m json.tool

# search
curl -s "localhost:8000/api/v1/repositories/$REPO/search?q=finalise_amount&mode=keyword"
curl -s "localhost:8000/api/v1/repositories/$REPO/search?q=where+is+rounding+done&mode=hybrid"

# background indexing progress: indexing | ready | failed, embedded of total
curl -s "localhost:8000/api/v1/repositories/$REPO/index"

# re-chunk, re-describe and re-embed from the stored snapshot
curl -s -X POST "localhost:8000/api/v1/repositories/$REPO/index"
```

### Import responds first, indexing follows (#28)

Descriptions and embeddings are two Gemini calls per chunk, and the import used to wait
for all of them: about 40 s for `legacy_orders` (40 chunks) and over 2 minutes for chess
(122 chunks). Now the import stores the repository, graph and chunks, then responds;
keyword search works at once. Descriptions and embeddings run in the background, in
batches of 20, each batch written onto the stored chunks in place (never deleted and
re-inserted, so keyword search keeps finding them).

Timed on 24 Oct with Atlas in memory and Gemini replaced by a stub calibrated to the real
latency (4.5 s per description, 0.4 s per embedding, five at a time):

| Import | Responds, before | Responds, after | Indexing done, after |
|---|---|---|---|
| `legacy_orders`, 40 chunks | 39.3 s | 0.01 s | 39.3 s |
| chess folder, 122 chunks | 122.7 s | 0.04 s | 122.7 s |

Real Atlas adds its writes and the few seconds Atlas Search takes to see new chunks,
before and after alike. The Gemini calls are the same in number; only when they happen
changed.

Progress lives in `search_index_status`, one document per repository:

| `state` | Meaning | Vector and hybrid search |
|---|---|---|
| `indexing` | `embedded` of `total` chunks done so far | 503 `SEARCH_INDEX_NOT_READY`, with the progress |
| `ready` | finished | normal |
| `failed` | stopped; `reason` says why | 503 `SEARCH_INDEX_NOT_READY`, with the reason |
| no document | imported before #28, or with `INDEX_ON_IMPORT` off | as before |

Keyword search is never held up by indexing. `GET /repositories/{id}/index` returns the
document. `POST /repositories/{id}/index` re-runs indexing, and answers 409
`INDEXING_IN_PROGRESS` while it is already running, so the quota is never spent twice
on the same chunks.

- **Restarts.** Indexing lives in the server process. Each document records the host and
  process that own it; a reader on the same host that finds a different process (or
  this one with nothing running) marks it `failed`, and `POST .../index` runs it again.
  A document from another machine is left alone, since a teammate's import on the
  shared cluster may be indexing right now, and only the importing machine has the
  snapshot.
- **A spent quota stops the job.** A batch with no descriptions stops the descriptions;
  a batch with no embeddings stops the job. Without this, every remaining chunk would
  still send its own failing requests.

`POST /repositories/{repo_id}/index` is for when `INDEX_ON_IMPORT` was off, when a
Gemini key arrives after an import, when background indexing failed, or when the embedding model or dimension count
changes. It reads sources from the immutable snapshot, so it describes exactly the
code the symbols were extracted from.

`INDEX_ON_IMPORT=false` skips descriptions and embeddings at import, background ones
included. Chunks are still
built and stored, so keyword search works immediately — with no API key at all. The
test suite sets it off: enrichment is two model calls per chunk, and the import tests
import the fixture eleven times.

### Tests

```bash
cd backend
uv run pytest -q -m "not live and not docker"   # offline, what CI runs
uv run pytest -q -m live tests/integration/test_search_live.py
```

The live file is where T-06 and T-07 are actually proven. Neither can be tested
offline: they are claims about what a real Lucene analyser and a real embedding model
do, and the first version of this layer failed T-06 while every offline test passed.

Current state on the real cluster:

- `finalise_amount` → `pricing.finalise_amount` rank 1, score 43.0 (next hit 0.51)
- `finalise` → finds it too
- "where does the final customer bill get rounded?" → `pricing.finalise_amount`
  rank 1, `native_hybrid: true`
- "what does a shopper owe on their receipt?" — not one content word of which appears
  in `pricing.py` — → `pricing.finalise_amount` in the top 3 on vector search alone

That last pair is the case for hybrid existing: on the T-07 query the keyword index
ranks the rounding function **fifth**, the vector index ranks it **first**, and fusion
puts it first. `test_fusion_can_beat_keyword_search_on_its_own` keeps that honest — if
hybrid ever stops beating keyword there, it is costing a stage for nothing.

## Disabling Atlas breaks the journeys (Spec §11.2)

"MongoDB integration" only counts if taking Atlas away *breaks* the graph, search and
history journeys honestly, rather than the app quietly carrying on with a fake. It does.

| How Atlas is gone | `/system/status` | Every Dev 3 endpoint |
|---|---|---|
| `MONGODB_URI` unset | `mongodb: unavailable` | 503 `DATABASE_UNAVAILABLE` |
| URI set, nothing listening | `mongodb: unavailable` | 503 `DATABASE_UNAVAILABLE` (after the 5 s server-selection timeout) |
| Cluster host does not resolve | `mongodb: unavailable` | 503 `DATABASE_UNAVAILABLE` |

The search indexes report `unavailable` too. The demo-fixture list still answers,
because it is read from disk and only says what *can* be imported. Validation still
works: an unknown fixture id is a 422, not a 503.

How:

- `get_db()` raises 503 when no URI is configured.
- `app/persistence/routing.py::DatabaseAwareRoute` is the route class of the
  repositories and search routers. It turns driver errors that mean "cannot reach the
  database" (`ConnectionFailure`, `ConfigurationError`, authentication failures) into
  503 `DATABASE_UNAVAILABLE`. The message names the exception type only, never the
  driver text, which lists cluster hostnames.
- An import checks Atlas is reachable before writing anything to disk, and removes its
  half-written snapshot if anything fails partway. Without that, every failed import
  left a snapshot folder for a repository that existed nowhere.

`tests/integration/test_atlas_disabled.py` proves all of it with real driver clients
pointed at nothing (no fake database, no network needed): 40 cases. With the route
class removed, 18 of them fail.

**To show it live:**

```bash
cd backend
MONGODB_URI="mongodb://127.0.0.1:1/" uv run uvicorn app.main:app --port 8000
curl -s localhost:8000/api/v1/system/status          # mongodb: unavailable
curl -s localhost:8000/api/v1/repositories           # 503 DATABASE_UNAVAILABLE
```

**The history journey too:** Dev 2's run, plan and event endpoints return 503
`DATABASE_UNAVAILABLE` in the same cases (Dev 2's fix, merged as #11).

## Safeguards added on top of the search layer

- **Held-out cases can never be indexed.** `build_chunks` raises `HeldOutLeak` for any
  path under `challenge_cases/`, so nothing from it is chunked or stored. This is the
  second lock: ingestion already refuses a fixture tree that contains held-out cases.
- **Readiness follows Atlas's `queryable` flag.** While an updated index definition
  rebuilds, Atlas reports `BUILDING` but keeps serving the previous version, so
  `search_status()` stays `ok` and search keeps answering instead of returning 503.
  A brand-new index is not queryable and reads `building`.
- **`symbols.source_chunk_id` is set** once a symbol's chunk is stored, and cleared for
  symbols that no longer have one.

- **A missing snapshot is reported, not shown as an empty file.** Snapshots live on
  the disk of the machine that imported them; the graph lives in Atlas. Open a
  repository imported elsewhere and the module inspector and symbol endpoint still
  return their data, with `source: ""` and the warning *"Source unavailable: this
  repository's snapshot is not on this machine. Re-import the repository here to
  inspect its code."* (`app/graph/__init__.py`, live-tested in `test_graph_live.py`).

## Quota budget for demo day

The free tier allows **1000 `embed_content` requests a day** per model. It ran out on
23 Oct after about a dozen imports; the 429 said *"retry in 12h14m51s"*, so it resets
roughly a day after it is spent, not at midnight. Descriptions use `generate_content`
on `GEMINI_MODEL`, **the same model as Dev 1's agent**, so every description also
competes with the migration run.

### What each action costs

| Action | `embed_content` | `generate_content` |
|---|---|---|
| Import `legacy_orders`, first time | 40 | 40 |
| Import chess, first time | 122 | 122 |
| Import identical code again, same database and models | **0** | **0** |
| `POST /repositories/{id}/index` | 1 per chunk | 1 per chunk |
| Keyword search | 0 | 0 |
| Vector or hybrid search, new query text | 1 | 0 |
| The same query again, same server process | 0 | 0 |
| Restarting the backend | 0 | 0 |
| `uv run pytest -m "not live"` | 0 | 0 |
| `uv run pytest -m live tests/integration/test_search_live.py`, cache empty | 44 | 40 |
| The same, cache primed | 0 | 0 |

How the zeros are kept (#11):

- **Re-imports reuse stored answers.** A chunk id is derived from the snapshot hash, so
  the same id means byte-identical code. The background indexer copies the description
  and embedding of any stored chunk with that id, but only when `enriched_with` (the
  description model, embedding model and dimensions) also matches. Chunks indexed
  before this landed have no `enriched_with`, so the **first** import of each project
  after it pays once.
- **A repeated query reuses its vector.** Vectors are kept in memory per server process,
  up to 256 queries. A restart clears them.
- **A spent daily quota stops the work.** It is not retried (it used to be retried 4
  times, so 160 failing requests for a 40-chunk import), and the batch stops at the
  first refusal. A per-minute limit is still retried.
- **Restarts resume nothing.** Indexing interrupted by a restart is marked `failed` and
  waits for someone to press re-index.
- **Re-index is deliberate.** `POST .../index` re-describes and re-embeds everything,
  because it is what you use after changing models. It answers 409 while indexing runs.

### Don't, before recording

1. **Don't run a plain `uv run pytest`.** With `.env` filled it runs all 56 live tests,
   including real Gemini calls in Dev 1's and Dev 2's. Use `uv run pytest -m "not live"`.
2. **Don't import into a new database, or change `GEMINI_MODEL`,
   `GEMINI_EMBEDDING_MODEL` or `EMBEDDING_DIMENSIONS`.** Reuse matches on both, so either
   makes the next chess import pay 244 calls again.
3. **Don't press re-index** on chess or `legacy_orders` unless indexing failed.
4. **Don't run `--cache-clear` or delete `backend/.pytest_cache`.** That throws away the
   recorded answers.
5. **Rehearse with the scripted queries.** Each new query wording costs one embedding;
   keyword search costs nothing.
6. **Import chess and `legacy_orders` before judging,** check `GET
   /repositories/{id}/index` says `ready`, and don't make the first import of the day
   the one just before recording.

Open, for the team: is a paid key or a second key available as a backup? Not decided.

### Prime the replay cache: one run, on the machine that runs the tests

`tests/integration/model_replay.py` records each real Gemini answer the first time it is
asked and replays it afterwards. It records only successes, and the cache lives in that
checkout's `backend/.pytest_cache`, so prime it on the machine that will run the tests.

1. Check the key has quota in the AI Studio usage page. That page costs no request.
2. Check `.env` has `MONGODB_URI`, `GEMINI_API_KEY`, `GEMINI_EMBEDDING_MODEL` and
   `EMBEDDING_DIMENSIONS`. The test uses a throwaway `axolotl_test_*` database and drops
   it afterwards.
3. Run exactly this file, never the whole live suite:

   ```bash
   cd backend
   uv run pytest -m live tests/integration/test_search_live.py -v -s
   ```

   **Cost: 84 requests.** That is 44 `embed_content` (40 chunks + 4 distinct query
   texts; the 6 searches repeat two of them, and repeats replay) and 40
   `generate_content` (one description per chunk). The last line
   prints `Gemini calls this run: {...}`.
4. Run the same command again. It should print `embeddings_called: 0` and
   `descriptions_called: 0`, and the six embedding-dependent tests should pass rather
   than skip.

If the quota runs out partway, what succeeded stays recorded, and the next run asks only
for the rest.

## TODO (agreed, not started)

- **Prime the replay cache with one live run.** Steps and cost are in
  [Quota budget for demo day](#quota-budget-for-demo-day) below. Still not done: on
  24 Oct the cache held zero recorded answers.

- ~~**Isolate `tests/integration/test_search_live.py`.**~~ Done (Dev 2). It now runs
  in a throwaway `axolotl_test_<random>` database, dropped after asserting the prefix,
  and deletes its snapshot from disk. Verified by counting both before and after a
  run: 11 repositories and 400 chunks in the real database and 13 snapshot directories,
  unchanged, with no `axolotl_test_*` database left behind.

  Doing it exposed a real bug in `ensure_search_indexes()`, now fixed: Atlas refuses
  to create a search index on a collection that does not exist
  (`NamespaceNotFound: Collection '<db>.code_chunks' does not exist.`), which is the
  state of every brand-new database. The failure was only a log line, so search
  reported `unavailable` until the next restart after a first import. It worked on the
  `axolotl` database solely because `ensure_indexes()` runs first in the lifespan and
  creates the collection - an ordering dependency nothing stated or enforced.
  `ensure_search_indexes()` now creates `code_chunks` itself if absent.
- **`search_repository()` returns `{mode_used, native_hybrid, hits}`**, where §8 says
  `list[SearchHit]`. Agree one shape with Dev 1 and update §8 under §11.
- **Dynamic imports:** also flag `getattr` on modules and string-based imports as
  `unknown_dynamic`. Today only `importlib.import_module`, `__import__` and `reload`
  are detected.
- **Standard-library nodes:** a flag to group or hide them (`ext_json`, `ext_sys`, ...)
  in the graph.
- **`calls` and `inherits` edges:** best-effort, `resolution="heuristic"`, never used
  for scheduling.
