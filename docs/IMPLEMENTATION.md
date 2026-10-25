# Implementation record

What has been built in Axolotl, and by whom. This repository starts from a single
commit; the work below was developed in an earlier private repository with full commit
history (74 commits, 13 merged pull requests). [`BUILD_LOG.md`](../BUILD_LOG.md) is the
detailed narrative of that work.

For how the pieces fit, read [`architecture.md`](architecture.md). For every endpoint and
error code, read [`api-contracts.md`](api-contracts.md).

---

## 1. What Axolotl does

Axolotl imports a legacy Python project, builds a real dependency graph from its AST,
and migrates it module by module in dependency order. A Gemini agent proposes each
change; an **independent verifier** runs the original and the candidate against the same
recorded behaviour cases in a Docker sandbox and compares their outputs. A unit advances
only when its required gates pass. The agent never decides whether it succeeded.

"Behaviour preserved" means agreement on the tested cases, never a proof of equivalence,
and the final report always says so.

## 2. Build order

| Phase | What landed |
|---|---|
| 1 | Repo layout and planning docs; FastAPI skeleton with shared contracts and real status checks; Vite/React status chips; demo fixture and sandbox runner; orchestration core (scheduler, state machine, event journal, plans, run worker, controller, reports) |
| 2 | 19 findings from an adversarial review of the orchestration core fixed, with regression tests |
| 3 | Import, AST and dependency graph; CI; workspaces, Docker runner and the five verification gates |
| 4 | Gemini quota investigation; **first full end-to-end run** on real Atlas and Docker; rename to Axolotl; Atlas client and idempotent indexes |
| 5 | UI design template; search layer (chunking, embeddings, Atlas Search and Vector, `$rankFusion`); Gemini planning and migration agent; chess demo input; search, graph and Atlas-down hardening |
| 6 | Frontend app shell and screens 01–04; agent merged into main |

## 3. Backend

FastAPI on Python 3.12, managed with `uv`. MongoDB Atlas is the only store; there is no
in-memory fallback.

### 3.1 Foundations

- `app/main.py`, `config.py`, `errors.py`: app factory, settings from `.env`, and a single
  error envelope. A blank `.env` boots cleanly and reports every dependency as
  unavailable rather than crashing.
- `app/models/contracts.py`: the shared Pydantic models every package imports, mirrored
  in `frontend/src/types/contracts.ts`.
- `GET /api/v1/system/status` does a live Mongo `ping`, lists the Atlas search indexes,
  checks the Docker runner and calls Gemini `models.get`. It answers 200 while reporting
  a dependency as down, so the status chips stay visible during an outage.

### 3.2 Ingestion, analysis and the dependency graph

- `ingestion/safety.py`: rejects absolute paths, drive letters, `..` traversal, symlinks
  and decompression bombs; caps size, file count and depth; skips `.git`, virtualenvs,
  `node_modules`, `.env` and key files.
- `analysis/astwalk.py`: static AST extraction of modules, classes, functions, methods
  and nested functions with signatures, decorators and line spans. Nothing is imported
  or executed; a syntax error becomes a warning and a `partial` repository.
- `analysis/identifiers.py`: every id is a pure function of content, so re-importing the
  same tree yields the same ids.
- `graph/builder.py`: resolves absolute, relative and `from pkg import submodule`
  imports. An edge is `confirmed` only when it resolves to a file in the snapshot; only
  confirmed local edges are used for scheduling. Dynamic imports are recorded as
  unresolved, never guessed.
- Snapshots are copied, hashed and marked read-only. Uploaded ZIPs are never executable
  (`409 REPOSITORY_NOT_EXECUTABLE`).
- `GET /repositories/{id}/runtime` reads declared Python versions statically;
  `setup.py` is never imported.

### 3.3 Orchestration

- `orchestration/scheduler.py`: pure and synchronous. Collapses import cycles into one
  unit with `nx.condensation`, releases a unit only when all its dependencies have
  passed, skips everything downstream of a blocked unit (T-13), and computes the
  impacted reverse dependants whose cases must re-run.
