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

> ### 👉 New here? Read [`docs/ONBOARDING.md`](docs/ONBOARDING.md) first.
> It has setup, what's already built, and what to do next for your role.

---

## Project name and existing setup

The project is now **Axolotl**, and so are the `axolotl` database, `axolotl-runner`
image, `.axolotl_workspaces` directory and package names, and the GitHub repository is
[`Prittstick22/axolotl`](https://github.com/Prittstick22/axolotl). If your `.env` or runner
image predates the rename, see "Dev 5 renames to Axolotl" in [BUILD_LOG](BUILD_LOG.md).

All 11 Markdown documents supplied in the team's "Encode Hackathon Projects" folder
are available in [`docs/planning/`](docs/planning/). The repository versions retain
the newer PyMongo async corrections in place of the supplied Motor instructions.

> **Decision, 24 Oct 2026: chess is the only demo.** Axolotl is being built for one demo:
> Will's terminal chess game, migrated from Python 2.7 to Python 3.14. There is no backup
> demo. The `legacy_orders` fixture stays in the repo only as the test fixture the backend
> tests and the sandbox run on, until chess replaces it ([#54](https://github.com/Prittstick22/axolotl/issues/54)). Chess can't be
> migrated yet: the sandbox has no
> Python 2.7 or 3.14 runtime, there are no chess behaviour cases, and `python2_to_3` is
> unsupported. That work is tracked in [#30](https://github.com/Prittstick22/axolotl/issues/30); the full plan is in
> [docs/PROJECT_STATUS.md](docs/PROJECT_STATUS.md). Planning documents describing the
> orders demo reflect the earlier plan.

## Quick start

```bash
cp .env.example .env     # then fill MONGODB_URI and GEMINI_API_KEY from the team vault
```

```bash
cd backend && uv sync && uv run uvicorn app.main:app --reload --port 8000
```

```bash
cd frontend && npm install && npm run dev
```

Open <http://localhost:5173>. The status chips show the **real** state of MongoDB, the
Atlas search indexes, the sandbox runner and Gemini.

**With an empty `.env` every chip reads `unavailable`. That is the honest answer, not a
failure of the app** — nothing here fakes a working dependency.

```bash
curl -s http://localhost:8000/api/v1/system/status     # same thing from the API
```

API docs: <http://localhost:8000/docs> · Tests: `cd backend && uv run pytest`

**Prerequisites:** Python 3.12 (uv installs and pins it), [uv](https://docs.astral.sh/uv/),
Node 20+, Docker running, and a MongoDB Atlas cluster plus a Gemini API key.

---

## Documentation

| Document | What's in it |
|---|---|
| [docs/ONBOARDING.md](docs/ONBOARDING.md) | **Start here.** Setup, the rules, and a per-role guide to what exists and what to build |
| [docs/PROJECT_STATUS.md](docs/PROJECT_STATUS.md) | What works, what is broken and what is left, with the GitHub tickets and the product features |
| [docs/IMPLEMENTATION.md](docs/IMPLEMENTATION.md) | Everything implemented so far, by area, with timeline, evidence and contributors |
| [docs/architecture.md](docs/architecture.md) | How the pieces fit, the state machine, where each safety property is enforced, what's real today |
| [docs/api-contracts.md](docs/api-contracts.md) | Every endpoint, every error code, implementation status |
| [docs/safety-and-limitations.md](docs/safety-and-limitations.md) | Sandbox protections and their honest limits *(Dev 4)* |
| [docs/mongodb-atlas.md](docs/mongodb-atlas.md) | Collections, Search / Vector / Hybrid indexes *(Dev 3)* |
| [docs/argon-agent.md](docs/argon-agent.md) | Model adapter, tool schemas, prompts *(Dev 1)* |
| [docs/demo-script.md](docs/demo-script.md) | The 3-minute chess demo sequence *(demo work, not MVP; unassigned)* |
| [runner/policy.md](runner/policy.md) | The exact sandbox profile, and what it does **not** protect against |
| [demo/legacy_orders/README.md](demo/legacy_orders/README.md) | The test fixture (not a demo), its seeded rounding rule and parameters |
| [BUILD_LOG.md](BUILD_LOG.md) | Timestamped record of what was built when, and the open decisions |
| [docs/planning/](docs/planning/) | The Spec, SHARED_CONTRACTS and the five dev briefs |

`SHARED_CONTRACTS.md` wins on names, shapes and ownership. The Spec wins on product and
safety rules. Change a contract only through SHARED_CONTRACTS §11.

---

## Layout

```text
backend/app/
  main.py config.py errors.py       Dev 2  app factory, settings, error envelope
  models/contracts.py               Dev 2  §4 models - everyone imports these
  api/                              one router file per owner
  orchestration/ reports/           Dev 2  scheduler, state machine, events, worker, reports
  persistence/ ingestion/
    analysis/ graph/ search/        Dev 3  Atlas, AST, dependency graph, hybrid search
  agents/                           Dev 1  Gemini adapter, tool schemas, prompts
  sandbox/ verification/            Dev 4  Docker runner, workspaces, differential verifier
frontend/                           Dev 5  React + TypeScript UI
runner/                             Dev 4  sandbox image, harness, policy
demo/                               Dev 4  legacy fixture, visible and held-out cases
```

## MongoDB Atlas: the system of record

Axolotl keeps everything it knows in MongoDB Atlas (M10, MongoDB 8.0): the
repository snapshot metadata, every symbol and import edge of the dependency graph,
the code chunks that search runs over, migration plans and runs, an append-only event
journal, behaviour cases, verification results and counterexamples. There is no
in-memory fallback. Switch Atlas off and the graph, search and run history stop with
a clear `503 DATABASE_UNAVAILABLE`; they never quietly answer from a fake.

**Atlas Search** (`code_chunks_text`) finds code by exact name or error string. A
custom analyser splits identifiers, so `finalise_amount` and `finalise` both land on
`pricing.finalise_amount`.
**Atlas Vector Search** (`code_chunks_vector`, Gemini `gemini-embedding-001`, 768
dimensions, filtered by repository) finds code by meaning: "where does the final
customer bill get rounded?" finds `finalise_amount`, whose name shares none of those
words.
**Native hybrid search** fuses both with `$rankFusion`. Each result shows its keyword
rank and vector rank, and the response says whether native fusion actually ran
(`native_hybrid`); it is measured, never assumed.

All indexes, regular and search, are created from code at startup and are safe to
re-run. Search readiness comes from Atlas itself: while an index builds, search
answers `503 SEARCH_INDEX_NOT_READY` instead of an empty list that would look like
"no matches". The run journal gets its sequence numbers from an atomic `$inc`, with a
unique `(run_id, sequence)` index as a backstop; 50 concurrent writers produce 50
distinct numbers.

See [docs/mongodb-bounty.md](docs/mongodb-bounty.md) for claim-by-claim evidence, and
[docs/mongodb-atlas.md](docs/mongodb-atlas.md) for the collections, index definitions and
the gotchas behind them.

## Current state

| Area | State |
|---|---|
| App boots; `/health`, `/system/status` with real checks | ✅ |
| Contracts, config, error envelope | ✅ |
| Scheduler, state machine, event journal, plans, controller, run worker, reports | ✅ |
| All 15 of Dev 2's §6 endpoints | ✅ registered |
| Demo fixture + sandbox runner | ✅ proven in a container |
| All 8 frontend screens | ✅ *(Dev 5)* — Overview, Graph, Module inspector, New migration, Plan, Run, Verification, Report |
| Import, AST, dependency graph | ✅ *(Dev 3)* |
| Atlas collections and indexes, created from code | ✅ *(Dev 3)* |
| Keyword, vector and native `$rankFusion` hybrid search | ✅ *(Dev 3)* |
| Workspaces, patches, differential verifier | ✅ *(Dev 4)* |
| Agent adapter, prompts, tools | ✅ *(Dev 1)* — `propose_plan`, `run_unit_session`, `narrate_report`, all three against `gemini-4-argon` |
| Chess (Python 2.7 → 3.14) planned and run | ⬜ — the gap between here and the demo, [#30](https://github.com/Prittstick22/axolotl/issues/30) |

676 offline backend tests, 9 in a real sandbox container, 56 live tests against the
real Atlas cluster and Gemini behind `-m live`, and 16 frontend tests. A plain
`uv run pytest` excludes the live ones, because one live run costs about 48 Gemini
embedding calls against a 1000-a-day free tier. CI runs the offline, container and
frontend suites on every pull request.

On `legacy_orders`, the chain runs end to end today: import → graph in Atlas → plan →
baseline in a container → an Argon patch → independent verification → a repair →
gates → report. `run_bb0da2e79534` is one such run, where Argon's patches for
`customers.py` and `pricing.py` both passed verification. **Chess cannot be migrated
yet**, and chess is the demo — see the decision note above and [#30](https://github.com/Prittstick22/axolotl/issues/30).

## Branching

Short-lived `devN/<topic>` branches, PR into `main`. **`main` must always boot.**
Merge at least every three hours.

## Deviations from SHARED_CONTRACTS

- **Chess, Python 2.7 → 3.14, is the only demo.** The Spec lists Python 2 → 3 as a
  hackathon non-goal; the team chose it on 24 Oct, with no backup demo.
- **PyMongo, not Motor.** `pymongo>=4.9`'s `AsyncMongoClient` replaces Motor, which
  reached end of life in May 2026. `get_db()` returns an `AsyncDatabase`; the §8
  signature is otherwise unchanged. All 8 doc references updated.
- **React 19, not React 18.** The current Vite template ships it. Ratified by Dev 5 on Fri 23 Oct.
- **§6.2 vs `RunState`.** The Spec's state diagram and the contract enum disagree;
  resolved in favour of the enum per §0. **Acked** in SHARED_CONTRACTS §11.
- **`search_repository` returns a dict**, not `list[SearchHit]`: the caller needs
  `mode_used` and `native_hybrid` as well as the hits. §8's signature was corrected to
  match §6 and the code. **Acked.**
- **`folder_upload` origin** added for the chess upload screen. Additive, and it grants
  no execution rights of its own. **Acked.**
- **`fissix` dependency** (PSF licence) to read Python 2-only syntax: `lib2to3` is
  deprecated, gone in Python 3.13, and absent from the Python 3.12 CI installs.

All recorded in [BUILD_LOG.md](BUILD_LOG.md), with the §11 acks in
[SHARED_CONTRACTS](docs/planning/SHARED_CONTRACTS.md#11-changing-a-contract).

---

## Problem and user

Legacy Python exists in large quantities and nobody wants to touch it. The reason is
not that the changes are hard to write — a model writes them readily — it is that
nobody can tell whether the rewrite still does what the original did. The code that
needs migrating is usually the code with the thinnest tests, and a plausible-looking
diff is exactly what a reviewer cannot check.

Axolotl is for the engineer holding that codebase. It migrates module by module in
dependency order and, for each module, **proves agreement on recorded behaviour**
before moving on: the original and the migrated code run against the same cases in a
sandbox and their outputs are compared. The agent proposes; it never decides whether
it succeeded.

"Behaviour preserved" means **agreement on the tested cases**. It is not a proof of
equivalence, and every report says so in those words.

## Architecture

```
            ┌────────── React 19 + Vite (8 screens) ──────────┐
            │  Overview · Graph · Module · New migration       │
            │  Plan · Run · Verification · Report              │
            └───────────────────────┬─────────────────────────┘
                                    │  26 REST endpoints, /api/v1
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
              13 collections,          one container per case run
              Search + Vector
```

The single rule the design turns on: **only a persisted `VerificationResult` can mark
a unit passed.** The agent's own report of success is advisory. A unit advances when
its required gates pass, and no earlier.

## Exact models and settings

| | |
|---|---|
| planning and migration | `gemini-4-argon`, thinking level `medium` |
| chunk descriptions | `gemini-4-argon` (`GEMINI_MODEL`) |
| embeddings | `gemini-embedding-001`, 768 dimensions, cosine |
| embedding task types | `RETRIEVAL_DOCUMENT` for chunks, `RETRIEVAL_QUERY` for queries |

`gemini-4-argon` had no quota when we started on 23 Oct and returned
`429 RESOURCE_EXHAUSTED`; `gemini-3.8-flash` was the fallback, and the model that ran
is in `GEMINI_MODEL`. Argon quota arrived on 24 Oct and the live runs below used it.

## MongoDB Atlas features used

Covered in full under ["MongoDB Atlas: the system of record"](#mongodb-atlas-the-system-of-record)
above, rather than repeated here: 13 collections with every index created from code,
Atlas Search with a custom identifier analyser, Vector Search at 768 dimensions
filtered by repository, native `$rankFusion` hybrid search whose `native_hybrid` flag
is measured per request, and a gap-free event journal from an atomic `$inc` behind a
unique `(run_id, sequence)` index.

Claim-by-claim evidence is in [docs/mongodb-bounty.md](docs/mongodb-bounty.md).

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

Dev 4's fuller treatment is [`docs/safety-and-limitations.md`](docs/safety-and-limitations.md) ([#9](https://github.com/Prittstick22/axolotl/issues/9)), still to be written.

## Fixture provenance

- **chess** — Will's own terminal chess game, Python 2.7, used with permission. Per
  file hashes are in [`docs/chess-source-manifest.json`](docs/chess-source-manifest.json).
  **Those hashes were taken on a checkout with CRLF line endings**, so 13 of the 16 do
  not match the same source here; the manifest is provenance, not a checksum, and the
  trust gate normalises line endings for exactly that reason.
  Attribution and permission to publish still to be confirmed with Will before the
  repository goes public ([#8](https://github.com/Prittstick22/axolotl/issues/8)).
- **legacy_orders** — written for this project. A small order service whose pricing
  rule rounds per line before tax, which is the behaviour a naive rewrite breaks:
  77 of 300 single-line inputs diverge, and the fixture's own 12 tests all pass on a
  banker's-rounding rewrite. Only the held-out differential catches it (2.65 → 2.64).

## Coverage caveats

- Agreement on recorded cases is not equivalence. Untested behaviour can change
  undetected, and the report says so.
- Held-out cases are the independent half of the suite. They are never shown to the
  model unless a repair exposes one deliberately, and never leak through a read path:
  comparisons, counterexamples and gate logs are all redacted for a sealed case.
- An inconclusive gate is never a pass. If the sandbox cannot run, the result is
  inconclusive, not success.
- `legacy_orders` has 26 behaviour cases. **Chess has none yet** ([#23](https://github.com/Prittstick22/axolotl/issues/23)), so chess cannot be verified
  today, only imported and inspected.

## Known issues and failures

| | |
|---|---|
| **Chess cannot be migrated** | the sandbox has no 2.7 or 3.14 runtime ([#22](https://github.com/Prittstick22/axolotl/issues/22)), chess has no behaviour cases ([#23](https://github.com/Prittstick22/axolotl/issues/23)), and `python2_to_3` is `supported: false` until both land ([#21](https://github.com/Prittstick22/axolotl/issues/21)) |
| A 429 mid-run still loses a unit | the failure is now reported honestly, but the retry window is outstanding ([#47](https://github.com/Prittstick22/axolotl/issues/47)) |
| Free-tier Gemini quota | 1000 embedding calls a day; one live test run is about 48, and a debugging session spent a whole key on 23 Oct |
| Snapshots are machine-local | a repository imported on one laptop cannot be re-indexed on another; the endpoint says so with 409 `SNAPSHOT_UNAVAILABLE` |
| Report runtime timings | `performance` is always `null`; nothing times cases yet ([#46](https://github.com/Prittstick22/axolotl/issues/46)) |
| A run turns terminal before its last event | clients that stop polling at a terminal state can miss `report_generated` ([#16](https://github.com/Prittstick22/axolotl/issues/16)) |

## Credits

- **Will** — the chess source, used with permission.
- **Gemini** (`gemini-4-argon`, `gemini-embedding-001`) — planning, migration, chunk
  descriptions, embeddings.
- **MongoDB Atlas** — system of record, Atlas Search, Vector Search, `$rankFusion`.
- **fissix** (PSF licence) — the maintained standalone copy of `lib2to3`, used to read
  Python 2-only syntax. `lib2to3` is deprecated, gone in Python 3.13, and absent from
  the Python 3.12 that CI installs.
- **FastAPI, Pydantic, PyMongo, NetworkX, React, Vite, Tailwind, pytest, ruff, uv.**

## Screenshots

To add before submission: Overview with the graph, the Plan screen with risk and
visible-case counts, the Run screen mid-repair, the Verification screen's gate table
and per-case comparison, and the Report with its NOT-A-PROOF statement.

