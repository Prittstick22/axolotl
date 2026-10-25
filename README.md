# Axolotl

**Agentic legacy-code migration with verified behaviour preservation.**

Axolotl imports a legacy Python project, builds a real dependency graph from its AST,
and migrates it module by module in dependency order. Every change is proposed by a
Gemini agent and then **independently verified**: the original and the migrated code are
run against the same recorded behaviour cases in a sandbox, and their outputs are
compared. The agent never decides whether it succeeded — the verifier does, and a unit
only advances when its required gates pass.

"Behaviour preserved" here means **agreement on the tested cases**. It is never a proof
of equivalence, and the final report always says so.

Built at **Encode London 2026** for the Google DeepMind Gemini 4 Argon track
(*Autonomous Agents That Ship*), MongoDB Atlas (*Build Anything, Change Everything*)
and Encode's *Agents That Ship* challenge.

---

## The demo: a chess game, Python 2.7 → Python 3.14

Will's terminal chess game is 15 modules of Python 2.7. Axolotl plans it as 14
migration units and migrates them in dependency order, with the original running on
Python 2.7 and every candidate on Python 3.14.

Live run `run_c86aff1bea77` (25 Oct, `gemini-4-argon`): **14 of 14 units verified on the
first attempt, 288 checks passed, 0 failed, in about 15 minutes.**