- `orchestration/states.py`: every run and unit transition goes through one function
  that raises on an illegal move. A run reaches `completed` only through final
  verification with green gates.
- `orchestration/events.py`: append-only run journal. Sequence numbers come from an
  atomic `$inc` with a unique `(run_id, sequence)` index as a backstop; payloads are
  redacted of secrets, then truncated to 2 KB.
- `orchestration/controller.py`: the only surface the agent can act through. It
  enforces the patch budget and the per-unit file allowlist, accepts only `agent_*`
  events, and `is_verified()` reads the persisted verification result and nothing else.
- `orchestration/worker.py`: runs a plan in the background: baseline, units in
  eligible order, final verification (always), verdict and report. Runs left
  unfinished by a restart are marked `failed` at startup.
- `orchestration/ports.py`: resolves the agent, verifier and graph functions lazily, so
  each owner's code is picked up without edits. A missing integration produces an
  inconclusive or blocked outcome, never a pass.
- `reports/`: the run report, built from recorded evidence only. Repeated attempts are
  deduplicated so verified-case counts are not inflated.

### 3.4 Sandbox and verification

- `runner/`: the `axolotl-runner` image and harness. Containers run with
  `--network none`, a read-only `/code` mount and a writable `/tmp`, as uid 65534.
  `runner/policy.md` records the profile and what it does **not** protect against.
- `sandbox/workspaces.py`: a fresh copy of the last accepted revision for every attempt.
  A patch is validated in full before any file is written.
- `verification/`: `capture_baseline`, `verify_unit` and `verify_final`. Unit gates run in
  order: `compile`, `differential`, `impacted_tests`, `existing_tests`, `sandbox_policy`.
  Final gates are `final_integration` and `sandbox_policy`. Outputs are compared on
  canonical encoding; exceptions match on type name only. Without Docker every gate is
  inconclusive; nothing ever runs on the host.
- Held-out cases are never shown to the model, and the search layer refuses to chunk
  them (`HeldOutLeak`).

### 3.5 Search (MongoDB Atlas)

- `search/chunking.py`: one chunk per function, method, class and module, with
  deterministic ids.
- `search/descriptions.py`, `embeddings.py`: a one-sentence Gemini description per chunk
  (source framed as untrusted data) and `gemini-embedding-001` vectors at 768 dimensions.
  A failed description or embedding stays `None`, never invented text or a zero vector.
- `search/indexes.py`: creates both search indexes from code at startup, rebuilds a
  `FAILED` index, updates a drifted definition, and creates `code_chunks` if it does not
  exist yet. Readiness follows Atlas's `queryable` flag.
- `search/__init__.py`: keyword search (`code_chunks_text`, with a custom analyser that
  splits identifiers), vector search (`code_chunks_vector`), and hybrid search with
  native `$rankFusion`, falling back to Python reciprocal-rank fusion. Each response
  reports whether native fusion actually ran.
- `search/retry.py`: jittered backoff on transient Gemini errors; a spent daily quota
  stops after one warning.
- `POST /repositories/{id}/index` re-runs enrichment, and returns
  `409 SNAPSHOT_UNAVAILABLE` when the snapshot was imported on another machine.

### 3.6 Gemini agent

`app/agents/` (see [`argon-agent.md`](argon-agent.md)):

- `gemini_client.py`: isolates the `google-genai` Interactions API, chains
  `previous_interaction_id`, applies a 240 s timeout and bounded backoff on rate limits.
  The model comes from `GEMINI_MODEL` with no silent fallback.
- `propose_plan`: structured `submit_plan` call, validated for membership, uniqueness and
  dependency order.
- `run_unit_session`: tool loop over repository overview, graph, module source, symbol
  lookup, search, visible behaviour cases and `propose_patch`; continues with
  counterexamples after a failed verification. It returns `verified` only when
  `controller.is_verified()` says so.
- `narrate_report`: optional prose built only from recorded evidence, labelled as
  model-generated.
- Prompt-injection tests cover hostile instructions embedded in source files.

### 3.7 API surface

25 routes under `/api/v1`:

