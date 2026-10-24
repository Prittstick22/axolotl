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
> tests and the sandbox run on, until chess replaces it ([#54](https://github.com/Prittstick22/axolotl/issues/54)).
> Chess readiness: the Python 2.7/3.14 sandbox landed in
> [#22](https://github.com/Prittstick22/axolotl/issues/22), the chess behaviour suite
> merged in [PR #69](https://github.com/Prittstick22/axolotl/pull/69), and the target
> is enabled by merged [PR #76](https://github.com/Prittstick22/axolotl/pull/76). Check the
> deployed API for availability. That work is tracked in [#30](https://github.com/Prittstick22/axolotl/issues/30); the full plan is in

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

**Build the sandbox images.** Nothing can be verified without them: the runner chip
reads `unavailable`, and a run gets as far as the baseline and stops. There are five,
and the same commands CI runs are the ones to run here
(`.github/workflows/ci.yml`, which is the source of truth if these drift):

```bash
docker build -t axolotl-runner:latest runner/
docker build -t axolotl-runner:py2.7  --build-arg PYTHON_VERSION=2.7  --build-arg PYTEST_VERSION=4.6.11 runner/
docker build -t axolotl-runner:py3.14 --build-arg PYTHON_VERSION=3.14 --build-arg PYTEST_VERSION=8.4.2  runner/
docker build -f runner/Dockerfile.behaviour --build-arg PYTHON_IMAGE=python:2.7.18-slim -t axolotl-chess-cases:py27  runner/
docker build -f runner/Dockerfile.behaviour --build-arg PYTHON_IMAGE=python:3.14-slim  -t axolotl-chess-cases:py314 runner/
```

**Run these again after pulling.** The runner changed in [#98](https://github.com/Prittstick22/axolotl/issues/98) and [#100](https://github.com/Prittstick22/axolotl/issues/100), and a stale image does not announce itself — it fails the runner check, which reads like a broken Docker setup rather than an old build.

`axolotl-runner:latest` is the 3.12 image `legacy_orders` runs on; the `py2.7` and
`py3.14` pair is what a Python 2 migration needs, one for the baseline and one for the
candidate ([#22](https://github.com/Prittstick22/axolotl/issues/22)).

Open <http://localhost:5173>. The status chips show the **real** state of MongoDB, the
Atlas search indexes, the sandbox runner and Gemini.

**Then import the demo.** On *New migration*, **Demo fixture: `chess`** is already
selected — it ships in the repository and runs from any laptop ([#92](https://github.com/Prittstick22/axolotl/issues/92)), so there is
nothing to upload. Choose **Python 2 to Python 3** as the target, the label the Plan
screen shows.

*Uploading `demo/chess` as a folder also works: an exact content match is recognised
as the trusted fixture, which is what makes it executable, and a modified copy is
imported for reading only ([#21](https://github.com/Prittstick22/axolotl/issues/21)). The built-in option is the one to demo.*

**With an empty `.env` every chip reads `unavailable`. That is the honest answer, not a
failure of the app** — nothing here fakes a working dependency.

```bash
curl -s http://localhost:8000/api/v1/system/status     # same thing from the API
```

API docs: <http://localhost:8000/docs> · Tests: `cd backend && uv run pytest`

**Prerequisites:** Python 3.12 (uv installs and pins it), [uv](https://docs.astral.sh/uv/),
Node 20+, a running Docker daemon (on Windows, Docker Desktop must be **started** —
the daemon is not running just because Docker is installed), and a MongoDB Atlas
cluster plus a Gemini API key.

`MONGODB_URI` and `GEMINI_API_KEY` are the only two values you must fill; everything
else in `.env.example` has a working default.

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
| [docs/presentation.md](docs/presentation.md) | Will's 1:00–1:30 introduction and judge Q&A (#70) |
| [docs/demo-script.md](docs/demo-script.md) | The timed 3-minute chess demo, preflight and fallback (Dev 4, #14) |
| [docs/submission-videos.md](docs/submission-videos.md) | Three video scripts, shot lists and upload checks (#15) |
| [docs/rehearsal-log.md](docs/rehearsal-log.md) | Actual-run evidence, two rehearsals and checked video links |
| [runner/policy.md](runner/policy.md) | The exact sandbox profile, and what it does **not** protect against |
| [demo/legacy_orders/README.md](demo/legacy_orders/README.md) | The test fixture (not a demo), its seeded rounding rule and parameters |
| [BUILD_LOG.md](BUILD_LOG.md) | Timestamped record of what was built when, and the open decisions |
| [docs/planning/](docs/planning/) | The Spec, SHARED_CONTRACTS and the five dev briefs |

`SHARED_CONTRACTS.md` wins on names, shapes and ownership. The Spec wins on product and
safety rules. Change a contract only through SHARED_CONTRACTS §11.

---

## Team

`Dev N` in the docs, the layout below, ticket titles and the `dev N` labels means:

| Role | Name | GitHub |
|---|---|---|
| Dev 1: Argon agent, and project manager | Dev | [`Prittstick22`](https://github.com/Prittstick22) |
| Dev 2: orchestrator and integration | Mo | [`mh-husssain`](https://github.com/mh-husssain) |
| Dev 3: graph and Atlas | Caroline | [`Caroline3110`](https://github.com/Caroline3110) |
| Dev 4: verifier and sandbox | William (Will) | [`WilliamParis1`](https://github.com/WilliamParis1) |
| Dev 5: frontend | Abhay | [`PanotAbhay`](https://github.com/PanotAbhay) |

The repository is public, by decision (24 Oct). Never commit `.env` or any key.

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
| All 9 frontend screens | ✅ *(Dev 5)* — Overview, Graph, Module inspector, New migration, Plan, Run, Verification, Report, Search |
| Import, AST, dependency graph | ✅ *(Dev 3)* |
| Atlas collections and indexes, created from code | ✅ *(Dev 3)* |
| Keyword, vector and native `$rankFusion` hybrid search | ✅ *(Dev 3)* |
| Workspaces, patches, differential verifier | ✅ *(Dev 4)* |
| Agent adapter, prompts, tools | ✅ *(Dev 1)* — `propose_plan`, `run_unit_session`, `narrate_report`, all three against `gemini-4-argon` |
| Chess (Python 2.7 → 3.14) can be planned and verified | ✅ — dual-runtime sandbox ([#22](https://github.com/Prittstick22/axolotl/issues/22)), chess behaviour cases ([#23](https://github.com/Prittstick22/axolotl/issues/23)), `python2_to_3` supported ([#21](https://github.com/Prittstick22/axolotl/issues/21)) |
| A chess run that reaches a verdict | ✅ — `run_70e126c3acf2`, 24 Oct: `completed` across 14 units against `gemini-4-argon` ([#4](https://github.com/Prittstick22/axolotl/issues/4)). Recorded for replay on `demo/argon-replay`, so a laptop with no Gemini key can still show it |

827 offline backend tests, 25 in real sandbox containers, 56 live tests against the
real Atlas cluster and Gemini behind `-m live`, and 20 frontend tests. A plain
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
            ┌────────── React 19 + Vite (9 screens) ──────────┐
            │  Overview · Graph · Module · New migration       │
            │  Plan · Run · Verification · Report · Search     │
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
  Will wrote it with AI assistance and published it as part of this project. Attribution
  and permission to publish were confirmed with him on 24 Oct: no licensing or publishing
  issues ([#8](https://github.com/Prittstick22/axolotl/issues/8)).
- **legacy_orders** — written for this project. A small order service whose pricing
  rule rounds per line before tax, which is the behaviour a naive rewrite breaks:
  77 of 300 single-line inputs diverge, and the fixture's own 12 tests all pass on a
  banker's-rounding rewrite. Only the held-out differential catches it (2.65 → 2.64).

## Coverage caveats

- Agreement on recorded cases is not equivalence. Untested behaviour can change
  undetected, and the report says so.
- Held-out cases are the independent half of the suite. They are never shown to the
  model unless a repair exposes one deliberately, and never leak through a read path:
  comparisons, counterexamples, gate logs and the event journal are all redacted for
  a sealed case.
- An inconclusive gate is never a pass. If the sandbox cannot run, the result is
  inconclusive, not success.
- `legacy_orders` has 26 behaviour cases, 16 visible and 10 held out. Chess has 24,
  12 and 12, routed per fixture ([#23](https://github.com/Prittstick22/axolotl/issues/23)). Chess has been verified end to end
  once (`run_70e126c3acf2`); `legacy_orders` is exercised by the test and sandbox
  suites rather than by a full run.

## Known issues and failures

| | |
|---|---|
| **No cold-start reproduction yet** | a live chess run reached a verdict on 24 Oct, but nobody has yet started from a fresh clone with only this README and reproduced it. That is the remaining honest gap ([#7](https://github.com/Prittstick22/axolotl/issues/7)) |
| Free-tier Gemini quota | 1000 embedding calls a day; one live test run is about 48, and a debugging session spent a whole key on 23 Oct. A plain `uv run pytest` excludes the live suite for that reason |
| Snapshots are machine-local | a repository imported on one laptop cannot be re-indexed on another; the endpoint says so with 409 `SNAPSHOT_UNAVAILABLE`, so whoever demos must be whoever imported |
| Report runtime timings are conditional | `performance` is filled ([#46](https://github.com/Prittstick22/axolotl/issues/46)) only for a completed `trusted_docker` run whose final verification passed on the accepted revision; otherwise `null`, and the Report leaves the panel out rather than showing zeroes |
| `legacy_orders` is a test fixture, not a demo | it is what the backend tests and the sandbox run on. The demo is chess |

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