**No key? Use the demo build.** [Prittstick22/axolotl-demo](https://github.com/Prittstick22/axolotl-demo)
is this repository plus a replay mode: it replays that run's recorded Gemini answers,
while every patch, sandbox run and check still runs for real. It needs only Docker,
uv and Node, starts with one command (`demo\start.ps1` or `bash demo/start.sh`), and
migrates chess in about two minutes with no API calls. It's kept in its own
repository so the replay code never reaches this one.

## How it works

1. **Import and map.** The analyser parses the project with `ast` (it never imports or
   runs it) and stores every module, symbol and import edge in MongoDB Atlas.
2. **Plan, and a person approves.** Argon proposes migration units in dependency order;
   the plan is validated against the graph, and nothing runs until it's approved.
3. **Migrate.** For each unit, Argon works through tools: it reads modules, symbols,
   the graph and the unit's visible behaviour cases, searches the code, proposes
   full-file patches inside the unit's allowlist, asks for verification, reads the
   counterexamples and repairs.
4. **Verify independently.** The verifier runs the original and the candidate on the
   same cases, visible and held out, each in a fresh locked-down container, and
   compares their outputs. Only a persisted verification result can mark a unit
   passed; `finish_unit` never does.
5. **Report and deliver.** The verdict per unit, the whole project's diff, dependency
   and security changes, a summary, before-and-after docs per module, and a download
   of the migrated codebase as a zip.

Alongside the migration: keyword, vector and hybrid **search** over the code, and
**Ask**, which answers questions about the codebase with citations to files and lines.

## Quick start

**Prerequisites:** [uv](https://docs.astral.sh/uv/) (it installs and pins Python 3.12),
Node 20+, git, and a running Docker daemon (on Windows, Docker Desktop must be
**started**). You also need a MongoDB Atlas cluster that allows your IP address, and a
Gemini API key.

**1. Configure.** `MONGODB_URI` and `GEMINI_API_KEY` are the only two values you must
fill; everything else in `.env.example` has a working default.

```bash
git clone https://github.com/Prittstick22/axolotl.git && cd axolotl
cp .env.example .env     # then fill MONGODB_URI and GEMINI_API_KEY
```

Optional: `GEMINI_DESCRIPTION_MODEL=gemini-3.8-flash` writes search descriptions and
docs with Flash, keeping Argon's quota for the agent.

**2. Build the sandbox images.** Nothing can be verified without them: the runner chip
reads `unavailable`, and a run stops at the baseline. These are the commands CI runs
(`.github/workflows/ci.yml` is the source of truth if they drift). Run them again after
pulling runner changes; a stale image fails the runner check rather than announcing
itself.

```bash
docker build -t axolotl-runner:latest runner/
docker build -t axolotl-runner:py2.7  --build-arg PYTHON_VERSION=2.7  --build-arg PYTEST_VERSION=4.6.11 runner/
docker build -t axolotl-runner:py3.14 --build-arg PYTHON_VERSION=3.14 --build-arg PYTEST_VERSION=8.4.2  runner/
docker build -f runner/Dockerfile.behaviour --build-arg PYTHON_IMAGE=python:2.7.18-slim -t axolotl-chess-cases:py27  runner/
docker build -f runner/Dockerfile.behaviour --build-arg PYTHON_IMAGE=python:3.14-slim  -t axolotl-chess-cases:py314 runner/
```

`axolotl-runner:latest` is the Python 3.12 image the `legacy_orders` test fixture runs
on; the `py2.7` and `py3.14` pair is the baseline and the candidate for a Python 2
migration.

**3. Run it.**

```bash
cd backend  && uv sync && uv run uvicorn app.main:app --port 8000     # terminal 1
cd frontend && npm install && npm run dev                              # terminal 2
```

Open <http://localhost:5173>. The status chips show the **real** state of MongoDB, the
Atlas search indexes, the sandbox runner and Gemini; with an empty `.env` every chip
reads `unavailable`, because nothing here fakes a working dependency.

**4. Migrate chess.** On *New migration*, **chess** is preselected: it ships in this
repository, so there's nothing to upload. Import it, open **Plan**, keep **Python 2 to
Python 3**, then **Create plan**, **Approve** and **Start migration**. A live run takes
about 15 minutes.

*Uploading `demo/chess` as a folder also works: an exact content match is recognised as
the trusted fixture, which is what makes it executable. A modified copy is imported for
reading only.*

API docs: <http://localhost:8000/docs> · Status: `curl -s http://localhost:8000/api/v1/system/status`
· Tests: `cd backend && uv run pytest`

## Architecture

```
            ┌────────────── React 19 + Vite ──────────────────┐
            │  Overview · Graph · Module inspector · New       │
            │  migration · Plan · Run · Verification · Changes │
            │  Dependencies · Report · Docs · Search and Ask   │
            └───────────────────────┬─────────────────────────┘
                                    │  REST, /api/v1
            ┌───────────────────────▼─────────────────────────┐
            │  FastAPI backend                                │
            │                                                 │
            │  ingestion ── analysis ── graph    (Dev 3)       │
            │        │          AST, no imports                │
            │        ▼                                         │
            │  orchestration ── reports          (Dev 2)       │
            │        │   scheduler, state machine,             │
            │        │   event journal, run worker             │
            │        ├────────────► agents       (Dev 1)       │
            │        │              Gemini, tool loop          │
            │        └────────────► verification (Dev 4)       │
            │                       sandbox, differential       │
            └───────────┬──────────────────────┬──────────────┘
                        │                      │
              MongoDB Atlas 8.0        Docker, --network none
              system of record,        one container per case run
              Search + Vector
```

The single rule the design turns on: **only a persisted `VerificationResult` can mark
a unit passed.** The agent's own report of success is advisory.

## Exact models and settings

| | |
|---|---|
| planning, migration, report narrative, Ask | `gemini-4-argon` (Interactions API), thinking level `medium` |
| search descriptions and module docs | `GEMINI_DESCRIPTION_MODEL`; we used `gemini-3.8-flash` (empty means `GEMINI_MODEL`) |
| embeddings | `gemini-embedding-001`, 768 dimensions, cosine |
| embedding task types | `RETRIEVAL_DOCUMENT` for chunks, `RETRIEVAL_QUERY` for queries |

**The agent's tools:** `get_repository_overview`, `get_dependency_graph`,
`get_module_source`, `get_symbol`, `search_repository`, `get_behaviour_cases` (visible
cases only), `propose_patch`, `run_verification`, `get_counterexamples` and
`finish_unit`.

## MongoDB Atlas: the system of record

Axolotl keeps everything it knows in MongoDB Atlas (M10, MongoDB 8.0): the
repository snapshot metadata, every symbol and import edge of the dependency graph,
the code chunks that search runs over, migration plans and runs, an append-only event
journal, behaviour cases, verification results, counterexamples, reports and generated
docs. There is no in-memory fallback. Switch Atlas off and the graph, search and run
history stop with a clear `503 DATABASE_UNAVAILABLE`; they never quietly answer from a
fake.

**Atlas Search** (`code_chunks_text`) finds code by exact name or error string. A
custom analyser splits identifiers, so `finalise_amount` and `finalise` both land on
`pricing.finalise_amount`.
**Atlas Vector Search** (`code_chunks_vector`, Gemini `gemini-embedding-001`, 768
dimensions, filtered by repository) finds code by meaning: "where does the final
customer bill get rounded?" finds `finalise_amount`, whose name shares none of those
words.
**Native hybrid search** fuses both with `$rankFusion`. Each result shows its keyword
rank and vector rank, and the response says whether native fusion actually ran
(`native_hybrid`); it is measured, never assumed. The agent's `search_repository`
tool and Ask use the same search, choosing the mode per query.

All indexes, regular and search, are created from code at startup and are safe to
re-run. Search readiness comes from Atlas itself: while an index builds, search
answers `503 SEARCH_INDEX_NOT_READY` instead of an empty list that would look like
"no matches". The run journal gets its sequence numbers from an atomic `$inc`, with a
unique `(run_id, sequence)` index as a backstop; 50 concurrent writers produce 50
distinct numbers.

See [docs/mongodb-bounty.md](docs/mongodb-bounty.md) for claim-by-claim evidence, and
[docs/mongodb-atlas.md](docs/mongodb-atlas.md) for the collections, index definitions and
the gotchas behind them.

## Execution limits and sandbox policy

Every case run happens in a fresh container, with these flags and no exceptions
(`app/sandbox/runner.py`):

```
--network none          --user 65534:65534      --read-only
--memory 256m           --cpus 1                --pids-limit 64
--security-opt no-new-privileges                --cap-drop ALL
--tmpfs /tmp:size=16m   --rm
```

60-second default timeout per run. A timeout, a kill or oversized output fails the
`sandbox_policy` gate rather than being discarded.

Beyond the container: **executable migration is only ever enabled for allowlisted
trusted fixtures.** An upload qualifies only by being an exact content match for a
tree in this repository (`app/ingestion/trust.py`); one changed byte, one extra file,
one moved file and it stays static-analysis only. The analyser never imports or
executes the code it reads — it parses with `ast`, and Python 2-only syntax is
rewritten in memory for parsing only, never on disk.

The exact profile, and what it does **not** protect against, is in
[runner/policy.md](runner/policy.md).

## Coverage caveats

- Agreement on recorded cases is not equivalence. Untested behaviour can change
  undetected, and the report says so.
- Held-out cases are the independent half of the suite. They are never shown to the
  model unless a repair exposes one deliberately, and never leak through a read path:
  comparisons, counterexamples, gate logs and the event journal are all redacted for
  a sealed case.
- An inconclusive gate is never a pass. If the sandbox cannot run, the result is
  inconclusive, not success.
- Chess has 24 behaviour cases, 12 visible and 12 held out. `legacy_orders` has 26,
  16 and 10.
- No live chess run has needed a repair: in both, every unit passed on its first
  patch. The repair loop is exercised on `legacy_orders` (`run_bb0da2e79534`) and by
  the test suite.

## Tests

981 offline backend tests (some in real sandbox containers), 56 live tests against the
real Atlas cluster and Gemini behind `-m live`, and the frontend's Vitest suite. A plain
`uv run pytest` excludes the live ones, because they spend Gemini quota. CI runs the
offline, container and frontend suites on every pull request.

## Known issues

| | |
|---|---|
| Live runs are slow and cost quota | a live chess migration takes about 15 minutes of Argon calls. Each Argon request times out after 240 s, with no retry |
| Uploaded snapshots are machine-local | the built-in chess and `legacy_orders` fixtures are rebuilt from this checkout on any machine, but an uploaded repository can only be re-indexed on the laptop that imported it (409 `SNAPSHOT_UNAVAILABLE`) |
| Report runtime timings are conditional | `performance` is filled only for a completed `trusted_docker` run whose final verification passed on the accepted revision; otherwise the Report leaves the panel out rather than showing zeroes |
| `legacy_orders` is a test fixture, not a demo | it's what the backend tests and the sandbox run on. The demo is chess |

## Fixture provenance

- **chess** — Will's own terminal chess game, Python 2.7, used with permission. Will
  wrote it with AI assistance and published it as part of this project; attribution and
  permission to publish were confirmed with him on 24 Oct
  ([#8](https://github.com/Prittstick22/axolotl/issues/8)). Per-file hashes are in
  [`docs/chess-source-manifest.json`](docs/chess-source-manifest.json). **Those hashes
  were taken on a checkout with CRLF line endings**, so most don't match the same
  source here; the manifest is provenance, not a checksum, and the trust gate
  normalises line endings for exactly that reason.
- **legacy_orders** — written for this project. A small order service whose pricing
  rule rounds per line before tax, which is the behaviour a naive rewrite breaks:
  77 of 300 single-line inputs diverge, and the fixture's own 12 tests all pass on a
  banker's-rounding rewrite. Only the held-out differential catches it (2.65 → 2.64).

## Team

| Role | Name | GitHub |
|---|---|---|
| Dev 1: Argon agent, and project manager | Dev | [`Prittstick22`](https://github.com/Prittstick22) |
| Dev 2: orchestrator and integration | Mo | [`mh-husssain`](https://github.com/mh-husssain) |
| Dev 3: graph and Atlas | Caroline | [`Caroline3110`](https://github.com/Caroline3110) |
| Dev 4: verifier and sandbox | William (Will) | [`WilliamParis1`](https://github.com/WilliamParis1) |
| Dev 5: frontend | Abhay | [`PanotAbhay`](https://github.com/PanotAbhay) |

`Dev N` in the docs, the layout below, ticket titles and the `dev N` labels means the
person above.

## Credits

- **Will** — the chess source, used with permission.
- **Gemini** (`gemini-4-argon`, `gemini-3.8-flash`, `gemini-embedding-001`) — planning,
  migration, Ask, descriptions, docs, embeddings.
- **MongoDB Atlas** — system of record, Atlas Search, Vector Search, `$rankFusion`.
- **fissix** (PSF licence) — the maintained standalone copy of `lib2to3`, used to read
  Python 2-only syntax. `lib2to3` is deprecated, gone in Python 3.13, and absent from
  the Python 3.12 that CI installs.
- **FastAPI, Pydantic, PyMongo, NetworkX, React, Vite, Tailwind, pytest, ruff, uv.**

---

## For contributors

### Documentation

| Document | What's in it |
|---|---|
| [docs/ONBOARDING.md](docs/ONBOARDING.md) | **Start here.** Setup, the rules, and a per-role guide to what exists |
| [docs/PROJECT_STATUS.md](docs/PROJECT_STATUS.md) | What works, what is broken and what is left, with the GitHub tickets |
| [docs/IMPLEMENTATION.md](docs/IMPLEMENTATION.md) | Everything implemented, by area, with timeline, evidence and contributors |
| [docs/architecture.md](docs/architecture.md) | How the pieces fit, the state machine, where each safety property is enforced |
| [docs/api-contracts.md](docs/api-contracts.md) | Every endpoint, every error code, implementation status |
| [docs/mongodb-atlas.md](docs/mongodb-atlas.md) | Collections, Search / Vector / Hybrid indexes |
| [docs/argon-agent.md](docs/argon-agent.md) | Model adapter, tool schemas, prompts |
| [docs/chess-demo.md](docs/chess-demo.md) | The chess fixture and its behaviour cases |
| [docs/presentation.md](docs/presentation.md) | Will's introduction and judge Q&A |
| [docs/demo-script.md](docs/demo-script.md) | The timed 3-minute chess demo, preflight and fallback |
| [docs/submission-videos.md](docs/submission-videos.md) | The video scripts, shot lists and upload checks |
| [docs/rehearsal-log.md](docs/rehearsal-log.md) | Actual-run evidence and rehearsals |
| [runner/policy.md](runner/policy.md) | The exact sandbox profile, and what it does **not** protect against |
| [demo/legacy_orders/README.md](demo/legacy_orders/README.md) | The test fixture, its seeded rounding rule and parameters |
| [BUILD_LOG.md](BUILD_LOG.md) | Timestamped record of what was built when, and the open decisions |
| [docs/planning/](docs/planning/) | The Spec, SHARED_CONTRACTS and the five dev briefs |

`SHARED_CONTRACTS.md` wins on names, shapes and ownership. The Spec wins on product and
safety rules. Change a contract only through SHARED_CONTRACTS §11.

### Layout

```text
backend/app/
  main.py config.py errors.py       Dev 2  app factory, settings, error envelope
  models/contracts.py               Dev 2  §4 models - everyone imports these
  api/                              one router file per owner
  orchestration/ reports/           Dev 2  scheduler, state machine, events, worker, reports
  persistence/ ingestion/
    analysis/ graph/ search/        Dev 3  Atlas, AST, dependency graph, hybrid search
  agents/                           Dev 1  Gemini adapter, tool schemas, prompts, Ask
  docs/                             Dev 1  generated module docs
  sandbox/ verification/            Dev 4  Docker runner, workspaces, differential verifier
frontend/                           Dev 5  React + TypeScript UI
runner/                             Dev 4  sandbox images, harness, policy
demo/                               Dev 4  chess and legacy_orders, visible and held-out cases
```

### Branching

Short-lived `devN/<topic>` branches, PR into `main`. **`main` must always boot.** The
repository is public, by decision (24 Oct). Never commit `.env` or any key.

### Deviations from SHARED_CONTRACTS

- **Chess, Python 2.7 → 3.14, is the only demo.** The Spec lists Python 2 → 3 as a
  hackathon non-goal; the team chose it on 24 Oct, with no backup demo.
- **PyMongo, not Motor.** `pymongo>=4.9`'s `AsyncMongoClient` replaces Motor, which
  reached end of life in May 2026. `get_db()` returns an `AsyncDatabase`; the §8
  signature is otherwise unchanged.
- **React 19, not React 18.** The current Vite template ships it. Ratified by Dev 5 on Fri 23 Oct.
- **§6.2 vs `RunState`.** The Spec's state diagram and the contract enum disagree;
  resolved in favour of the enum per §0. **Acked** in SHARED_CONTRACTS §11.
- **`search_repository` returns a dict**, not `list[SearchHit]`: the caller needs
  `mode_used` and `native_hybrid` as well as the hits. **Acked.**
- **`folder_upload` origin** added for the chess upload screen. Additive, and it grants
  no execution rights of its own. **Acked.**
- **`fissix` dependency** (PSF licence) to read Python 2-only syntax: `lib2to3` is
  deprecated, gone in Python 3.13, and absent from the Python 3.12 CI installs.

All recorded in [BUILD_LOG.md](BUILD_LOG.md), with the §11 acks in
[SHARED_CONTRACTS](docs/planning/SHARED_CONTRACTS.md#11-changing-a-contract).