| Area | Routes |
|---|---|
| System | `GET /health`, `GET /system/status`, `GET /migration-targets` |
| Repositories | `GET /demo-fixtures`, `POST /repositories/import`, `GET /repositories`, `GET /repositories/{id}`, `.../graph`, `.../modules/{module_id}`, `.../symbols/{symbol_id}`, `.../runtime` |
| Search | `GET /repositories/{id}/search`, `POST /repositories/{id}/index` |
| Plans and runs | `POST /repositories/{id}/plans`, `GET /plans/{id}`, `POST /plans/{id}/approve`, `POST /plans/{id}/runs`, `GET /runs/{id}`, `POST /runs/{id}/cancel`, `.../events`, `.../units/{unit_id}`, `.../diff`, `.../report`, `.../artefacts`, `GET /artefacts/{id}/download` |

With Atlas unreachable, every database-backed route answers
`503 DATABASE_UNAVAILABLE` rather than a bare 500.

## 4. Frontend

Vite, React 19 and TypeScript, styled with shadcn/ui on Base UI, Tailwind v4, Phosphor
icons and IBM Plex, following the template in [`design/`](design/).

- App shell with live system-status chips on every screen.
- **01 New migration**: import the demo fixture or upload a project folder. The browser
  drops caches, build output, binaries and secrets such as `.env` before uploading.
- **02 Overview**: modules table, snapshot counts, declared Python version, warnings.
- **03 Graph**: React Flow with a dagre layout; selecting a module highlights its
  dependants, `/` filters, and the graph re-fits on resize.
- **04 Module inspector**: symbols, imports, dependants and read-only source in Monaco.
- **05 Plan**: pick a migration target, have Argon order the units, review each unit's
  risk and visible cases, then approve the plan and start a run.
- **06 Run**: run state, current unit, attempt and test counts, the graph coloured by
  unit state (a node shows its attempt once a repair starts), the gate results of the
  latest verification, and a timeline of the run's events. Tool calls are paired with
  their results, IDs are replaced by file paths, and Argon's rationale and reasons sit
  next to what the verifier found. It polls every 1.5 s until the run's closing event
  arrives. A run can be cancelled from the header.
- **07 Verification / Diff**: the gate table, attempt history and the per-case old and
  new outputs for one unit, with sealed held-out cases shown as mismatched but not
  valued. Routed at `run/:unitId`.
- **08 Final report**: the verdict, what was migrated, the counterexamples and the
  NOT-A-PROOF statement.
- **09 Search**: keyword, vector or hybrid search over the code chunks, each hit
  showing its match method and its keyword and vector ranks, and leading to its
  module on the graph. Routed at `search`.

## 5. Demo inputs

