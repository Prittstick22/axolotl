# Start here

Everything you need to pick up Axolotl. Fifteen minutes end to end.

> **Decision, 24 Oct 2026:** the demo is Will's chess game, migrated from Python 2.7 to Python 3.14.
> **Chess is the only demo; there is no backup.** `legacy_orders` stays in the repo only as the test
> fixture until chess replaces it ([#54](https://github.com/Prittstick22/axolotl/issues/54)). Chess can't be migrated yet:
> see [#30](https://github.com/Prittstick22/axolotl/issues/30) and [PROJECT_STATUS.md](PROJECT_STATUS.md).

> The demo script, videos and presentation are demo work: not MVP, unassigned, done last.
> What is left, by developer, is in [PROJECT_STATUS.md](PROJECT_STATUS.md).

> **Who is who:** Dev 1 is Dev (`Prittstick22`, also project manager), Dev 2 is Mo (`mh-husssain`),
> Dev 3 is Caroline (`Caroline3110`), Dev 4 is William (`WilliamParis1`) and Dev 5 is Abhay
> (`PanotAbhay`). The repository is public, by decision; never commit `.env` or any key.

---

## 1. Get it running (5 min)

```bash
git clone https://github.com/Prittstick22/axolotl.git && cd axolotl
cp .env.example .env
```

Fill `MONGODB_URI` and `GEMINI_API_KEY` in `.env` from the team vault.
**Never** paste them into chat, a commit, a log or a screenshot. `.env` is gitignored.

```bash
cd backend && uv sync && uv run uvicorn app.main:app --reload --port 8000
```

```bash
cd frontend && npm install && npm run dev
```

Open <http://localhost:5173>.

> **The status chips will read `unavailable` until `.env` is filled in. That is correct.**
> The app never pretends a dependency is up. If you see `unavailable` with a filled-in
> `.env`, expand "Why something is unavailable" — it tells you exactly what failed.

Check your work any time:

```bash
cd backend && uv run pytest && uv run ruff check .
cd frontend && npm run build && npm run lint
```

177 backend tests should pass, with 16 skipped. If they don't on a fresh clone, that's a
bug — say so.

### The `live` tests

The 16 skipped ones talk to the **real** Atlas cluster and the **real** Gemini API. They
skip themselves unless `MONGODB_URI` / `GEMINI_API_KEY` are set, so they are safe to
leave in the default run.

```bash
cd backend && uv run pytest -m live -s     # -s so the model list prints
cd backend && uv run pytest -m "not live"  # skip them explicitly, e.g. in CI
```

They are worth running once your `.env` is filled in, because they answer questions the
planning docs left open:

- **Your Atlas cluster version**, and whether it supports native `$rankFusion` (8.0+)
  or `$scoreFusion` (8.2+). If it doesn't, Dev 3 must fall back to manual reciprocal
  rank fusion and report `native_hybrid: false`.
- **Which Gemini models your key can actually reach**, whether `gemini-4-argon` is among
  them, and which embedding model and dimension count to give Dev 3 for the
  `code_chunks_vector` index. If `gemini-4-argon` isn't available the test tells you
  what to set `GEMINI_MODEL` to — that's a `.env` change, not a code change.

They also prove things unit tests can't: that the event counter's atomic `$inc` really
is atomic (50 concurrent writers, 50 distinct sequence numbers), that the unique index
rejects a forged duplicate, and that timezone-aware datetimes survive the round trip.

> **They never touch your real database.** Each run creates its own
> `axolotl_test_<random>` database and drops only that, after asserting the name
> carries the test prefix.

---

## 2. Read, in this order

1. **[`planning/SHARED_CONTRACTS.md`](planning/SHARED_CONTRACTS.md)** — all of it. Names, shapes, ownership. It is the law.
2. **Your own** `planning/AGENT_DEVn_*.md`.
3. [`architecture.md`](architecture.md) — how the pieces fit and which are real today.
4. [`api-contracts.md`](api-contracts.md) — the HTTP surface, with what's implemented.
5. The Spec (`planning/Axolotl_Product_...md`) — §0, §2, §6.2, §7.1, §11.2 at minimum.
6. [`../BUILD_LOG.md`](../BUILD_LOG.md) — what was built when, and the open decisions.

---

## 3. The rule that matters

**The verifier decides pass/fail. The model only proposes. Dev 2 records. Dev 5 displays.**

Nothing else may set a unit to `passed`. If you find yourself writing a code path where
a model's claim, a flag or a return value marks something verified — stop, that is the
one thing this project exists to prevent.

Its corollaries, which are not negotiable:

- **No fakes.** No mocked AI output, hard-coded results, invented edges, fake search,
  timer-driven progress or in-memory DB fallback in anything a judge sees.
- Temporary stubs are allowed only behind an interface, must be named `*_stub`, must log
  a warning, and must be gone before the Saturday 17:00 dry run. `grep -r _stub backend/`
  must come back empty. Right now there is exactly one: `sandbox._runner_status_stub`.
- **"Behaviour preserved" means agreement on the tested cases.** Never say "proven",
  "equivalent" or "guaranteed" — in code, in the UI, in the report or in the demo.
- Only the allowlisted fixture executes. Uploaded ZIPs are static-analysis only.

---

## 4. Your lane

Only edit files you own ([`architecture.md` §4](architecture.md#4-packages-and-owners)).
Need a change elsewhere? Ask the owner, or propose it through SHARED_CONTRACTS §11.

### Dev 1 (Dev, `Prittstick22`) — Argon agent · `app/agents/`

**Already there for you:** `agents/contexts.py` has `PlanContext`,
`UnitSessionContext`, `UnitSessionOutcome` and the `UnitController` Protocol,
transcribed from §8. `agents/health.py` has a working Gemini reachability check you can
crib the client setup from.

**Implement:** `propose_plan(ctx) -> PlanProposal` and
`run_unit_session(ctx, controller) -> UnitSessionOutcome` in `app/agents/__init__.py`
(or re-export from there). `orchestration/ports.py` picks them up automatically the
moment they exist — no wiring needed.

**Your loop:** the model drives. Call `controller.submit_patch(...)`, then
`controller.run_verification()`, read `controller.get_counterexamples()`, repeat while
`controller.patch_attempts_remaining() > 0`. Narrate with `controller.emit("agent_turn", ...)`.

**Things that will bite you:** `emit()` accepts only `agent_turn`, `agent_tool_call`,
`agent_tool_result` — anything else raises. The budget is 3 patches (1 + 2 repairs) and
a fourth never reaches the sandbox. You are told failing *visible* case ids and only a
*count* of held-out failures. Never write a prompt that nudges the model to introduce a
bug for a nicer demo (T-18 and the DEV1 brief both forbid it).

**You're blocked by:** Dev 4's verifier, for a real pass/fail. Build against
`run_verification()` returning inconclusive until it lands.

### Dev 2 (Mo, `mh-husssain`) — Orchestrator & integration · `app/orchestration/`, `reports/`

**Done:** scheduler (SCC, eligibility, T-13), state machine, event journal, plans,
`UnitControllerImpl`, run worker, store, all 15 §6 endpoints, 155 tests.

**Left:** patch and archive artefacts, the T-16 diff-hash assertion, `get_unit_statuses`
for Dev 3's graph overlay, scope-cut calls at each gate.

### Dev 3 (Caroline, `Caroline3110`) — Graph & Atlas · `app/persistence/`, `ingestion/`, `analysis/`, `graph/`, `search/`

**Already there for you:** `persistence/db.py` has a working `AsyncMongoClient` with
`get_db()`, `ping()` and `server_version()`. **It uses PyMongo's async client, not
Motor** — Motor reached end of life in May 2026. `get_db()` returns an `AsyncDatabase`;
the §8 signature is otherwise unchanged. `persistence/search_health.py` already queries
`listSearchIndexes` for `/system/status`.

**Implement:** `import_repository`, `get_repository`, `snapshot_path`, `get_graph`,
`load_networkx`, `get_module_detail`, `get_symbol`, `find_symbol`, `search_repository`,
plus `persistence/indexes.py` (idempotent, called from the lifespan).

**First task:** import the fixture and produce the graph. **Dev 2 is blocked on
`load_networkx`** — `POST /repositories/{id}/plans` returns 503 until it exists.

**The exact T-03 edge set** is in [`../demo/legacy_orders/README.md`](../demo/legacy_orders/README.md) —
7 edges, no more, no fewer, correct direction. Note `A → B means A depends on B`.

**Ready for you:** the status overlay for `GET /repositories/{id}/graph?run_id=`.
Call it, don't recompute it — it is keyed by **module_id**, and a cyclic unit paints
every module in its SCC:

```python
from app.orchestration import get_unit_statuses_safe, get_graph_overlay

statuses = await get_unit_statuses_safe(run_id)        # {} for a missing/stale run_id
node.status = statuses.get(node.id, "not_planned")

overlay = await get_graph_overlay(run_id)              # also gives unit_id, attempts
node.unit_id = overlay.get(node.id, {}).get("unit_id")
```

**Watch out:** exclude `demo/challenge_cases/` from anything you index or chunk, and
assert that you did. Those are held-out.

### Dev 4 (William, `WilliamParis1`) — Verifier & sandbox · `app/sandbox/`, `app/verification/`, `runner/`, `demo/`

**Changed 24 Oct:** chess is the MVP demo, so the dual-runtime sandbox and chess cases
are MVP ([#22](https://github.com/Prittstick22/axolotl/issues/22), [#23](https://github.com/Prittstick22/axolotl/issues/23)). `legacy_orders` below is no longer a demo: it is
only the test fixture, until chess replaces it ([#54](https://github.com/Prittstick22/axolotl/issues/54)).

**Already there, built and proven:** the complete demo fixture, 16 visible + 10 held-out
cases, the `axolotl-runner` image, and `harness/run_cases.py`. All of it verified in a
container under the full security profile. Read
[`../demo/legacy_orders/README.md`](../demo/legacy_orders/README.md) — it has
`[confirm]` markers on every parameter the planning docs left open (tax rate, discount
bands, refund rule). **Please confirm or change those.**

**Implement:** `create_workspace`, `apply_patch`, `accept_workspace`, `diff`,
`capture_baseline`, `verify_unit`, `verify_final`, `get_counterexamples`, `expose_case`,
`load_cases`, and replace `_runner_status_stub` with a real Docker probe.

**Proven facts to build on:** against a banker's-rounding patch, 7 of the 10 held-out
cases mismatch, including 2.65 → 2.64. **The fixture's own 12 tests still pass on that
patch** — only the held-out differential catches it. That gap is the demo.

**Watch out:** `--read-only` means `/code` cannot take `.pyc` files; the image sets
`PYTHONPYCACHEPREFIX=/tmp/pycache`. pytest needs `PYTHONPATH=/code`, also set in the
image. Both are already handled — see [`../runner/policy.md`](../runner/policy.md).

### Dev 5 (Abhay, `PanotAbhay`) — Frontend · `frontend/`

**Already there for you:** `src/types/contracts.ts` is the full §4 mirror in snake_case.
`src/api/client.ts` parses the error envelope into a thrown `ApiError` with `.code`.
`App.tsx` polls `/system/status` and renders chips — treat it as a worked example and
replace it.

**Implement:** the 8 screens from Spec §2.2. React Flow graph, module inspector with
Monaco, run dashboard with the event timeline, diff view, report, search.

**Watch out:** poll `/runs/{id}` and `/events?after_sequence=N` every 1.5 s while
non-terminal. Status must be readable **without colour** — every chip carries a glyph and
the status word. Never render a green "completed" unless the API literally says
`completed`. Show coverage caveats prominently, not in a collapsed panel. Label Argon's
`narrative` as model-generated.

**Resolved (Fri 23 Oct):** the Vite template ships **React 19**; SHARED_CONTRACTS §2 said
React 18. `@xyflow/react` 12 and TanStack Query 5 both support it, so you kept React 19
and §2 now says so.

---

## 5. Working agreements

- Branch `devN/<topic>`, PR into `main`. **`main` must always boot.** Merge at least
  every 3 hours.
- Run `uv run pytest && uv run ruff check .` before you push. Don't break the suite for
  someone else.
- Add tests for every endpoint you build.
- Update [`../BUILD_LOG.md`](../BUILD_LOG.md) every couple of hours: done, next, blocked.
- Changing a shared contract means editing `SHARED_CONTRACTS.md` **and** `contracts.py`
  **and** `contracts.ts` in one PR, with the affected owners' acks (§11).

### Gates (all demonstrable on `main`)

| When | Gate |
|---|---|
| Fri 18:00 | Skeleton boots, real Mongo status in the UI — **done early** |
| Fri 21:00 | Fixture → real graph in Atlas → UI; one real baseline case in the sandbox |
| Sat 11:00 | Real Argon patch → real verification → repair attempt |
| Sat 14:00 | Two units verified in order; search live |
| Sat 17:00 | Cold-start E2E, all stubs deleted |
| Sat 22:00 | **Feature freeze** |
| Sun 10:45 | Submitted (cutoff 12:00) |

---

## 6. Open decisions — please weigh in

1. **Motor → PyMongo.** Done, all 8 doc references updated. Motor is EOL. No functional
   requirement depended on a Motor-only API.
2. **§6.2 vs `RunState`.** The Spec's state diagram and the contract enum disagree.
   Resolved in favour of the enum per §0. Needs a §11 ack — see
   [`architecture.md` §7](architecture.md#7-state-machine).
3. **React 19 vs §2's React 18.** Resolved: Dev 5 kept React 19 (Fri 23 Oct).
4. **Fixture parameters.** Tax rate, discount bands and the refund rule were unspecified
   and chosen by Dev 2. Dev 4 to confirm — all marked `[confirm]`.
5. **The demo.** Decided 24 Oct: chess, Python 2.7 → 3.14, is the only demo. There is no
   backup; `legacy_orders` is only the test fixture ([#54](https://github.com/Prittstick22/axolotl/issues/54)). Tracked in [#30](https://github.com/Prittstick22/axolotl/issues/30).
