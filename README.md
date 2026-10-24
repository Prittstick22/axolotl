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
| Frontend | status chips only — 8 screens outstanding *(Dev 5)* |
| Import, AST, dependency graph | ✅ *(Dev 3)* |
| Atlas collections and indexes, created from code | ✅ *(Dev 3)* |
| Keyword, vector and native `$rankFusion` hybrid search | ✅ *(Dev 3)* |
| Workspaces, patches, differential verifier | ✅ *(Dev 4)* |
| Agent adapter, prompts, tools | ⬜ *(Dev 1)* — the one gap left in the demo path |

470 offline tests, 9 in a real sandbox container, and a live suite against the real
Atlas cluster and Gemini behind `-m live`. CI runs the offline and container suites on
every PR.

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
  resolved in favour of the enum per §0. Needs a §11 ack.

All recorded in [BUILD_LOG.md](BUILD_LOG.md).

---

## To fill in before submission

Problem and user · architecture · exact model ids and thinking levels · MongoDB Atlas
features used · execution limits and sandbox policy · fixture provenance · coverage
caveats · known issues and failures · credits.