> **Decision, 24 Oct 2026:** the demo is Will's chess game, migrated from Python 2.7 to Python 3.14.
> **Chess is the only demo; there is no backup.** `legacy_orders` stays in the repo only as the test
> fixture until chess replaces it ([#54](https://github.com/Prittstick22/axolotl/issues/54)). Chess can now be
> imported, planned and verified - the dual-runtime sandbox ([#22](https://github.com/Prittstick22/axolotl/issues/22)),
> its own behaviour cases ([#23](https://github.com/Prittstick22/axolotl/issues/23)) and a supported
> `python2_to_3` target ([#21](https://github.com/Prittstick22/axolotl/issues/21)) all landed on 24 Oct.
> **No chess run has reached a verdict yet** ([#4](https://github.com/Prittstick22/axolotl/issues/4),
> [#7](https://github.com/Prittstick22/axolotl/issues/7)), so the chain is assembled and unproven.

- `demo/legacy_orders/`: the test fixture (not a demo), with visible and held-out behaviour
  cases and a seeded rounding rule. A banker's-rounding patch keeps all 12 of its own
  tests green but is caught by the held-out differential (2.65 → 2.64).
- `demo/chess/`: a terminal chess game supplied unchanged, with a SHA-256 provenance
  manifest. It runs on Python 2.7.18 and 3.14; its integration as a migration target
  is not finished (see [`chess-demo.md`](chess-demo.md)).
- `backend/tests/fixtures/cycles/`: an import-cycle fixture that is parsed and scheduled
  as a single unit (T-05).

## 6. Evidence

- **First full end-to-end run** against real Atlas (M10, MongoDB
  8.0.34) and Docker: 10 files imported, the exact 7 dependency edges, 26 baseline cases
  captured with 0 failures, blocked units correctly skipping their dependants, and a
  `completed_with_warnings` verdict whose report stated that nothing was verified.
  No agent existed yet, so no unit was migrated.
- **Search on the real cluster**: keyword search finds `pricing.finalise_amount` by name;
  hybrid search puts it first for "where does the final customer bill get rounded?",
  where keyword search alone ranks the wrong function first.
- **Live agent checks**: two `gemini-4-argon` tests passed (declared-tool handshake and
  structured planning).
- **A full chess migration**: run `run_70e126c3acf2`, 24 Oct, ended `completed` across
  all 14 units of the chess fixture against `gemini-4-argon`. Argon's responses are
  recorded in `demo/replays/run_70e126c3acf2.json` on `demo/argon-replay`, which
  replays the run without calling Gemini. The recording holds the model's side only,
  so the gates are re-run for real on every replay.
- **Agent patches through the verifier**: earlier, in run `run_bb0da2e79534` on Atlas
  Local, Argon's patches for `customers.py` and `pricing.py` passed verification. That
  run ended `completed_with_warnings` at 2 of 6 units, and the chess run above
  supersedes it as the stronger evidence.
- **Tests**: 827 offline backend tests, 25 in real sandbox containers, 56 live Atlas
  and Gemini tests behind `-m live`, and 20 frontend vitest tests. A plain
  `uv run pytest` excludes the live ones (`addopts`), because one live run costs about
  48 Gemini embedding calls against a 1000-a-day free tier. CI
  (`.github/workflows/ci.yml`) runs lint, offline tests, the container suite and the
  frontend build on every pull request.

## 7. Deviations from the planning documents

- PyMongo's `AsyncMongoClient` instead of Motor, which reached end of life in May 2026.
- React 19 instead of React 18, ratified by Dev 5.
- The run state machine follows the contract enum where the Spec's diagram disagrees.
- Optional additive fields: runtime timings on reports, and `qualified_name` and `kind`
  on search hits.

## 8. Known gaps

- **No cold-start reproduction yet.** A live chess run reached a verdict on 24 Oct —
  `run_70e126c3acf2`, `completed` across 14 units against `gemini-4-argon`, recorded
  for replay on `demo/argon-replay` ([#4](https://github.com/Prittstick22/axolotl/issues/4)). What has not happened is someone
  starting from a fresh clone with only the README and reproducing it ([#7](https://github.com/Prittstick22/axolotl/issues/7)).
- A cold start needs five Docker images built, which the README now lists. It used to
  say only "Docker running", so anyone following it got no images, a runner chip
  reading `unavailable`, and nothing explaining why ([#7](https://github.com/Prittstick22/axolotl/issues/7)).
- The Verification/Diff, Report and Search screens all landed on 24 Oct; no screen
  renders a placeholder any more.
- `calls` edges are not extracted.
- The Gemini free-tier embedding quota is 1000 requests a day; the replay cache needs
  one run with quota available to populate it.
- Re-indexing only works on the machine that imported the repository.

## 9. Contributors

From the original commit history:

| Contributor | Areas |
|---|---|
| mh-husssain | Infrastructure, backend foundations, orchestration, reports, ingestion and graph, CI, sandbox and verification, search layer, hardening |
| caroline ajami | Atlas persistence and indexes, search fixes and held-out guard, graph and search live tests, model replay, MongoDB bounty write-up |
| Dev | Axolotl rename and documentation reconciliation, Gemini planning and migration agent, chess demo input, ZIP upload and runtime endpoint |
| PanotAbhay | Rename follow-through, runtime report fields, UI design template, frontend app shell and screens 01–04 |

The chess source was supplied by Will.
