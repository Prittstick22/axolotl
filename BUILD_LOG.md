# Axolotl — Build Log

Append-only record of what was built, in build order: the human-readable account of
each session's work, decisions and evidence.

---

## Session 1 (Dev 2, infra)

### Deliberate deviations from SHARED_CONTRACTS

| # | Contract says | We used | Why |
|---|---|---|---|
| 1 | §2, §8: `motor` (async MongoDB), `get_db() -> AsyncIOMotorDatabase` | `pymongo>=4.9`, `AsyncMongoClient`, `get_db() -> AsyncDatabase` | Motor reached end of life May 2026. Instructed change. All 8 Motor mentions in `docs/planning/` updated; no functional requirement in any doc depends on a Motor-only API. |
| 2 | §2: "Vite + React 18 + TypeScript" | React **19.3.1** | The current Vite `react-ts` template ships React 19; `@xyflow/react` 12 and TanStack Query 5 both support it. **Ratified by Dev 5 on Fri 23 Oct.** |

Installed versions: `pymongo 4.19.0`, `google-genai 2.32.0`, `fastapi 0.161.0`,
`pydantic 2.14.1`, `networkx 3.7`, `uvicorn 0.54.0`, `pytest 9.1.1`, `ruff 0.17.2`,
`vite 8.3.4`, `typescript 6.0.3`, `oxlint 1.88.0`.

### Phase 0 — machine and accounts

| Check | Result |
|---|---|
| `git --version` | 2.46.2.windows.1 ✅ |
| `gh --version` | 2.96.0 ✅ — but **not authenticated** (`gh auth login` outstanding) |
| `ssh -T git@github.com` | ❌ not run — no GitHub auth yet |
| Python 3.12 | ⚠ system Python was **3.11.0**; installed CPython **3.12.15** via `uv`, pinned per-project in `backend/.python-version`. The uv minor-version symlink failed (Windows symlink permissions) — harmless, the full version path works. |
| `uv --version` | 0.13.5 ✅ (installed this session) |
| `node -v` / `npm -v` | v24.19.0 / 11.17.0 ✅ (20+) |
| Docker | 28.4.0 ✅ — daemon was **stopped**; started Docker Desktop, confirmed `docker info` responds |
| `docker pull python:3.12-slim` | ✅ pulled (first attempt failed: daemon was down) |
| `mongosh` | ✅ 2.13.0 (installed this session via npm) |
| Atlas connection + cluster version | ❌ **blocked** — no `.env`, so no `MONGODB_URI` |
| Gemini test call + model list | ❌ **blocked** — no `.env`, so no `GEMINI_API_KEY` |
| Demo laptop runs Docker + backend + frontend together | ⏳ partially — backend and frontend both proven on this machine; not yet all three at once with a container running |

### Phase 1 — skeleton

Repo created at `C:\Users\hisha\Documents\axolotl` on branch `main`.
Deliberately **outside OneDrive**: OneDrive sync contends with `.venv/` and
`node_modules/`, which corrupts Python envs and slows installs.

Built, matching SHARED_CONTRACTS §2 / §4 / §6 / §10:

- Folder layout per §2, plus `errors.py`, `.editorconfig` and `tests/conftest.py` from
  `APP_SCAFFOLD_SETUP.md`. Every package `__init__.py` names its owner.
- All 11 planning docs **copied** (not moved) into `docs/planning/`.
- `backend/pyproject.toml` — deps per §2 with the PyMongo substitution; ruff line-length
  100; pytest `asyncio_mode=auto`, `live` marker, `pythonpath=["."]`.
- `app/config.py` — every §10 variable, secrets defaulting to empty so the app always
  boots, plus `secret_values()` for the §7 `redact()` helper.
- `app/models/contracts.py` — §4 transcribed verbatim, all 30 models and 11 enum aliases.
- `app/errors.py` — `AppError` + the §6 envelope `{"error": {code, message, details}}`,
  wired for `AppError`, `HTTPException` and validation errors.
- `app/main.py` — `create_app()` factory, lifespan opening/closing the Mongo client,
  `/api/v1` prefix, CORS restricted to `FRONTEND_ORIGIN`, five owner routers mounted,
  `GET /api/v1/health`.
- `app/api/system.py` — `GET /api/v1/system/status` with a **real** Mongo `ping`, a real
  `listSearchIndexes` check, and honest `unavailable` for the runner and Gemini.
- `app/persistence/db.py` — real `AsyncMongoClient`. **No in-memory fallback** (§9: 503
  `DATABASE_UNAVAILABLE`). Minimal; Dev 3 owns and extends it.
- `app/agents/health.py` — a **real** Gemini reachability check (reports unreachable with
  no key; calls `models.get` when a key exists), result cached per process.
- `app/sandbox/__init__.py` — `runner_status()` delegating to `_runner_status_stub()`,
  which logs a warning. **The only stub in the repo.** `grep -r _stub backend/` must come
  back empty before the Sat 17:00 dry run (§0.2).
- `frontend/` — Vite React-TS, with `@xyflow/react`, `@monaco-editor/react`,
  `@tanstack/react-query`, `react-router-dom`, `dagre`. `src/types/contracts.ts` mirrors
  §4 in snake_case; `src/api/client.ts` parses the §6 error envelope; `App.tsx` polls
  `/system/status` and renders the chips (glyph + status word, so they read without
  colour).
- `.env.example` (placeholders only), `.gitignore` (`.env`, venvs, `node_modules`,
  `.axolotl_workspaces/`), `.editorconfig`, `README.md`, heading-only `docs/*.md`.

### Proven by running it

- `uv sync` → resolved and installed ✅
- `uv run pytest` → **5 passed** ✅ (boot, `/health`, `/system/status` contract shape,
  contracts import, error envelope)
- `uv run ruff check .` → **All checks passed** ✅
- `uvicorn app.main:app --port 8000` → booted ✅
  - `GET /api/v1/health` → `{"status":"ok"}`
  - `GET /api/v1/system/status` → `{"mongodb":"unavailable","search_indexes":{"keyword":"unavailable","vector":"unavailable"},"runner":{"mode":"trusted_docker","available":false},"gemini":{"model":"gemini-4-argon","reachable":false},"details":{…}}`
    — correct §6 shape, honestly all-unavailable with no secrets configured
  - `GET /api/v1/nope` → `404 {"error":{"code":"NOT_FOUND","message":"Not Found","details":{}}}`
  - CORS preflight from `http://localhost:5173` → `access-control-allow-origin` present

### Blocked / outstanding

1. **`.env` does not exist.** Needs `MONGODB_URI` and `GEMINI_API_KEY` from the team
   vault, added by a human — secrets must not pass through chat or this log. Until then
   Atlas and Gemini verification cannot run, and `/system/status` correctly says
   `unavailable`.
2. **`gh` is not authenticated**, so nothing is pushed. `main` exists locally only.
   The GitHub repo has not been created.
3. **React 19 vs §2's React 18** — needs Dev 5's ack (deviation 2 above).
4. Phase 2 not started: demo fixture, runner Dockerfile, Atlas collections and indexes.

### Next

- Human: `gh auth login`; create `.env` from `.env.example` with the vault values.
- Then: verify Atlas (cluster version, Search/Vector availability) and Gemini (which
  models the key can use, whether `gemini-4-argon` exists, the Interactions tool-call
  shape); push `main`; build the demo fixture and the runner Dockerfile.

---

## Session 1 (continued), Dev 2 orchestrator core

Role confirmed as **Dev 2, Orchestrator & Integration Lead**. Everything below is
inside Dev 2's ownership from SHARED_CONTRACTS §2.

### Built

- `orchestration/scheduler.py` — sync and pure, no I/O. `build_units` collapses SCCs
  into units via `nx.condensation` (multi-module SCC → one unit, `is_cycle=True`);
  `eligible_units`, `impacted_units` (transitive reverse dependants),
  `units_to_skip` (T-13), `score_risk`, `topological_order`, `is_valid_order`.
- `orchestration/states.py` — guarded run and unit transitions, `IllegalTransition`,
  `final_verdict`. A run cannot reach `completed` except through
  `final_verification` with the gates actually green.
- `orchestration/events.py` — `append_event` with an atomic `$inc` on `counters`,
  `list_events`, `redact`, `truncate`, `sanitise`, `ensure_indexes`.
- `api/targets.py` — `GET /migration-targets` and `require_supported_target` (T-08).

### Contract reconciliation needed (SHARED_CONTRACTS §11)

Spec §6.2's state diagram and `RunState` in §4 do not agree: §6.2 has `parsing`,
`planning` and `awaiting_approval` which the enum lacks, and the enum has
`cancelled` which §6.2 never transitions into. §0 says SHARED_CONTRACTS wins on names
and shapes, so the tables are built over the enum, with §6.2's shape mapped on and the
reasoning written into `states.py`'s module docstring. **Needs team ack.**

Also unresolved and chosen by us, marked `[confirm]` in code: Spec §5.5 gives risk a
formula but no weights and no score→label thresholds.

### Proven by running it

- `uv run pytest` → **103 passed** (was 5)
- `uv run ruff check .` → All checks passed
- `GET /api/v1/migration-targets` live → 1 supported, 4 disabled with reasons

One real bug was caught by its own test: `sanitise()` read secrets only from global
settings, so with an empty `.env` it silently redacted nothing and could not be
tested. It now takes an explicit `secrets` argument, and a test pins the ordering —
redact before truncate, or a secret straddling the 2 KB cap leaves its first half in
the journal.

### Still blocked

Unchanged: no `.env` (Atlas and Gemini unverified), `gh` not authenticated so
nothing is pushed. React 19 vs §2's React 18 still needs Dev 5's ack.

---

## Dev 2 run pipeline

### Built

- `orchestration/ports.py` — the three integration points Dev 2 needs (Dev 1's agent,
  Dev 3's graph, Dev 4's verifier). They resolve lazily, so a collaborator's real
  function is picked up the moment it exists with no change here. When one is missing
  the call raises `IntegrationUnavailable`, which becomes an honest inconclusive or
  blocked outcome — **never** a pass. Nothing in that file invents a graph or a verdict.
- `orchestration/plans.py` — `create_plan` builds units from the real graph, asks
  Argon only to *order* them, and discards the order if it is not a valid topological
  order of the condensation, recording why in `rationale`. `approve_plan`.
- `orchestration/controller.py` — `UnitControllerImpl`, the model's entire surface.
- `orchestration/worker.py` — the run algorithm, off the HTTP request lifetime.
- `orchestration/store.py`, `ids.py`, `reports/builder.py`.
- `agents/contexts.py` — `PlanContext`, `UnitSessionContext`, `UnitSessionOutcome`
  and the `UnitController` Protocol, transcribed from §8 so **Dev 1 is unblocked**.
- `api/plans_runs.py` — all 15 of Dev 2's §6 endpoints.

### Bug the tests caught

The controller moved a unit `verifying → migrating` after a failed check. Spec §6.2
says `verifying → repairing → migrating`, and the state machine rejected it. The
controller was wrong, not the table: a repair now follows a failed verification, and
a unit whose budget is spent stays in `verifying` for the **worker** to block — so
one place decides a unit has failed, not two.

### Proven by running it

- `uv run pytest` → **155 passed** (was 103)
- `uv run ruff check .` → All checks passed
- 15 endpoints registered, confirmed from the OpenAPI schema
- Every database-backed endpoint returns **503 DATABASE_UNAVAILABLE** with an empty
  `.env` — no 500s, and no endpoint fakes an empty 200 (§9: no in-memory fallback)
- T-08 still rejects an unsupported target with 422 **while Atlas is down**, because
  validation runs before any database access

### Still blocked

`gh auth login` is an interactive browser flow, so nothing is pushed and the GitHub
repo does not exist yet. `ssh -T git@github.com` also fails on host key verification.
No `.env`, so Atlas and Gemini remain unverified.

---

## Documentation and artefacts

### Repo is live

**https://github.com/mh-husssain/ghostspec** — private, `main`, 9 commits
pushed. Invited with write access: Prittstick22, PanotAbhay, Caroline3110.

### Documentation (the ask: enough for others to pick this up)

- `docs/ONBOARDING.md` — **the front door.** Setup, the non-negotiable rules, and a
  per-role section saying what already exists for that dev, what to implement, who they
  block and who blocks them, and the specific things that will bite them.
- `docs/architecture.md` — component map with owners, the main journey, scheduling,
  the state machine, the event journal, and a table of **where each safety property is
  actually enforced** rather than merely intended. Plus an honest what-is-real-today list.
- `docs/api-contracts.md` — every endpoint with implementation status, every error code
  with its HTTP status, the full event table, and the conventions that catch people out.
- `README.md` — rewritten as a front door that points at all of it.

### Artefacts and T-16

- `reports/artefacts.py` — patch (unified diff, baseline → final accepted revision),
  archive (deterministic zip of the accepted tree) and report (JSON). Files on disk
  under `WORKSPACE_ROOT/artefacts/<run_id>/`, only metadata + path + sha256 in Mongo.
- `verify_patch_matches_revision` implements **T-16**: it recomputes the diff and
  compares hashes, so a patch that is not the verified revision's diff is caught. A
  mismatch is written into the report's `limitations` rather than quietly shipped.
- Wired into `build_report`: artefact ids and `final_artefact_hash` are populated.

### Bug the tests caught

Windows text-mode writing translated `\n` to `\r\n`, so the patch file on disk did
not hash to the bytes we rendered — T-16 would have failed on Windows and passed on
Linux, and the `.patch` would have had CRLF endings that can break `git apply`.
Both artefact writers now pass `newline="\n"`.

### Proven by running it

- `uv run pytest` → **168 passed** (was 155)
- `uv run ruff check .` → All checks passed

### Still blocked

No `.env`, so Atlas and Gemini remain unverified. Dev 3's graph is what unblocks
`POST /repositories/{id}/plans`; Dev 4's verifier is what makes any gate real.

---

## Graph overlay and live-service tests

### Built

- `orchestration/overlay.py` — `get_unit_statuses(run_id)` per SHARED_CONTRACTS §8,
  **keyed by module_id, not unit_id**, because Dev 3's graph nodes are modules and a
  cyclic unit must paint every module in its SCC. Plus `get_graph_overlay` (adds
  `unit_id`, which `GraphNode` also needs) and `get_unit_statuses_safe`, which
  returns `{}` for a stale `run_id` so a bookmarked URL renders an un-overlaid graph
  instead of a 404 — while still letting a 503 through.
- `orchestration/__init__.py` now re-exports Dev 2's whole §8 surface, so other devs
  import from `app.orchestration` without knowing which file a function lives in.
- `tests/integration/test_atlas_live.py` and `test_gemini_live.py`, behind the
  `live` marker.

### The live tests answer the open Phase 0 questions

They skip without `.env`, so they are safe in the default run. With credentials they
report, with evidence:

- the **Atlas cluster version** and whether it supports native `$rankFusion` (8.0+)
  or `$scoreFusion` (8.2+) — if not, Dev 3 falls back to manual reciprocal rank fusion
- **which Gemini models the key can reach**, whether `gemini-4-argon` is among them,
  what to set `GEMINI_MODEL` to otherwise, and which embedding model and dimension
  count Dev 3 needs for `code_chunks_vector`

They also prove what unit tests cannot: 50 concurrent `append_event` calls produce 50
distinct sequence numbers (the atomic `$inc`), the unique index rejects a forged
duplicate, secrets land redacted **in the stored document**, and timezone-aware
datetimes survive the round trip.

**Isolation:** each run creates its own `axolotl_test_<random>` database and drops
only that, after asserting the name carries the test prefix. The real `axolotl`
database is never written to. The global client is swapped via monkeypatch so it is
restored even when a test fails.

### Proven by running it

- `uv run pytest` → **177 passed, 16 skipped**
- `uv run pytest -m "not live"` → 177 passed (the CI command)
- `uv run ruff check .` → All checks passed
- every relative link in the README and docs resolves

### Still blocked

No `.env`, so the 16 live tests have not actually run against Atlas or Gemini yet.
Dev 3's `load_networkx` still gates `POST /repositories/{id}/plans`.

---

## Two real bugs, found by writing tests

`.env` now exists locally (copied from `.env.example`, secrets still blank — fill
from the vault). Proven gitignored: `git check-ignore` matches `.gitignore:2`,
`git status` cannot see it, `git add -A --dry-run` does not stage it, and it appears
in zero commits.

### Bug 1 — a blank `.env` stopped the app booting (would have hit EVERY teammate)

`.env.example` ships `EMBEDDING_DIMENSIONS=` blank, and the README says to copy it
verbatim. Doing exactly that produced:

    ValidationError: embedding_dimensions
      Input should be a valid integer, unable to parse string as an integer

So the documented first run bricked the app, with an error that looks nothing like
"fill in .env". `Settings` now drops empty values before validation, so a blank field
falls back to its declared default. A genuinely invalid value (`"banana"`) is still
rejected. 9 tests.

**This only surfaced because `.env` was actually created.** It had been invisible for
the whole build.

### Bug 2 — unit status had two sources of truth (would have broken the first real run)

The controller tracked `self.status` (`migrating → verifying → passed`) while the
worker separately tracked `run.units[].status`, which only ever saw
`queued → migrating`. The worker then attempted `migrating → passed` and the state
machine correctly rejected it:

    IllegalTransition: illegal unit transition for unit_customers: migrating -> passed

Because `_execute` swallows exceptions into `run.error`, this would have looked like
a quietly failed run rather than a crash.

Fixed properly rather than by widening the transition table:

- the controller takes an `on_status_change` callback and writes through to the run
  document, so there is **one** status
- **the controller can no longer mark a unit `passed` at all.** Only the worker does
  that, and only *after* `accept_workspace` succeeds — otherwise a failed acceptance
  would leave a unit terminally passed with nothing accepted behind it

That is one fewer place that could violate the central invariant.

### Run worker now has tests (it had none)

30 tests driving whole runs through a fake Atlas + agent + verifier, with the fake store
round-tripping through `model_dump()` exactly as Mongo does. Covers dependency-first
order, T-13 gated progression (a blocked unit's dependants are skipped and **never
handed to the agent**), T-14 final verification always running, repair, the 3-attempt
budget, cancellation, missing baseline, and an unavailable agent.

Three parametrised invariants run across six scenarios each:
no run reports `completed` unless every unit passed; no unit is left non-terminal;
no path raises `IllegalTransition`.

### Proven by running it

- `uv run pytest` → **216 passed, 16 skipped** (was 177)
- `uv run ruff check .` → All checks passed

### Still blocked

`.env` secrets are empty, so the 16 live tests still skip and Atlas/Gemini remain
unverified. Dev 3's `load_networkx` still gates `POST /repositories/{id}/plans`.

---

## Bug 3: the report overstated how much was verified

### The bug

Every repair attempt re-emits that unit's gate results. `build_report` summed all of
them, so a unit verified three times tripled its own counts. A test with a 12-test
suite and three attempts produced:

    tests_passed: 34        (the true number is 12)

This is the worst kind of bug for this project specifically: the report is the artefact
a judge reads, and it was **overstating how much had actually been verified**.

### The fix

`_final_gate_results` takes the last `gate_result` per `(unit_id, gate)` — the
attempt that actually stands — rather than summing every attempt. Final-verification
gates carry `unit_id=None`, so they form their own key and are counted alongside the
per-unit totals instead of overwriting them.

### The report now has tests (it had none)

17 of them, about honesty rather than formatting: counters not inflated by retries,
counters still correct across multiple units and multiple gates, the "not a proof of
equivalence" caveat always present, skipped units named with their blocked
prerequisite, exposed held-out cases declared as no longer independent, a disabled
runner stated as "nothing in this report was verified by execution", inconclusive
declared as not passing, verdict mirroring run state (a cancelled run says cancelled,
not failed), counterexamples counted resolved only if their unit passed, and an absent
narrative staying `None` rather than becoming invented prose.

### Proven by running it

- `uv run pytest` → **233 passed, 16 skipped** (was 216)
- `uv run ruff check .` → All checks passed

---

## Adversarial review, six more fixes

Ran a multi-agent adversarial review over the orchestration core (it is the
safety-critical part and had only ever been read by the person who wrote it). It
independently re-found the status-divergence bug fixed above, which is a decent signal,
and surfaced these. All six are now fixed **with regression tests**, because none of the
existing tests caught them.

| # | Severity | Bug |
|---|---|---|
| 1 | critical | **The worker accepted `controller.workspace`, not the workspace the passing verification came from.** If the model passed verification and then edited again without re-verifying, the run accepted a tree **no verifier had ever looked at**. A direct violation of the central invariant. The controller now pins `verified_workspace` at the moment a verdict passes, and that is what gets accepted. |
| 2 | high | **Final verification ran against `workspace=None`.** The accepted revision was a local variable in `_migrate_units` and was thrown away, so T-14's whole-project check saw no tree. `_migrate_units` now returns it and final verification runs against a workspace at that revision. |
| 3 | high | **Cancelling mid-run crashed the worker.** `_is_cancelled` is called twice in sequence; the second call attempted `cancelled → cancelled`, raising inside the handler meant to record the cancellation. Now idempotent. |
| 4 | high | **A failed initial `store.get_run` left the run `queued` forever** and leaked the task handle. Moved inside the `try`. |
| 5 | medium | **Cancelling left every unit mid-flight** inside a terminal run. Non-terminal units are now marked `skipped`. |
| 6 | medium | **`RunSummary.tests_passed/failed/inconclusive` were never written** — permanently zero, which also made the report's "inconclusive is not a pass" caveat dead code. Now kept current per `(unit, gate)` so retries replace rather than add. |

Also fixed a false coverage claim: `build_units` took a module-keyed
`visible_case_counts` that no caller ever supplied, so every unit read as zero cases
and the plan told the user "no visible behaviour cases for \<every unit\>". Counts now
come from Dev 4's `load_cases` keyed by `unit_id` (no key translation to get wrong),
and when that is unavailable the plan says coverage **is not known** rather than
claiming there is none.

### Proven by running it

- `uv run pytest` → **241 passed, 16 skipped** (was 233)
- `uv run ruff check .` → All checks passed
- backend boots with the real `.env` present and reports every dependency honestly

The review is still running over the remaining dimensions; anything further will be
fixed and logged the same way.

---

## Session close: seven more fixes, and what is left

### Fixed this round (all with regression tests)

| Severity | Bug |
|---|---|
| **critical** | **A rejected or no-op patch could pass every gate.** If `apply_patch` rejected the model's patch, the workspace still held the **untouched baseline** — verifying it passed trivially and marked the unit migrated when nothing had been migrated. The controller now tracks `patch_applied` and refuses to verify a tree with no applied patch. |
| **high** | `create_workspace(run_id, repo_id, base_revision)` was being called with **`unit_id` in the `repo_id` position**, so Dev 4 would have looked up a repository that cannot exist. The controller now carries `repository_id`. |
| **high** | `final_verification_id` on the report was **the last unit's verification**, not the final one — a different artefact entirely. Final verification now emits with `unit_id=None, scope="final"` and the report matches on that. |
| **high** | **Exposing a held-out case was never journalled**, so the report's "a held-out case was revealed" caveat could never fire and a run silently lost an independent check. |
| **high** | The "not a proof" caveat claimed agreement across **`units_total`**, including units that were skipped or blocked. Now counts units that actually passed: "across 1 of 3 migration unit(s)". |
| **high** | **`GET /runs/{id}/units/{unit_id}` did no held-out redaction** despite its docstring promising it. A sealed held-out case's inputs and values are now stripped; an exposed one is shown in full. |
| **high** | `bytes` payload values **bypassed both `redact()` and `truncate()`**, so a secret inside one would be stored verbatim in `run_events`. |
| medium | Unexpected errors bypassed the §6 envelope — a reachable-but-failing Atlas returned a bare 500 clients cannot parse. Now enveloped as `INTERNAL_ERROR`, with only the exception **type** in the message (its text can carry a connection string). |
| low | A held-out case was exposed **even when no attempts remained** — spending an independent check the model could no longer act on. |

That last one was found by a probe file a review agent left in
`backend/tests/unit/test_zz_probe_expose.py`. It is **untracked** so it never reaches
the repo, but it will fail locally on this machine until deleted:

    rm backend/tests/unit/test_zz_probe_expose.py

### Known and NOT yet fixed — pick these up next

Ranked. All found by the review; none are blocking the gates.

1. **medium** — `run_verification` has no budget of its own and the latest result
   wins, so a model could re-verify repeatedly for free. Worth a cap.
2. **medium** — the archive artefact (`write_archive_artefact`) is implemented and
   tested but **never called** by `build_report`; it needs the accepted tree's path
   from Dev 4.
3. **medium** — `counterexamples_found` misses final-verification counterexamples and
   may count per-attempt duplicates.
4. **medium** — the exposure cap is per *case*, not per *unit*: one held-out case can
   be released per attempt, so a 3-attempt unit could leak three.
5. **medium** — `GET /runs/{id}/units/{unit_id}` always returns an empty
   `verifications` array (blocked on Dev 4 writing `verification_results`).
6. **low** — `run.last_event_sequence` only advances on `run_state_changed`, so the
   persisted value lags the journal.
7. **low** — `GET /runs/{id}/diff` silently ignores an unknown `unit_id` and returns
   the whole-run diff.
8. **low** — `patch.unit_id` is neither validated against the unit nor recorded.

### Proven by running it

- `uv run pytest` → **256 passed, 16 skipped**
- `uv run ruff check .` → All checks passed (tracked files)
- backend boots and reports every dependency honestly

### State at end of session

- `main` is clean and pushed (`git log --oneline` for the count).
- `.env` exists and is gitignored; **both secrets are still empty**, so the 16 live
  tests skip and Atlas/Gemini remain genuinely unverified.
- Three collaborators invited (invitations pending acceptance).

### Next session, in order

1. Fill `MONGODB_URI` and `GEMINI_API_KEY` in `.env`, then `uv run pytest -m live -s`.
   That answers the Atlas cluster version / `$rankFusion` question and tells you which
   Gemini models the key can reach.
2. Dev 3's `load_networkx` unblocks `POST /repositories/{id}/plans` — the single
   biggest unblock for the demo path.
3. Work the "not yet fixed" list above.

---

## Review completed: full finding ledger

The adversarial review finished: **50 agents, 44 findings judged, 19 survived
refutation, 25 refuted.** Refuting three quarters of them is the point — a review that
confirms everything it finds is not a review.

### All 19 confirmed findings, with status

| Severity | Finding | Status |
|---|---|---|
| critical | Worker accepted `controller.workspace`, not the workspace the passing verification came from | **fixed** |
| critical | A no-op or sandbox-rejected patch verified the untouched baseline and passed every gate | **fixed** |
| high | Report and diff read the journal through a hard 1000-event ceiling, publishing an intermediate revision as the verified one | **fixed** |
| high | Cancellation checked twice in a row crashed `_execute` | **fixed** |
| high | Held-out exposure was never journalled, so the report's caveat was unreachable | **fixed** |
| high | `create_workspace` called with `unit_id` in the `repo_id` position | **fixed** |
| high | Counters kept gate results from discarded attempts | **fixed** |
| medium | `run.error` persisted and served without `sanitise()`, unlike the same string on the event path | **fixed** |
| low | Cancellation wrote unit statuses straight onto the document, bypassing `transition_unit` and the journal (T-15) | **fixed** |
| low | A held-out case was exposed even when no attempts remained | **fixed** |
| low | `GET /runs/{id}/diff` silently ignored an unknown `unit_id` and returned the whole-run diff | **fixed** |
| medium | A cancellation arriving **during final verification** is silently dropped and the run reports `completed` | open |
| medium | The exposure cap is per case, not per unit: a 3-attempt unit could leak three held-out cases | open |
| medium | `run_verification` has no budget of its own; verification can be re-rolled for free | open |
| medium | `RunSummary.last_event_sequence` is not maintained by `append_event` | open |
| medium | The archive artefact is implemented and tested but never called by `build_report` | open |
| medium | `counterexamples_found` misses final-verification counterexamples and counts per-attempt duplicates | open |
| low | `run.last_event_sequence` only advances on `run_state_changed` | open |
| low | An attempt is spent when the sandbox was never reached, then the unit blocks as if the budget ran out | open |

**Eleven fixed, eight open.** None of the open eight is blocking a gate. The highest
value of them is the cancellation-during-final-verification one: it is the only
remaining path where a run could report `completed` when it should not.

### This round's three fixes, in detail

1. **The 1000-event ceiling (T-16).** `build_report` and `GET /runs/{id}/diff` both
   read `list_events(..., limit=1000)` and then took the **last**
   `snapshot_accepted`. On a run with more than 1000 events that is an *intermediate*
   revision, so the published diff would not be the diff of what was verified. New
   `list_all_events` pages the whole journal.
2. **`run.error` leaked secrets.** The identical string is redacted on the event path
   but was persisted raw on the run document and served straight out of
   `GET /runs/{id}`. Now redacted and truncated.
3. **Cancellation broke the audit trail.** My own regression from the previous round:
   the cancel path wrote `status = "skipped"` directly, so the journal still claimed
   those units were running. Now routed through `_set_unit`, which validates and
   journals. `skipped` is now reachable from the in-flight unit states, documented as
   carrying both meanings (T-13 "never attempted" and cancellation "abandoned"), since
   `UnitStatus` has no `cancelled`.

### Housekeeping

Review agents left two **untracked** probe files in `backend/tests/unit/`
(`test_zz_probe_expose.py`, `test_zzz_refute_probe.py`). Neither can reach the repo,
but both fail locally until removed:

    rm "C:\Users\hisha\Documents\axolotl\backend\tests\unit\test_zz_probe_expose.py"
    rm "C:\Users\hisha\Documents\axolotl\backend\tests\unit\test_zzz_refute_probe.py"

Both earned their keep: the first found the no-attempts-left exposure bug, the second
the cancellation audit-trail gap.

### Proven by running it

- `uv run pytest` → **260 passed, 16 skipped** (excluding the two stray probes)
- `uv run ruff check .` → All checks passed

---

## Dev 2 lane closed: all 19 review findings fixed

Staying in lane and finishing Dev 2's work before touching anyone else's. All eight
findings that were left open are in Dev 2's files, so this closes the ledger:
**19 confirmed findings, 19 fixed.**

| Severity | Finding | Fix |
|---|---|---|
| medium | A cancellation arriving **during final verification** was dropped and the run reported `completed` | `_execute` now re-checks cancellation after the final check, before the verdict. This was the last path where a run could overstate itself. |
| medium | `run_verification` had no budget; the model could re-roll it for free until a flaky gate passed | One verification per **applied** patch. Verifying again requires a new patch, which costs an attempt. Reuses the `patch_applied` guard rather than adding a second counter. |
| medium | The held-out exposure cap was per **case**, so a 3-attempt unit could release three | Cap is now per **unit** (`MAX_EXPOSED_PER_UNIT = 1`). |
| medium | `RunSummary.last_event_sequence` only advanced on `run_state_changed`, so the value the frontend polls from lagged the journal | `append_event` records the high-water mark; a `_save` helper keeps the run level with it. |
| medium | The archive artefact was implemented and tested but **never called** | Wired into `build_report` via a new `ports.workspace_path` (Dev 4 supplies the accepted tree's path; degrades honestly until then). |
| medium | `counterexamples_found` missed final-verification counterexamples and counted per-attempt duplicates | Final verification now journals its counterexamples; the report dedupes on `counterexample_id`. A final-verification counterexample is never counted *resolved* — nothing runs after it. |
| low | `run.last_event_sequence` lagged | Same `_save` fix. |
| low | An attempt was spent when the sandbox was never reached, then the unit blocked as if the budget ran out | The attempt is refunded on `IntegrationUnavailable`. Blocking a unit because infrastructure failed blames the model for someone else's problem. |

### A test that was wrong, not the code

The one-verification-per-patch rule broke
`test_only_a_passing_verification_makes_a_unit_verified`, which verified twice without
re-patching. The test was modelling a loop the real agent never runs; updated to
submit → verify → submit → verify.

Similarly, `test_last_event_sequence_tracks_the_journal` failed at first because the
fake `append_event` in the worker tests did not mimic the real one's sequence
bookkeeping — it was testing the fake. The fake now records the high-water mark too.

### Proven by running it

- `uv run pytest` → **270 passed, 16 skipped**
- `uv run ruff check .` → All checks passed
- Backend boots; `/system/status` honest, `/migration-targets` returns 5,
  database-backed endpoints return 503 `DATABASE_UNAVAILABLE`

### Dev 2's lane is now done for the current gates

What remains for Dev 2 is process, not code: running a cold-start E2E at each gate and
calling scope cuts. **Next session moves into other lanes** — Dev 3's import + AST +
dependency graph is the keystone, since `POST /repositories/{id}/plans` returns 503
until `load_networkx` exists and nothing downstream can run without it.

---

## Import, AST and dependency graph (Dev 3's area, on dev2/ingestion-graph)

Dev 2's lane is closed, so this is the keystone that unblocks everything:
`POST /repositories/{id}/plans` returned 503 until `load_networkx` existed.

**Deliberately scoped to the plumbing.** The Atlas Search / Vector / hybrid layer,
chunking, descriptions and embeddings are untouched and still Dev 3's — that is the
part that scores MongoDB bounty points.

### Built

- `app/analysis/identifiers.py` — every §3 id as a pure function of content, so a
  reimport of the same tree yields the same ids and a run's references stay valid.
- `app/ingestion/safety.py` — the T-02 guard. Rejects absolute paths, drive letters,
  `..` traversal, symlinks and decompression bombs; enforces 10 MB / 50 MB / 500 files
  / depth 12; ignores `.git`, venvs, `node_modules`, `__pycache__`, `.env` and key
  files.
- `app/analysis/astwalk.py` — modules, classes, functions, async functions, methods and
  nested functions with signatures, decorators, bases, docstrings and line spans.
  Static only: nothing is imported or executed. A syntax error is a file-level warning
  and a `partial` repository, never a crash.
- `app/graph/builder.py` — import resolution (absolute, relative, `from pkg import
  submodule`, prefix walking), external `ext_` nodes, `test_targets`, dynamic imports
  as `unknown_dynamic` with `resolution="unresolved"`, and `to_networkx`.
- `app/ingestion/__init__.py` — import pipeline, snapshot copy, snapshot hashing,
  read-only marking.
- `app/persistence/graphstore.py` + `indexes.py` — the §9 collections and indexes,
  wired into the lifespan.
- `app/graph/__init__.py` — `get_graph`, `load_networkx`, `get_module_detail`,
  `get_symbol`, `find_symbol`, with the run status overlay calling Dev 2's mapping
  rather than recomputing it.
- `app/api/repositories.py` — all 7 of Dev 3's §6 endpoints. 22 registered in total.

### The decision that matters

An edge is `confirmed` only when the import resolves to a file **actually in the
snapshot**. `to_networkx` then keeps local modules and confirmed dependency edges only
— no test modules (never migrated), no external packages (cannot be changed), no
heuristics. Migrating in an order derived from a guess is worse than refusing to guess.

### Proven by running it

- **T-01** every fixture module parses; 6 modules + 1 test module; no warnings
- **T-02** traversal, absolute-path, drive-letter, symlink and bomb archives all
  rejected — **and the destination is asserted empty afterwards**, because "it raised"
  is not the same as "it wrote nothing"
- **T-03** `to_networkx` gives **exactly** the 7 expected edges, correct direction,
  no extra, no missing — asserted against the real fixture on disk, so it fails if the
  fixture and the graph ever drift
- **T-04** `finalise_amount` found with a line span that brackets it in the real file;
  ids deterministic across reimport
- **T-09** snapshot hash reproducible, and content- and layout-sensitive
- `uv run pytest` → **329 passed, 16 skipped** (was 270)
- `uv run ruff check .` → All checks passed
- Live: `GET /demo-fixtures` works with no database; `POST /repositories/import` is
  503 without Atlas and 422 for an unknown fixture (validation before any DB access)

### A layering bug caught on the way

`analysis` imported from `ingestion`, which imports `analysis` — a circular import
that only showed up at boot. Fixed by moving the identifiers into `analysis`, where
they belong, rather than deferring an import to paper over it.

### Known gaps, for Dev 3

1. **Search is untouched** — chunking, descriptions, embeddings, `code_chunks`, the
   Atlas Search and Vector indexes, and the hybrid `$rankFusion` path. `search.py` is
   still an empty router. **This is yours and it is the bounty work.**
2. A failed import leaves an orphaned snapshot under `WORKSPACE_ROOT/snapshots/`.
   Harmless and gitignored, but it should be cleaned up on failure.
3. `ModuleDetail.test_case_count` is hard-coded 0 until Dev 4's `load_cases` lands.
4. `calls` edges are not extracted. The brief says best-effort and
   `resolution="heuristic"`, never used for scheduling — skipped rather than guessed.
5. Snapshot read-only marking is best effort on Windows; the real protection is that
   no code path opens a snapshot for writing.

---

## CI

`.github/workflows/ci.yml` runs on every PR into `main` and on every push to it:
backend `uv sync --frozen` → `ruff check` → `pytest -m "not live"`, and frontend
`npm ci` → `lint` → `build`. This is what makes SHARED_CONTRACTS §12's "`main`
must always boot" enforceable rather than aspirational once five people are pushing.

`-m "not live"` is deliberate. The Atlas and Gemini tests skip themselves without
credentials, but excluding them keeps CI honest about what it actually proved: there
are no secrets on the runner, so nothing needing a real cluster or a real model has
been verified there.

There is also a non-blocking step listing any remaining `*_stub`, so the §0.2 cleanup
before the Sat 17:00 dry run is visible on every run rather than remembered.

### A bug found while verifying it

`frontend/package-lock.json` still carried `vite_scaffold` — the throwaway scratch
directory the Vite template was generated in — while `package.json` said
`axolotl-frontend`. **`npm ci` fails outright on that mismatch**, so CI would have
been red on its first run and every teammate's first clean install would have failed.
Regenerated the lockfile and verified `npm ci --dry-run` resolves, plus lint and build.

(A plain `npm ci` cannot be run on this Windows machine: it fails with
`EPERM: unlink` on a locked `rolldown-binding.win32-x64-msvc.node`. That is a
Windows DLL-locking quirk, not a lockfile problem — the Linux runner is unaffected.)

---

## Verification layer (Dev 4's area, on dev2/verification-layer)

The last structural blocker. Every gate returned `inconclusive` because
`capture_baseline` and `verify_unit` did not exist.

### Built

- `sandbox/workspaces.py` — `create_workspace` (a fresh copy of the last accepted
  revision, every attempt), `apply_patch`, `accept_workspace`, `diff`,
  `workspace_path`.
- `sandbox/runner.py` — the Docker runner under `runner/policy.md`'s profile, plus a
  **real** `runner_status`. The `/system/status` runner chip now reports
  `available: true` with an image digest for the first time.
- `verification/comparison.py` — the §5 contract: outputs equal iff canonical
  encodings equal; exceptions match on **type name only**, messages reported but
  never compared.
- `verification/cases.py` — case loading, `case_hash`, persistence, exposure.
- `verification/store.py` — `verification_results`, `counterexamples`, and an
  additive `baseline_outcomes` collection (the oracle has to live somewhere
  queryable; in memory it would not survive a restart).
- `verification/__init__.py` — `capture_baseline`, `verify_unit` (the five gates in
  Spec §7.3 order), `verify_final`, `get_counterexamples`, `expose_case`.

### §0.2 cleanup, done early

`grep -r _stub backend/` now comes back **empty**. The runner placeholder was the
only one, and it is gone. CI reports this on every push so it stays that way.

### The claim, proven end to end

Nine tests run the **actual container** under the **actual profile** against the
**actual fixture** — no mocks:

- the held-out differential catches a banker's-rounding patch, **2.65 → 2.64**
- **the fixture's own 12 tests stay green on that same patch** — the gap that
  justifies the entire product, now asserted rather than asserted-about
- the baseline agrees with itself across two runs (16/16), so the above is signal
  and not noise
- network blocked, `/code` read-only, `/tmp` writable, compile gate fails a broken
  tree

CI now builds the runner image and runs these on every PR, because this is the
central claim and one laptop is not evidence.

### Honest caveat

`capture_baseline`, `verify_unit` and `verify_final` are **written but not yet
exercised end to end** — they persist through Atlas, and there is still no `.env`.
What is proven is the runner, the workspaces, the patch validation and the
comparison. The orchestration glue above them is tested only through fakes.

### Decisions worth knowing

- **No process-global state.** `verify_unit`'s §8 signature carries `run_id` but not
  `repo_id`, and cases are keyed by repository. A module-level cache would be empty
  after a restart and silently return **no cases**, which reads as "nothing to
  verify" — exactly the quiet wrong answer this project exists to avoid. It reads
  the run document instead.
- **`steps` without a contract change.** Four fixture cases use a `steps` sequence
  that `BehaviourCase` has no field for (a known §11 gap). Rather than change a
  shared contract unilaterally, the harness payload is built from the source case
  files, which already carry it.
- **A half-applied patch is never verified.** `apply_patch` validates every file
  before writing any, so one bad path rejects the whole patch.

### Proven by running it

- `uv run pytest` → **355 passed, 16 skipped** (was 329)
- CI selection (`not live and not docker`) → 346 passed
- `uv run ruff check .` → All checks passed

### CI caught a real bug the same hour it was added

The new sandbox job failed on its first run while backend and frontend passed.
Not a flaky test — a genuine defect that Windows had been hiding:

`_set_writable` and `_make_read_only` used `chmod(S_IREAD|S_IWRITE)` and
`chmod(S_IREAD)`, i.e. `0o600` and `0o400` — **owner only**. The sandbox container
runs as **uid 65534 (nobody)**, which does not own those files, so on Linux the
workspace and the snapshot are unreadable inside the container: every behaviour case
fails to import and pytest cannot even collect. On Windows `chmod` only toggles a
read-only attribute, so all nine tests passed locally.

This would have broken baseline capture and every gate on any Linux host — including
the demo laptop if it were not Windows. Modes are now `0o444`/`0o555` read-only and
`0o644`/`0o755` writable (directories need `+x` to be traversed). The mounts are
still `:ro`, so the container cannot write.

Worth noting plainly: this is the exact bug class that a laptop-only workflow does
not catch, found within an hour of CI existing.

---

## Gemini verified: Argon is visible but has NO quota

`GEMINI_API_KEY` is now in `.env`. Ran the live Gemini tests. Results, with evidence:

| Question | Answer |
|---|---|
| Is `gemini-4-argon` available? | **Yes** — it is in the model list and `models.get` succeeds |
| Can we actually generate with it? | **NO** — `generateContent` returns **429 RESOURCE_EXHAUSTED**: "You exceeded your current quota, please check your plan and billing details" |
| Does anything work? | **Yes** — `gemini-3.8-flash` generates fine |
| `gemini-2.5-flash`? | 404, no longer available to new users |
| Embedding models | `gemini-embedding-001`, `gemini-embedding-2`, `gemini-embedding-2-preview` — Dev 3 picks one and records its dimensions for `code_chunks_vector` |

Also present in the list: `antigravity-preview-09-2026`, which Spec §16 flags as an
unresolved assumption.

### What this means

**Dev 1 is blocked on Argon specifically, not on Gemini.** The agent loop cannot run a
single generation against `gemini-4-argon` on this key. `/system/status` still shows
`gemini.reachable: true` because the reachability check uses `models.get`, which
succeeds — the quota wall only appears on a real generation.

The brief's own guidance covers this: *"If something isn't available yet (for example
Gemini 4 Argon access), put it behind a setting, use a fallback, and keep going."*
`GEMINI_MODEL` is already that setting, and the model name is read from config
everywhere — so switching is a `.env` edit, not a code change.

### Actions

1. **Ask the organisers about Argon quota.** This is exactly the "Gemini 4 Argon
   access and quotas" question every brief said to raise at kickoff. Show them the 429.
2. Until quota exists, set `GEMINI_MODEL=gemini-3.8-flash` so Dev 1 can build and the
   demo path can run end to end.
3. Switch back to `gemini-4-argon` the moment quota is granted — one line, no rebuild.
4. Tell Dev 3 the embedding options above.

**Honesty note for the submission:** if the demo ends up running on a fallback model,
the README and the video must say which model actually produced the patches. Claiming
Argon while running `3.8-flash` would be exactly the kind of thing this project exists
to not do.


## Axolotl documentation review branch

- Updated from main and prepared `dev1/axolotl-docs` for review.
- Reconciled all 11 supplied planning documents; retained newer PyMongo corrections.
- Renamed product branding and specification filename to Axolotl, with matching references.
- Kept repository URL, database, Docker image and internal identifiers compatible.
- Recorded the selected Python 2 terminal chess demo; existing orders fixture remains until chess integration is delivered.
- Validation: 346 offline backend tests passed, 9 skipped, 16 live tests excluded; Ruff passed. MongoDB was disabled in the test process because local MONGODB_URI has an invalid URI scheme; .env was not modified.
---

## FIRST FULL END-TO-END RUN (real Atlas, real Docker)

`MONGODB_URI` is in. The pipeline has now run end to end for the first time, against
the real cluster and the real sandbox. Nothing below is mocked.

### Atlas, verified

M10, London, **MongoDB 8.0.34** — confirmed programmatically, not from the console.

- native `$rankFusion` (needs 8.0+): **YES** — Dev 3's hybrid search can be the real
  thing, not a labelled fallback
- native `$scoreFusion` (needs 8.2+): **NO**
- 12/12 live tests pass, including 50 concurrent `append_event` calls producing 50
  distinct sequence numbers, the unique index rejecting a forged duplicate, and
  secrets coming back redacted **in the stored document**

### `/system/status`, three of four chips green for the first time

    mongodb        ok            (8.0.34)
    runner         available     trusted_docker
    gemini         reachable     gemini-3.8-flash
    search_indexes unavailable   <- Dev 3's search layer does not exist yet

### The run

| Stage | Result |
|---|---|
| Import | 10 files, 7 modules, 40 symbols, **0 warnings**, `executable=true` |
| Graph (read back from Atlas) | 6 local + 1 test + 3 external nodes; the exact **7** dependency edges |
| Plan | deterministic order; **real** case counts (pricing 8, orders 3, refunds 3); risk computed; coverage gaps naming `unit_main` and `unit_storage` |
| Baseline | **26 cases captured in a real container, 0 failed** |
| Units | 3 blocked (no agent), 3 **skipped** — T-13 propagation working on a live run |
| Final verification | ran anyway (**T-14**) |
| Verdict | `completed_with_warnings` — **never** `completed` |

### The report told the truth without being asked to

> Agreement shown only for 26 tested case(s) across **0 of 6** migration unit(s);
> this is not a proof of equivalence.
>
> Not attempted because a prerequisite was blocked: unit_main, unit_orders, unit_refunds
>
> No unit completed verification in this run.
>
> Errors recorded during the run: AGENT_UNAVAILABLE

`narrative` is `None` rather than invented prose. Every honesty mechanism built over
the last two days did its job under a real run, unprompted.

### What this proves, and what it does not

**Proves:** import → AST → graph → Atlas → plan → baseline-in-Docker → gated
progression → final verification → report is a working chain against real
infrastructure. The orchestration layer that was previously only tested through fakes
now has a real execution behind it.

**Does not prove:** that anything can be *migrated*. Zero units passed, because Dev 1's
agent does not exist, so nothing ever proposes a patch. The verifier has never been
handed a candidate by a model.

### Remaining

1. **Dev 1's agent** — the only thing standing between this and a complete demo path.
2. **Dev 3's search** — `search.py` still has 0 routes; the whole MongoDB bounty
   surface, now known to support native `$rankFusion`.
3. Embedding model for Dev 3: `gemini-embedding-001`, `gemini-embedding-2` or
   `gemini-embedding-2-preview`.

---

## Dev 5 renames to Axolotl, adds runtime fields, keeps React 19

### GhostSpec is now Axolotl

The team agreed the new name on Fri 23 Oct. Every tracked file is renamed, including
names that machines and other code depend on:

Dev 1's rename on main (`6086a50`) changed display names only and kept these
identifiers on the old names. Dev 5 renamed them as well, and README now says so.

| What | Old | New | What you need to do |
|---|---|---|---|
| Mongo database default | `ghostspec` | `axolotl` | With no `MONGODB_DATABASE` in your `.env` you now get an empty `axolotl` database **with no Atlas Search indexes** (nothing creates them in code). Either set `MONGODB_DATABASE=ghostspec` to keep the old data, or create the indexes on `axolotl`. |
| Runner image | `ghostspec-runner:latest` | `axolotl-runner:latest` | `docker build -t axolotl-runner:latest runner/`, or set `RUNNER_IMAGE`. |
| Workspace root | `./.ghostspec_workspaces` | `./.axolotl_workspaces` | Delete the old folder when convenient. `.gitignore` still ignores it. |
| Harness timeout variable | `GHOSTSPEC_CASE_TIMEOUT` | `AXOLOTL_CASE_TIMEOUT` | Rename it if you set it. |
| Package names | `ghostspec-backend`, `ghostspec-frontend` | `axolotl-backend`, `axolotl-frontend` | Nothing. |
| Planning doc | `GhostSpec_Product_Requirements_and_Implementation_Handoff.md` | `Axolotl_Product_Requirements_and_Implementation_Handoff.md` | Nothing; links are updated. |

Not renamed: the GitHub repo is still `mh-husssain/ghostspec`, so the clone URL and
`cd ghostspec` stay as they are until the owner renames the repo.

### Runtime before and after (SHARED_CONTRACTS §11, additive)

`CaseOutcome.duration_ns`, `UnitTiming`, `PerformanceSummary` and `RunReport.performance`
are new optional fields, all `None` by default, added to §4, `contracts.py` and
`contracts.ts` together. Nothing fills them yet. Dev 4 times each case inside the
harness; Dev 2 aggregates per unit at final verification. The report hides its Runtime
panel while `performance` is null.

### React 19 is ratified

Dev 5 kept React 19. Deviation 2 above is closed and §2 now says React 19.

---

## Dev 5 adds the UI template to `docs/design/`

`docs/design/` holds the UI/UX template that `frontend/` follows: tokens, nine screens,
states, storyboard and components, in light and dark. Open `docs/design/index.html`. It
replaces the Figma file, which is out of date.

The 08 Final report verdict now follows Atlassian's warning-message pattern:

- A tinted panel with no border and the warning triangle on the left. The old dashed border is gone.
- A three-word title, then two sentences: why, the risk, and what to do.
- The caveats, then the run's numbers.

`completed_with_warnings` gets its own yellow tokens, `--warning` and `--warning-tint`, so
it no longer shares the repairing orange. Light is `#7A5C00` on `#FFF7D6` and dark is
`#F5CD47` on `#3D3720`. Every text pair passes 4.5:1 in both themes.

## Dev 3's search layer (`dev2/search-layer`)

Chunking, descriptions, embeddings, Atlas Search + Vector indexes, and hybrid search
over native `$rankFusion`. `search.py` had 0 routes; it now has 2, and the last red
chip on `/system/status` is green.

### What shipped

| File | What |
|---|---|
| `app/search/chunking.py` | one chunk per function / method / class / module, deterministic ids |
| `app/search/descriptions.py` | one sentence per chunk from Gemini; source framed as untrusted data |
| `app/search/embeddings.py` | `gemini-embedding-001` @ 768, separate task types for documents and queries |
| `app/search/indexes.py` | both index definitions, plus create / update-on-drift / rebuild-on-failure |
| `app/search/retry.py` | jittered backoff for transient model failures |
| `app/search/pipeline.py` | build → describe → embed → persist → wait until searchable |
| `app/search/__init__.py` | keyword, vector, hybrid (`$rankFusion`) + Python RRF fallback |
| `app/api/search.py` | `GET .../search`, `POST .../index` |

46 new tests (35 offline, 11 live). Offline suite: **395 passing**, ruff clean.

### T-06 and T-07, on the real cluster

| query | mode | result |
|---|---|---|
| `finalise_amount` | keyword | `pricing.finalise_amount` rank 1, score 43.0 (next 0.51) |
| `finalise` | keyword | finds it too |
| "where does the final customer bill get rounded?" | hybrid | `pricing.finalise_amount` rank 1, `native_hybrid: true` |
| "what does a shopper owe on their receipt?" | vector | `pricing.finalise_amount` top 3 |

The last query shares **no content word** with `pricing.py` — "shopper", "owe" and
"receipt" appear nowhere in it. A keyword index cannot answer it at all.

And the case for hybrid existing, measured rather than claimed: on the T-07 query the
keyword index ranks the rounding function **fifth**, the vector index ranks it
**first**, fusion puts it **first**. `test_fusion_can_beat_keyword_search_on_its_own`
will fail if that ever stops being true.

### Four bugs found by running it, none of which a test would have caught

**1. The text index silently FAILED.** `qualified_name` was defined as a list of two
`string` entries with different analysers. Atlas rejected it: *"field contained
multiple [string] type definitions"*. A `FAILED` index is not queryable at all, and
nothing in the code was wrong — the definition was plausible and the API accepted the
create call. Alternate analysers belong in `multi`.

**2. T-06 failed with a valid, READY index.** `lucene.standard` does **not** split
`pricing.finalise_amount` on the dot — it indexes the whole dotted name as one token.
So searching `finalise_amount` matched that field *not at all*, and `finalise` matched
nothing anywhere in the index. It worked for `discount_rate` only by accident, through
the `identifiers` field. Fixed with a custom `identifier_parts` analyser
(`regexSplit` on `[^a-zA-Z0-9]+`, lowercased), which is now what T-06 rests on.

This is the one worth remembering: **every offline test passed, the index was READY,
and the headline feature returned zero results.** No amount of reading the definition
would have found it. Only querying it did.

**3. A failed or outdated index stayed that way forever.** `ensure_search_indexes`
skipped anything that existed by name, so the FAILED index from (1) could never be
repaired, and after fixing (2) the live index would have kept answering under the old
rules with nothing saying so. It now drops and recreates a FAILED index, and calls
`update_search_index` when the live definition has drifted from the code.

**4. Atlas Search is eventually consistent.** The first search after an import
returned nothing; the same query seconds later worked. Import now waits for its own
chunks to become searchable (45s cap) and warns if they do not. "I imported it and
search found nothing" is indistinguishable from a broken search.

### Two more, found the same way

**All forty descriptions failed**, reported only as `ClientError` — the type with no
message, which cost a round of guessing. The real error was
`503 UNAVAILABLE — "This model is currently experiencing high demand"`. Nothing was
wrong with the code; the model was busy. Added `app/search/retry.py` (jittered
exponential backoff over 429/500/502/503/504 and timeouts) and made every failure
report `type: message`. A later run hit `429 RESOURCE_EXHAUSTED` — the daily quota —
so a spent quota now short-circuits the remaining chunks with **one** warning instead
of forty retrying copies.

**An "in-memory" import test was writing to the real Atlas.** `test_import_pipeline`
stubs `graphstore` and `persist_cases`, but chunk persistence was new and unstubbed,
so eleven imports per run reached the live `code_chunks` collection on any configured
machine. Stubbed, and the fixture now also captures chunks for assertions.

### Deliberate decisions

- **`SearchHit` gained `qualified_name` and `kind`** — additive under §11 (optional,
  default `None`, no consumer breaks). §4 gives a hit a path and a line range but no
  name, so a result list would read `pricing.py:48-106` with no way to say which
  function that is without a second request per hit. Flagged for Dev 5; ignorable.
- **`INDEX_ON_IMPORT`** (new, additive, default true) gates descriptions and
  embeddings only. Chunks are always built and stored, so keyword search works with
  no API key at all. The test suite turns it off: enrichment is two model calls per
  chunk and the import tests import the fixture eleven times.
- **`native_hybrid` is measured per request.** If `$rankFusion` fails, the Python RRF
  fallback runs and reports `false`. Claiming native fusion while the fallback ran
  would misrepresent how the answer was produced.
- **A failed description stays `None`; a failed embedding stays `None`.** Never an
  invented sentence, never a zero vector — a zero vector sits at a fixed distance from
  every query and quietly pollutes every result.
- **Import warnings are now persisted.** Behaviour cases and search indexing both run
  after the record is written, so their warnings previously lived only on the object
  returned to one caller and never reached `GET /repositories/{id}`.
- **`search_health.py` now delegates to `app.search.indexes.search_status()`** instead
  of keeping its own copy. Two implementations of "is the index ready?" could
  disagree, and the status chip would then contradict the endpoint beside it.

### T-18, incidentally

The fixture's prompt injection in `customers.category_for` went through the description
model. It described the injection as *present* ("while notably containing…") rather
than acting on it — which is the behaviour the prompt asks for.

### `/system/status`, all green for the first time

```
mongodb:        ok (8.0.34)
search_indexes: {"keyword": "ok", "vector": "ok"}
runner:         {"mode": "trusted_docker", "available": true}
gemini:         {"model": "gemini-3.8-flash", "reachable": true}
```

### Merged with main, and one correction to Dev 5's rename note

Merged `origin/main` (6 commits: Dev 3's persistence/indexes work, Dev 5's Axolotl
rename and UI template). Conflicts in `BUILD_LOG.md`, `docs/mongodb-atlas.md` and
`app/search/__init__.py`; both Atlas docs were kept, since they cover different ground
— theirs collections and regular indexes, mine the search layer.

Dev 5's rename table says of the `axolotl` database: *"with no Atlas Search indexes
(nothing creates them in code)"*. **That is no longer true as of this branch** —
`ensure_search_indexes()` runs at startup and creates, repairs or updates both. A fresh
`axolotl` database needs an import to populate `code_chunks`, but not manual index work.

Dev 3's `app/search/status.py` arrived on main as a shim over `search_health`. Kept
under its §8 name, now delegating to `indexes.search_status()` so there is one
implementation rather than three that could disagree.

### Remaining

1. **Dev 1's agent** — still the only thing between this and a complete demo path.
   Dev 1 is active (pushed `dev1/axolotl-docs`), so this needs a conversation, not a
   takeover.
2. **Dev 5's frontend** — status chips only; 8 screens unbuilt, including the search
   UI this layer now backs.
3. **Gemini description quota is spent** for the day. Search works without
   descriptions (proven above), just less well. `POST /repositories/{id}/index`
   re-runs enrichment once quota returns.

---

## `queryable`, and two people fixing one bug at once

Found ten minutes after merging #8, by running the path a teammate would take rather
than the one I had been testing: pointing at the renamed `axolotl` database and
watching a fresh index come up.

```
NAME code_chunks_text   | STATUS BUILDING | QUERYABLE True
NAME code_chunks_vector | STATUS PENDING  | QUERYABLE False
```

`classify()` read `status` alone, so it called that text index `building` and the
endpoint returned **503 for a keyword search that would have worked**.

### Dev 3 found it first, and the overlap is the real lesson

While I was fixing it, Caroline landed `0787edc` on main with the same fix, plus two
things I did not have: a `HeldOutLeak` guard so held-out cases can never be chunked
even if ingestion's own check were bypassed, and `symbols.source_chunk_id`. Her
`classify(status, queryable)` and `search_status()` changes are the same as mine.

That is the second collision in Dev 3's lane in one afternoon, and this time the
message warning about the first had already been sent. The honest reading: a warning
after the fact does not prevent a collision, and neither does good intent. What would
have prevented it is asking before entering the lane, not reporting after.

So PR #9 was reduced from "the fix" to the delta hers does not cover, merged on top of
her work rather than around it:

| Atlas says | hers | with this | why it matters |
|---|---|---|---|
| `BUILDING` + queryable | ok | ok | hers, the main case |
| `READY` + **not** queryable | ok | **building** | a green chip over a search that fails |
| `FAILED` + queryable | ok | **unavailable** | a failed index needs a rebuild, not patience |
| no `queryable` field | status alone | unchanged | older response shapes |

The second row is the one neither of us would have reached for: `queryable` has to win
in *both* directions, or the status chip can read green over a search that cannot run.

Her parametrised `test_readiness_follows_what_atlas_says` already covered the rest, so
the two cases were added to it rather than given a duplicate test of their own, and my
three overlapping tests were deleted.

### What I could not prove

I could not reproduce the `BUILDING`-and-queryable window on demand. Dropping and
recreating the index on an empty collection went `PENDING/False` straight to
`READY/True`, eleven times out of eleven. The evidence is the direct observation above
on first creation against a fresh database, plus `queryable` being authoritative
regardless. Caroline hit the same state from the other direction — a definition update,
where Atlas keeps serving the old index while the new one builds — which is both easier
to trigger and more likely in practice.

One thing that looked like a second bug was not. Mid-poll, the raw listing said
`PENDING/queryable=False` while `search_status()` said `ok` in the same iteration. That
was my probe making two `list_search_indexes()` calls milliseconds apart with the index
flipping between them — a race in the probe, not in the code. Recorded because it read
exactly like a contradiction in the classifier.

417 offline, 9 docker, 11 live, ruff clean, frontend lint and build clean.


## Will's chess input import

- Confirmed `dev1/gemini-agent` was pushed, updated main to `2420946`, and created
  `dev1/chess-demo-input`. The agent implementation is not yet merged into main.
- Added 15 unchanged supplied files under `demo/chess/`, with SHA-256 provenance
  manifest and integration notes in `docs/chess-demo.md`.
- Found an input-version mismatch: supplied directory and ZIP are Python >=3.10,
  not Python 2.7. Await the genuine Python 2.7 revision for the intended 3.14 demo.
- Validation: all 11 supplied tests passed on Python 3.12; all source parsed on
  Python 3.14; Python 3.14 terminal session played two moves and quit successfully.
- No executable registration or target enablement: Python 2 parsing, dual runtimes,
  fixture-specific oracle routing and chess behaviour cases remain prerequisites.


## Refreshed Python 2.7-compatible chess input

- Replaced the chess copy with the user's updated local directory, byte-for-byte:
  16 files, new legacy_enum.py/setup.py, removed obsolete pyproject.toml.
- All eight unittest methods pass on real Python 2.7.18 and Python 3.14.0.
  Terminal transcripts match across both for help, illegal move, undo and checkmate.
- The source already runs on both interpreters. Describe a model demo as legacy
  modernisation, not fixing Python-2-only syntax. setup.py still restricts installation
  to Python <3; packaging needs migration too. Original local ZIP remains stale.
- Updated manifest and integration notes. Sandbox dual-runtime selection, chess cases
  and fixture registration still need implementation; no sandbox verdict claimed.


## Chess ZIP inspection in the UI

- Added ZIP upload, real dependency graph with module/source inspection, and a
  declared Python 2.7 -> desired Python 3.14 display to the frontend.
- Added static runtime-metadata inspection endpoint; setup.py is never imported.
  The target action remains gated by executable status and API target support.
- Added a clean chess ZIP generator; source-folder ZIP is stale and must not be used.
- Validation: frontend production build and lint passed; 427 offline backend tests
  passed, including runtime metadata and endpoint checks. Ruff passed. Live Atlas upload/browser
  interaction not validated here; local MongoDB URI currently has an invalid scheme.
- Remaining: trusted chess upload recognition, fixture-specific behaviour cases,
  dual-runtime sandbox verification and Python 2 target enablement. UI does not claim
  a migration occurred before those prerequisites exist.
---

## Isolating the live search test, and the bug it exposed

Dev 3's TODO list assigned this one to me and was right to: `test_search_live.py`
imported the fixture into whatever database `.env` names and cleaned up nothing, which
is how eleven `legacy_orders` repositories ended up on the shared cluster in an
afternoon.

It now runs in a throwaway `axolotl_test_<random>` database, dropped after asserting
the prefix, and deletes its snapshot from disk. The snapshot needs its read-only bit
cleared first, since import deliberately makes the tree read-only.

Proven by counting rather than asserted:

| | before | after |
|---|---|---|
| repositories in the real database | 11 | 11 |
| chunks | 400 | 400 |
| snapshot directories on disk | 13 | 13 |
| leftover `axolotl_test_*` databases | none | none |

### The bug it exposed

A fresh database has no `code_chunks` collection, and **Atlas refuses to create a
search index on a collection that does not exist**:

```
could not create code_chunks_text: Collection '<db>.code_chunks' does not exist.
NamespaceNotFound (code 26)
```

That failure was only a log line, so `ensure_search_indexes()` returned
`unavailable` and nothing retried until the next restart. On a brand-new database,
search would have been dead until someone restarted the app after a first import.

It had been working for one reason only: `persistence.indexes.ensure_indexes()`
happens to run earlier in the lifespan and creates all twelve collections, so by the
time search indexes are created `code_chunks` exists. An ordering dependency nothing
stated and nothing enforced, one reorder away from silently killing search.
`ensure_search_indexes()` now creates the collection itself if absent.

**This also corrects something I claimed earlier today.** The "zero manual index work
on a fresh `axolotl` database" check in this log was right by accident: the probe
called `ensure_indexes()` first, exactly as the app does, so it never hit the empty
case. Verified properly this time against a database with no collections at all -
before the fix, both indexes stayed `unavailable` through six polls; after it, both
report `building` immediately and reach `ok`.

### One more thing worth knowing

`WORKSPACE_ROOT=./.ghostspec_workspaces` is relative, so snapshots land relative to
the **working directory of the process**, not the repository root. Running pytest from
`backend/` puts them in `backend/.ghostspec_workspaces`; running uvicorn from the repo
root would put them somewhere else. Not changed here - it is Dev 4's setting - but it
cost ten minutes of looking in the wrong directory.

Also fixed: the index wait in the test cached only success, so a slow cluster made
every one of the eleven tests pay the full timeout. Eleven tests at three minutes each
is over half an hour of waiting to be told the same thing once.

421 offline, 9 docker, 11 live in 47s, ruff clean.

---

## Dev 3's review asks: the 500s, and the README

Caroline reviewed the search layer, is building on it, and sent a list. Two items were
mine to do now.

### `/plans` and `/runs` were bare 500s with Atlas down

She was right, and it was worse than one endpoint. With a URI set and the cluster
unreachable, **all eight** orchestration endpoints returned `500 INTERNAL_ERROR`:

```
500  INTERNAL_ERROR   GET  /plans/{id}
500  INTERNAL_ERROR   GET  /runs/{id}
500  INTERNAL_ERROR   GET  /runs/{id}/events
500  INTERNAL_ERROR   GET  /runs/{id}/report
500  INTERNAL_ERROR   GET  /runs/{id}/artefacts
500  INTERNAL_ERROR   GET  /runs/{id}/diff
500  INTERNAL_ERROR   GET  /runs/{id}/units/{unit}
500  INTERNAL_ERROR   POST /runs/{id}/cancel
503  DATABASE_UNAVAILABLE   GET  /repositories     <- hers, already correct
```

Fixed with her `route_class=DatabaseAwareRoute` on the plans/runs router, which is
exactly the one line she suggested. All eight now answer 503 `DATABASE_UNAVAILABLE`.

**Not the app-level handler in `errors.py` she offered as an alternative**, and the
reason is worth recording: `/system/status` must answer **200** while *reporting*
mongodb as unavailable. A global handler would turn the status report itself into a
503, so the status chips would go blank precisely when they are the thing you want to
look at. Her own `test_status_says_unavailable_rather_than_guessing` would have caught
it. Per-router is the right granularity.

Her `test_atlas_disabled.py` gained a `DEV2_ENDPOINTS` list and a parallel test rather
than a new file of mine: 8 endpoints × her 3 scenarios = 24 more cases, 64 in that
file now.

Worth noting how this hid: `TestClient` re-raises unhandled server exceptions by
default, so the first reproduction attempt showed a driver traceback instead of a
status code. `raise_server_exceptions=False` is what shows what a real client would
get. A test written the default way asserts on an exception, not on the 500 the user
sees.

### README

Dev 3's paste-ready MongoDB section from `docs/mongodb-bounty.md` is now the README's
"MongoDB Atlas: the system of record", with links to the bounty write-up and
`mongodb-atlas.md`.

The status table was well out of date, so it was not just a matter of ticking the Dev 3
row: import/AST/graph, Atlas collections and indexes, hybrid search and Dev 4's
verifier had all landed and were still showing ⬜. Dev 1's agent adapter is now the only
⬜, marked as the one gap left in the demo path. The stale "155 tests" count is replaced
by a line about the three suites, which does not go out of date on every merge.

494 offline, 9 docker, ruff clean, frontend lint and build clean.

---

## `.env` to `axolotl`, and three things that only showed by running it

Switched `MONGODB_DATABASE` to `axolotl` so Dev 3 can drop `ghostspec`, then did her
"run the index and recheck T-07 there" ask. The switch was one line; the ask turned up
three real problems.

### 1. `POST /index` on a repository imported elsewhere returned 200 and did nothing

Dev 3 imported `legacy_orders` into `axolotl` on **her** machine and asked me to index
it, since I have the Gemini key. The result:

```
chunks=0 described=0 embedded=0
warning: no indexable symbols, so nothing was chunked
```

Wrong twice over: all forty symbols were present and the repository was healthy. The
real cause is that **the snapshot is on local disk, not in Atlas**. Re-indexing reads
sources from `WORKSPACE_ROOT/snapshots/<repo_id>`, which exists only on the machine
that did the import. Every source read failed, `build_chunks` saw no sources, and the
endpoint reported success with nothing done.

Now a 409 `SNAPSHOT_UNAVAILABLE` that says to re-import on this machine. Verified live:
the call on her repository returns exactly that.

**This is a constraint worth the team knowing, not just a bug.** Whoever demos must be
whoever imported, or re-import first. "Let the person with the key run the index" does
not work across machines.

The data survived only by luck: `persist_chunks` deletes by `repository_id` before
inserting, so reaching it with an empty list would have replaced forty working chunks
with nothing. The early return that stopped it was written for "no symbols", not for
"no sources". There is now a test pinning it in place for the right reason.

### 2. Dev 3's `source_chunk_id` write shared my try/except

Her `set_source_chunk_ids` call landed inside the block guarding `persist_chunks`, so a
failure in the second write reported `chunks: 0` and "chunks were not persisted" when
the chunks had in fact been stored - sending the caller to re-run a successful index.
Two writes, two reports now: search works in that state, only the back-reference is
stale, and the warning says so.

A merge interaction neither of us would have seen alone: her code, my function, and the
seam between them is where it broke.

### 3. The free-tier embedding quota is 1000 requests a day, and it is gone

```
Quota exceeded for metric: embed_content_free_tier_requests, limit: 1000,
model: gemini-embedding-1.0.  Please retry in 12h14m51s
```

Forty embeddings per import, plus one per query, adds up faster than expected. Four
live tests failed on it - and **the code behaved correctly throughout**: hybrid
degraded to keyword and reported `mode_used: "keyword"`, vector mode returned 503.
Those tests now **skip** with the reason instead of failing, because "I could not run
this" and "I ran it and it was wrong" must not look the same. That is the same rule the
search endpoints already follow for an index that is still building.

Result: 5 passed, 6 skipped, no false failures.

### T-07 on `axolotl`: verified, at 18:33, before the quota went

Imported the fixture on this machine into `axolotl` (`repo_c5c0df8605bf`, 40 chunks, 40
embedded, 0 described - descriptions were already out of quota) and ran the checks
through the API:

| query | mode | rank 1 |
|---|---|---|
| `finalise_amount` | keyword | `pricing.finalise_amount` 47.3 (next 4.5) |
| "quantity must not be negative" | keyword | `pricing.line_gross` 53.4 (Dev 3's phrase boost) |
| "where does the final customer bill get rounded?" | **hybrid** | **`pricing.finalise_amount`**, `k=4 v=1` |
| same query | keyword only | `storage.get_order` ❌ |

The last two rows together are the best evidence for hybrid yet, and better than the
"keyword ranks it fifth" example recorded earlier: on the T-07 query **keyword alone is
actively wrong**, because "get" in the question matches `get_order` and `get_refund`
through the identifier analyser. Vector puts the rounding function first, and fusion
keeps it there.

That is a known characteristic of the identifier analyser, not something to fix:
splitting `get_order` into `get` and `order` is exactly what makes T-06 work. Hybrid is
the default, and keyword mode is for when you know the name.

### Also

Dev 3's repository on `axolotl` now shows 40 embedded, having had 0 at 16:57. Nothing I
ran wrote to it - my index call on it returned the 409 above - so she very likely did it
herself in the interim. Noting the uncertainty rather than claiming the work.

Both repositories share chunk ids, as designed: they are derived from the snapshot hash
and symbol id, and the same fixture gives the same ids. 80 documents, 40 distinct chunk
ids, 2 repositories, unique on `(repository_id, chunk_id)`. No overlap, nothing
corrupted.

`.gitignore` now covers `.env.*` with `!.env.example`, because a hand-made `.env`
backup was sitting untracked where `git add -A` would have swept up a live API key and
connection string.

500 offline, 9 docker, 5 live passed with 6 honestly skipped, ruff and frontend clean.


---

## A note left rather than a run

Dev 3 landed `model_replay.py` while I was finishing: real Gemini answers, recorded on
first ask and replayed after, keyed by model, dimensions, task type and text. It is the
right fix for the quota problem that bit me twice today.

It cannot help yet, though, and the reason is worth writing down rather than
discovering twice: **it records on success**, and the free-tier embedding quota was
gone before any call succeeded. The cache is empty - zero recorded answers under
`.pytest_cache/axolotl/gemini` - so every run still makes real calls and the six
embedding-dependent live tests skip. One run with quota available fixes it
permanently; nobody has made that run.

Left as the first item on the TODO list in `docs/mongodb-atlas.md` rather than done
here, with the numbers that make it matter: 1000 `embed_content` requests a day, ~40
per import, ~48 for a cold live run, and the key resets around 07:05 on 24 Oct (the 429
at 18:50 said "retry in 12h14m51s"). The warning that belongs with it: do not let the
first run of the day be the one just before recording the video.

Nothing else outstanding. Working tree clean, no unpushed commits, no open PRs; #8
through #12 are all on `main`, which is green at 500 offline, 9 docker, and 5 live with
6 honestly skipped.

Dev 1 has pushed `dev1/gemini-agent` (prompts, sessions, tools, prompt-injection
tests, ~1200 lines), unmerged and with no PR open. That is the last gap in the demo
path, and it is theirs to land.


## Dev 1 Gemini agent implementation

- Implemented the Interactions adapter, declared/validated local tools, prompts,
  structured planning, bounded unit migration/repair loop and evidence-based narration.
- Exported the three interfaces consumed by Dev 2's ports. No shared contract changes.
- Bound repository/unit identity in code; restricted patch paths and test edits;
  filtered visible cases, preserved controller authority and capped/redacted events.
- Added offline tests for repair, final-attempt verification, budgets, malformed calls,
  rate limits/timeouts, structured planning and prompt injection.
- Validation: 381 offline tests passed, 9 skipped, 25 live tests deselected; Ruff passed.
  Two live Gemini tests passed using gemini-4-argon: declared-tool/result handshake and
  a structured plan for explicitly synthetic test input. No migration verdict is claimed.
- Remaining integration: local Docker Desktop Linux engine is unavailable, so a real
  model-patch-to-verifier run has not been validated here. Dev 3 search is not exported
  yet; that tool returns unavailable. The chess fixture/Python 2 baseline still needs
  integration by its owner. See docs/argon-agent.md for commands and submission text.


## Dev 1 agent integrated with current main

- Merged dev1/gemini-agent, keeping both build-log histories.
- Adapted the agent search tool to Dev 3's response envelope and added a protected
  path filtering regression test. Search mode and native-hybrid metadata are retained.
- Validation: 544 offline tests passed, 9 skipped, 56 live tests excluded; Ruff and
  frontend build/lint passed. Both real Gemini checks (tool handshake and structured planning) passed; full sandbox migration
  still requires the runner and remains a separate integration milestone.
- GitHub settings checked: merge commits and squash merges are already enabled for
  everyone with merge permission. Rebase is also enabled. Current Git credentials
  have push permission but not admin, so no repository settings were changed.

---

## Dev 5 builds the app shell and screens 01–04

`frontend/` now follows `docs/design/`: shadcn/ui on Base UI with Tailwind v4, Phosphor
icons and IBM Plex. The system status chips sit in the app bar on every screen.

- 01 New migration: import the demo fixture or upload a ZIP.
- 02 Overview: modules table, snapshot counts, declared Python and warnings.
- 03 Graph: React Flow with a dagre layout. Selecting a module lights its dependants, and `/` filters.
- 04 Module inspector: symbols, imports, dependants and read-only source in Monaco.

Dev 1's `/explorer` is folded into these screens and deleted along with `App.css`. The
runtime endpoint's 500 and what blocks a chess 2.7 → 3.14 run are in
[docs/backend-issues.md](docs/backend-issues.md).

Plan, Run, Report and Search are placeholders until their steps land. Lint, build and five
vitest tests pass. Monaco is a 2.7 MB chunk loaded only when the inspector expands, so the
build prints a chunk-size warning.

---

## Project moved to a fresh repository

- The project now lives in `Prittstick22/axolotl`, starting from a single commit. The
  earlier history (74 commits, pull requests #1–#13) stays in the original repository;
  [docs/IMPLEMENTATION.md](docs/IMPLEMENTATION.md) summarises what was built and by
  whom.
- The new repository allows squash merges only and deletes head branches on merge.

---

## Dev 1: the API accepts a folder upload

The demo will upload the chess project as its folder, not as a ZIP. This is the
backend half; Dev 5 is changing the upload screen.

- **API.** `POST /repositories/import` also accepts multipart `files` with one `paths`
  entry each, giving the file's path inside the folder. ZIP and fixture imports are
  unchanged.
- **Same checks as a ZIP.** `safety.plan_folder` runs every path through
  `safe_member_path` (traversal, absolute and drive-letter paths, depth) and applies
  the 50 MB, 500-file and ignore rules. A duplicate path is refused, case-insensitively,
  because on Windows the second file would overwrite the first. A bad path rejects the
  whole upload and nothing is written.
- **Still static only.** A folder upload is `origin="folder_upload"`,
  `executable=False`. **SHARED_CONTRACTS §11, additive:** `folder_upload` is a new value
  of `RepositoryRecord.origin`, in §4, `contracts.py` and `contracts.ts`. Needs acks.
- **For Dev 5.** Send each file as `files` and its path inside the folder (drop the
  picked folder's own name) as `paths`, in the same order; pass the folder name as
  `name`. Add `'folder_upload'` to `origin` in `contracts.ts` and label it on Overview.
  Skipping hidden files and `__pycache__` client-side keeps `.env` off the wire.
  `demo/package_chess.py` and the ZIP flow stay until the screen changes.

### A bug found on the way: the project name on every upload was discarded

The route shared one `Form(None)` default between `demo_fixture_id` and `name`.
FastAPI bound both to the first parameter's field, so the name typed on the upload
screen never arrived and the API fell back to the file name. Shown on main: a ZIP sent
with `name=my-project` was saved as `chess`. Each parameter now has its own default,
and `test_a_zip_upload_keeps_its_project_name` pins it.

560 offline backend tests (16 new), ruff clean. No frontend changes.

---

## Dev 1: the runtime endpoint no longer fails on other machines

Item 1 in [docs/backend-issues.md](docs/backend-issues.md), from Dev 5.

- **The bug.** Snapshots stay on the machine that imported them
  (`WORKSPACE_ROOT/snapshots/{repo_id}`), but the repository record is in the shared Atlas
  database. On any other machine, `inspect_runtime` called `root.iterdir()` on a missing
  directory, and `GET /repositories/{id}/runtime` answered 500. The Overview showed
  "Couldn't read the package metadata: Unexpected FileNotFoundError".
- **The fix.** `inspect_runtime` returns the normal shape when the snapshot is missing:
  `declared_python: null`, no evidence, `runtime_verified: false`, and a note that the
  source isn't on this machine. The guard is in `inspect_runtime`, not the route, so every
  caller gets it. `/symbols/{id}` already handles the same case with a warning.
- **No contract change.** The response shape is unchanged; the Overview already shows
  `null` as "Not declared" with the note under it.

562 offline backend tests (2 new), ruff clean. No frontend changes.

---

## Project manager: chess is the MVP demo; tickets on GitHub

Decisions of 24 Oct, recorded across the docs with a dated note:

- **Chess, Python 2.7 → 3.14, is the MVP demo.** It can't be migrated yet; that is the bug
  [#30](https://github.com/Prittstick22/axolotl/issues/30), with one sub-ticket per piece
  (folder upload screen, import time, case routing, dual-runtime sandbox, chess cases,
  `python2_to_3` target). The Spec listed Python 2 → 3 as a non-goal; the decision overrides it.
- **`legacy_orders` is the backup demo.** Kept, and must keep working.
- **Dev 4 (the verifier) owns the demo script, the three videos and the presentation**, moved
  from Dev 5 (#14, #15, #31).

All open work is now GitHub issues #2–#43, labelled `MVP`, `feature`, `bug`, `stretch` or `spike` and `dev 1` to
`dev 5`, unassigned. [docs/PROJECT_STATUS.md](docs/PROJECT_STATUS.md) has the status, the
tickets by developer and the product feature list. Two bugs found during the review: every
import gets the `legacy_orders` held-out cases (#29), and an import waits for search indexing
(#28, from Dev 5).

## Dev 5 for Dev 4: units with no cases can pass, and the diff has a baseline

Items 4 and 11 in [docs/backend-issues.md](docs/backend-issues.md) (#2 and #44), from Dev 5.

- **Item 4, units with no cases.** `_comparison_gate` marked the differential gate as required
  even with no cases, so its `skipped` status failed every attempt: `storage.py` passed every
  other gate in `run_bb0da2e79534` and was still blocked, which stopped the run at 2 of 6 units.
  The gate is now required only when it has comparisons, and its summary says "no behaviour
  cases for this unit". Such a unit is checked by the compile, existing-test and sandbox-policy
  gates, as `plans.py` already tells the user. A mismatching case still fails the unit.
- **Item 11, the diff baseline.** `diff()` read `rev_baseline` from
  `WORKSPACE_ROOT/runs/{run_id}/rev_baseline/`, which is never created, so every file read as
  added. It now reads the repository snapshot, the tree the first workspace is copied from. The
  fix is in `diff()`, so the diff endpoint, the report's `changed_files` and the patch artefact
  all get it. Checked offline on `run_bb0da2e79534`'s trees: `pricing.py` diffs as
  `@@ -8,13 +8,17 @@` instead of `@@ -0,0 +1,113 @@`, and the final tree changes 2 files, not 9.
- **The recorded run's report is unchanged.** Its `changed_files` and `migration.patch` were
  written at report time from the old diff, so they still list 9 files. A new run gets the
  right ones. The diff endpoint is computed per request, so it is right for the old run too.

- **Live, on Atlas Local.** In `run_e5380c4d180c` (same plan as `run_bb0da2e79534`),
  `storage.py` passed on its first attempt: compile, impacted tests (9 of its dependants' cases
  agree), existing tests (12 passed) and sandbox policy passed, and the differential was skipped and
  not required. `orders.py` passed after it, so the run reached 4 of 6 units instead of 2. The
  report of `run_60c18c27a651` lists `customers.py` and `pricing.py` as changed, and its
  `migration.patch` covers the same 2 files. `refunds.py` was then lost to a failed Argon call,
  logged as backend issue 14.

565 offline backend tests (3 new), 9 container tests, ruff clean. No frontend changes: the Run
screen already shows a non-required skipped gate as "Differential skipped".

---

## Kickoff rule removed

The event staff dropped the rule against writing code before Fri 23 Oct 17:00, and it was
causing problems, so it is gone from every planning doc: the five dev briefs, `SHARED_CONTRACTS`
§0, `TEAM_SPLIT`, `TEAM_TOOLING_PREP`, `APP_SCAFFOLD_SETUP`, `PRE_KICKOFF_PREP` and the Spec.
`SHARED_CONTRACTS` §0.1 is kept as a "removed" line so the §0.2–§0.8 references in the code
still point at the right rules. Mentions of kickoff as a time ("confirm quotas at kickoff") stay.

Also in [docs/PROJECT_STATUS.md](docs/PROJECT_STATUS.md): the demo script, videos and presentation
(#14, #15, #31) now come last, #2 and #44 are fixed on main pending Dev 4's review, and backend
issue 14 is #47.

---

## Item 12, fixed twice: the delta is the harness logs

Dev 5 fixed backend item 12 (#45) on a branch while Dev 2 landed the same fix on main as
`e4fad35` (PR #48). Both read `list_verifications` and redact sealed held-out comparisons by the
counterexamples' rule, so as with `queryable` earlier, the branch was cut down to what `e4fad35`
doesn't cover and merged on top of it as `23bffdc`.

The gap: the differential and impacted-tests gates store the harness's raw output in
`log_excerpt`, one JSON outcome per case with held-out ones included. `e4fad35`'s test fixtures
have `"gates": []`, so the logs were never exercised. On Atlas Local, with `e4fad35` running,
`unit_pricing` in `run_e5380c4d180c` returned all 10 held-out comparisons redacted, and
`hcase_001`'s and `hcase_008`'s expected values in those two logs of the same response.

The two logs are now withheld whole; the comparisons carry the same results, redacted case by
case. Filtering line by line would trust a 4 KB truncation not to cut an outcome away from its case
id. Live after the fix, no held-out outcome appears anywhere in the response. 599 offline backend
tests (1 new, beside Dev 2's in `test_api_without_database.py`), ruff clean.

Also: `docs/backend-issues.md` is gone (`60c3235`); GitHub issues are the source of truth for team
issues from now on. Item 1, already fixed in `edd19ea`, became #51 and is closed.

---

## Chess is the only demo

Decided 24 Oct: the team is building Axolotl for the chess demo only (Python 2.7 → 3.14), with no
backup demo. Every doc that called `legacy_orders` the backup now says so. The `legacy_orders`
fixture is **not deleted yet**: it is the only project the sandbox can run today, and 14 backend
test files plus the CI sandbox job use it. It is now described as the test fixture, and
[#54](https://github.com/Prittstick22/axolotl/issues/54) deletes it once chess runs end to end and
the tests move to chess.

---

## Dev 4 review: issue #2 verification safeguards (24 Oct 2026)

Reviewed the existing fix from `45fe5a5` on top of main at `4676b3b`. An empty
comparison gate stays skipped and optional; no further change to the pass rule was
needed. Clarified its comment: held-out cases, impacted cases, compile, existing
tests and sandbox policy still apply.

Expanded `backend/tests/unit/test_verification_gates.py` from 2 to 15 test cases.
The case-loader test double now honours unit IDs and held-out visibility, so tests
detect a verifier that accidentally excludes hidden cases or a dependant's cases.
Coverage includes hidden-only matches and mismatches, dependent-unit matches and
mismatches when the current unit has no cases, missing baseline/candidate outcomes,
compile failures, existing-test failures, timeouts, kills, output limits, Docker
errors and an unavailable runner.

Validation on this machine: 15 focused tests passed; the complete offline backend
suite passed (658 passed, 65 deselected); backend ruff and `git diff --check` passed.
The only pytest warning was the existing Starlette/httpx deprecation warning.

The 9 real-container tests were attempted but skipped because the sandbox runner
is unavailable here. This machine has no configured `.env`, so no new live migration
was performed. The prior live evidence remains `run_e5380c4d180c`, recorded above:
`storage.py` passed on its first attempt and `orders.py` passed afterwards. The
issue author's [review request](https://github.com/Prittstick22/axolotl/issues/2#issuecomment-6383693535)
also confirms the fix was checked on live runs. The PR's sandbox CI job builds the
runner and exercises the real container, including the wrong-rounding regression.

## Dev 4 review: issue #44 baseline diff and reports (24 Oct 2026)

Reviewed the snapshot-based baseline fix from `45fe5a5`. Added two report integration
scenarios using real workspace creation, patch application, acceptance, diffing and
artefact generation. Only persistence and the optional model narrative are faked.
An unchanged accepted tree produces an empty diff and patch. Two successive accepted
changes produce a cumulative patch against the original snapshot, with the correct
old content and only the two changed files in both report and patch metadata.
Unchanged README/test files are excluded, the baseline remains intact, and the
downloaded patch passes the existing revision/hash check.

Validation: all 57 workspace, report and artefact tests passed; changed-file ruff
and `git diff --check` passed. No Docker or external service is needed for these
filesystem/report checks. The existing production fix required no further change.

---

## Dev 1: one sandbox image per interpreter (#22)

The chess demo migrates Python 2.7 to 3.14, but the sandbox only had Python 3.12. Now
`python2_to_3` records the baseline on Python 2.7 and runs every candidate gate on Python
3.14; `modern_python3` keeps both sides on the original 3.12 image, unchanged.

- **Images.** `runner/Dockerfile` takes `PYTHON_VERSION` and `PYTEST_VERSION` build args and
  builds `axolotl-runner:latest` (3.12, the default, byte-for-byte the old build),
  `axolotl-runner:py2.7` (pytest 4.6.11, the last for 2.7) and `axolotl-runner:py3.14`
  (pytest 8.4.2). Same security profile on all three. CI builds all three.
- **Which runs where.** `app/sandbox/runtimes.py` maps a target to a baseline and a candidate
  runtime; verification reads the run's plan to pick them. An unknown target raises instead
  of quietly verifying on 3.12, and a missing 2.7 or 3.14 image makes the gates
  `inconclusive` with the exact build command, never a fallback.
- **Python 2 harness.** `run_cases.py` now runs unchanged on 2.7 and 3: text, `long`,
  old-style classes, `fromisoformat` and an encoding declaration were the differences.
  On chess, the same five cases (a tuple, `None`, text, an object built on its legacy enum,
  and a `TypeError`) encode to byte-identical output on 2.7.18, 3.14.9 and 3.12.15.
- **Compile gate on 2.7.** `compileall` writes each `.pyc` next to its source on 2.7 (no
  `PYTHONPYCACHEPREFIX`) and failed on the read-only `/code`. `harness/compile_tree.py`
  compiles in memory instead, without leaking its own `print_function` into the code
  under test, so a Python 2 `print` statement passes on 2.7 and fails on 3.14.
- **Recorded, not assumed.** `BaselineSummary.environment["python"]` was the constant
  `"3.12"`. Baselines and verifications now record the interpreter version probed from
  inside the container, plus the image tag and digest. `VerificationResult.environment`
  is new, additive and optional (SHARED_CONTRACTS §11); `contracts.ts` needs the mirror
  (Dev 5).

Chess's own 8 tests pass on 2.7 and 3.14. Not yet proven: a whole `python2_to_3` run,
which also needs the target (#21) and chess cases (#23).

714 offline backend tests (15 new), 22 container tests (13 new), ruff clean. No frontend
changes.

## 24 Oct 2026 — Dev 4: chess presentation and recording kit (#70, #14, #15)

Prepared the 183-word introduction (estimated 1:18 at 140 words/minute), judge Q&A,
three-minute click-by-click demo, preflight and clearly labelled fallback. Added
three submission shot lists and an unfilled evidence/rehearsal/upload log. The
newer chess-only decision takes precedence over the old orders fallback. The
MongoDB script uses real API search responses because Graph's module filter is
not Atlas search. README links the kit and reflects #22's merged runtimes.

Checked commands, UI labels, relative links and current code claims. No actual
spoken timing, human rehearsal, Dev 1/2 sign-off, video or upload is claimed. Those
remain outstanding: this checkout has no working demo services, Docker or supplied
demo URL/upload destination, and the chess target is still disabled pending
integration. These documentation changes do not close the three demo tickets.

## 24 Oct 2026 — Dev 4: measured sandbox runtime reports (#46)

Added five CPU-time samples after one warm-up for successful, reproducible cases
inside the Linux sandbox, including Python 2.7/3.14. Fresh inputs and fixture resets
sit outside the measured interval. Benchmark failures discard timings without
changing the original functional outcome. Samples persist alongside the median.

Reports aggregate matching paired cases from the successful final verification
of the final accepted revision. The frontend shows a runtime panel only when
measurements exist, explicitly limited to tested cases. Missing/old data remains
null. Spread is a descriptive heuristic, not proof of a speed improvement.
See `docs/runtime-timings.md` for method, costs and limits. Added 22 offline tests
and extended the existing real-container cross-interpreter check.


---

## Dev 1: Gemini rate limits are waited out, not lost (#47)

Two live runs each lost a unit to a failed Argon call: a 429 (`run_60c18c27a651`) and a
dropped connection (`run_e5380c4d180c`). Dev 2's half (#49) made those endings honest in
the journal; this half stops them happening.

- **Two retry layers became one.** The `google-genai` SDK retried a 429 three times on its
  own, reading only a `Retry-After` header, never the body's `retryDelay`. Our loop then
  added 1, 2 and 4 s. On a daily quota the SDK's sleeps outlasted our 120 s timeout, so the
  real error was replaced by a `TimeoutError`; that was the 500 on creating a plan on 24 Oct.
  The SDK's retries are now off (`HttpOptions` can't do it: it raises `attempts=0` to one
  retry, so `retry_config` is cleared on the interactions client), and checked against the
  real SDK on a mock transport: 2 requests where it used to send 8.
- **A 429 waits for what it asks for.** The body's `RetryInfo.retryDelay`, else `Retry-After`,
  plus a second; with neither, 2, 4, 8, 16, 32 s. Up to 90 s in all, which covers a
  per-minute quota.
- **A daily quota fails at once and says how long:** "the API asks to wait 17h 56m".
- **A dropped connection is retried once.** A timeout is not.
- **Creating a plan** when the model fails answers `503 MODEL_UNAVAILABLE` with that reason,
  instead of a bare 500 or the `NO_AGENT` fallback, which would wrongly say the agent is not
  wired up.

732 offline backend tests (8 new), 22 container tests, ruff clean. No live Gemini calls.

## 24 Oct 2026 — Dev 4: spoken practice and automated demo readiness

Updated the presentation/demo kit for merged #23 and target enablement PR #76.
Added `demo/check_readiness.py`: a GET-only checker for services, trusted chess,
indexing, target availability, and consistent completed run/plan/report/patch
metadata. It returns nonzero for missing or failed checks, prints no server error
bodies and leaves runtime-image inspection and human rehearsals explicit.

Added `demo/make_practice_audio.ps1` and generated a synthetic Hazel Desktop practice
track, measured at 84.63 seconds from WAV frames. This is not Will's voice or a
human rehearsal. The checker correctly failed on the local machine because the
API is not running and no demo repository/run IDs were supplied. Local evidence
and audio are under the ignored `.axolotl_workspaces/demo-preparation/` directory.
Real recordings, human rehearsals, factual sign-off and uploads remain pending.

## 24 Oct 2026 — Dev 1: chess is a built-in fixture that runs from any laptop (#81–#84)

Records are shared through the team's Atlas cluster, but each import got a random id and
a snapshot on one laptop only. The cluster had three `legacy_orders` entries and a chess
upload that ran only on Caroline's machine, and on any other laptop creating a plan
spent an Argon call on a run that then failed copying the baseline.

- **Chess is a built-in fixture, listed first** (#81), so New Migration preselects it.
- **One id per fixture content** (#82): `repo_` + 12 hex from the fixture id and its
  normalised tree hash. Importing it again returns the existing repository: no analysis,
  indexing or model calls. Chess in this checkout is `repo_3852f5968630`.
- **A missing fixture snapshot is rebuilt from the checkout** (#83), inside
  `snapshot_path`, so its six callers need no change. Built beside the destination and
  renamed into place, written as LF, re-hashed before use. A changed `demo/chess` gives a
  different id, so it is never rebuilt under an old import.
- **No Argon call for code that isn't here** (#84): creating a plan and starting a run
  answer `409 SNAPSHOT_UNAVAILABLE` first.

836 offline backend tests (10 new; a mutation that drops the rebuild fails the rebuild
test), ruff clean.

## 24 Oct 2026 — Dev 2: the export is the project, not the patch (#40)

People clicked a download labelled "Export migrated project" and got `migration.patch`.
Two separate causes, both fixed here.

- **The zip shipped the diff.** `write_export` looked up the recorded `patch` artefact
  and copied it in as `axolotl/migration.patch`. It no longer does: the zip is the
  project files, `axolotl/MIGRATION.md` and `axolotl/report.json`. The patch is
  unchanged as its own artefact, still at `GET /artefacts/{id}/download`.
- **MIGRATION.md promised it.** `migration_notes` took `with_patch` and, when true,
  closed with "and `migration.patch` the diff from the imported code" — a file that is
  now not in the zip. Nothing else passed the parameter, so it is gone rather than
  threaded through as `False`.
- **The Downloads panel offered four buttons**, three of them raw artefacts, so the
  wrong one was easy to click. It now shows one: "Download migrated codebase (.zip)".
  With nothing accepted it says so in words instead of rendering a button that 404s.
  `listArtefacts` is untouched in the API client; this screen just stops calling it.

The regression test keeps a recorded patch artefact in its setup on purpose, because a
patch *existing* is what used to put it in the zip. Re-adding the file to `write_export`
fails it with "the export is the project; the diff is its own artefact" rather than two
truncated lists, which is why the named check runs before the namelist comparison.

833 offline backend tests, ruff clean. Frontend: 25 tests, lint and build clean.
Verified both panel states against a throwaway local API; no run was executed.

## 24 Oct 2026 — Dev 2: a grounded Ask endpoint over our own hybrid search (#123)

`POST /repositories/{id}/ask`. One question about one imported repository, answered
from that repository's code.

It needed no retrieval pipeline. Five of the migration agent's ten declared tools are
read-only *and* need nothing but a repository id — no run, no unit, no controller — so
the endpoint is the existing agent loop with the controller removed and those five
tools offered. `ReadOnlyDispatcher` subclasses `ToolDispatcher` rather than changing
it, so Dev 1's lane is untouched.

Two tools are excluded although they only read. `get_counterexamples` is derived from
held-out failures, which have leaked through four separate read paths already (#45,
#56, #57, #94); a chat would be a fifth, so it never gets near them.
`get_behaviour_cases` needs `ctx.unit.unit_id`, and a question has no unit.

Three decisions worth recording:

- **`grounded` is false when retrieval found nothing**, and the answer says so rather
  than reaching for general knowledge. That is the verifier's rule applied to prose: an
  inconclusive result is never reported as a pass.
- **Citations are read off the tool results**, not asked of the model. A model invited
  to write its own citations can cite a file it never opened.
- **`retrieval` is in the response contract**, not a debug field: tool, query,
  `mode_used`, `native_hybrid`, `hit_count`. It is the only place in the app where the
  hybrid search is visible doing work inside a model loop.

The allowlist is pinned by a test, because every held-out leak so far was a path nobody
had asserted on rather than a rule somebody broke. Forcing `grounded = True` fails
three tests; the allowlist test fails if a write tool is added to the set.

872 offline backend tests (39 new), ruff clean. The model is stubbed throughout, so
none of this has yet run against the real Gemini: the live path is unexercised and the
pane (Dev 5, #123) is not built.

## 24 Oct 2026 — Dev 1: documentation for the legacy and the migrated code (#39)

Per-module docs for both sides of a migration, in the app and in the export.

- **Facts from the parse tree, for free.** `GET /repositories/{id}/docs` and
  `GET /runs/{id}/docs` read both trees on request with `analyse_module` and
  `build_edges`, as #34 does: docstring as written, public classes, methods and functions
  with signatures, what each module depends on and what uses it. No model call.
- **The migrated side says what changed.** The unit's verdict and attempts, Argon's own
  summary of the accepted patch (from `patch_proposed` in the journal, so free), and
  the module's dependency changes from #34's `compare_trees`.
- **One purpose sentence per module, on request.** `POST /repositories/{id}/docs`
  writes the missing ones in the background with `describe_chunks`, unchanged: the same
  untrusted-code prompt, pacing and quota handling as search. It reuses a cached purpose
  or the search index's module description first. Test modules are never sent.
  `GEMINI_DESCRIPTION_MODEL=gemini-3.8-flash`, so no Argon credit is spent.
- **Labelled and fenced.** Model text says who wrote it and "not verified", and is
  HTML-escaped. Docstrings sit in a fence longer than any run of backticks they contain,
  so code can't inject Markdown.
- **In the export.** `GET /runs/{id}/export` adds `axolotl/docs/` (an index, plus legacy
  and migrated pages) from cached purposes only. A docs failure never fails the export.

Live on chess (`run_70e126c3acf2`): both GETs made 0 model calls; generation made 14
Flash calls, all successful, `ready` 14/14; a second POST made 0; the export grew to 50
files, 31 of them docs, still with no `.patch`. 914 offline backend tests (17 new;
mutations that send test modules or fix the fence at three backticks each fail a test),
ruff clean.

Found on the way, not fixed here: `dotted_name_for` turns `local_chess/__main__.py` into
`local_chess..main.`, so its `from .cli import ConsoleUI` resolves to the package
instead of `local_chess/cli.py`.

## 24 Oct 2026 — Dev 1: migration summary (#35)

`GET /runs/{run_id}/summary`: one overview of what a run changed, for the screen in
#129 and as `axolotl/SUMMARY.md` in the export.

- **Every number is already recorded.** Outcome and attempts come from the report.
  Checks per unit are the last `gate_result` per gate (the report builder's own
  `_final_gate_results`, so a repaired unit isn't counted twice). Counterexamples are
  per unit, resolved only where the unit passed. Argon's summary is the last
  `patch_proposed` (the accepted patch for a unit that passed), labelled model-written.
- **Lines added and removed** are counted from the same unified diff the Changes screen
  shows, excluding file headers. **Python before and after, and the dependency changes,**
  come from #34's `compare_trees`.
- **On another laptop** the trees aren't there, so lines, Python and dependencies are
  `null` with a warning; the report and journal parts still answer. It's never a 500.

Live on `run_70e126c3acf2`: 14/14 verified, 14 files, +136/-59 lines, and checks per
unit plus the final gate sum to the report's 288. Python `2.7 -> >=3.10`,
`raw_input -> input`. 0 model calls. The export is now 51 files with `SUMMARY.md`.
963 offline backend tests (10 new; counting every gate result instead of the last per
gate fails one), ruff clean.

## 24 Oct 2026 — Dev 1: a dunder file's dotted name comes from its path (#140)

`dotted_name_for` rebuilt a module's importable name by decoding its id, replacing
`__` with `.`. Ids write `.` as `__`, so a file whose own name contains `__` decoded
wrongly: `local_chess/__main__.py` came back as `local_chess..main.`. Its
`from .cli import ConsoleUI` then resolved to the package `local_chess` instead of
`local_chess/cli.py`, and its symbols were named `local_chess..main..main` in search,
Ask and the docs.

Both functions now share one path-based `_dotted`: `module_id_for` encodes it as
before, and `dotted_name_for` returns it as it is. **Module ids don't move** (pinned for
seven paths), so units, plans and runs are untouched. Only dunder modules' symbol names,
and the symbol, chunk and edge ids derived from them, change. The chess entry on the
cluster is rebuilt under the same id afterwards.

981 offline backend tests (16 new; decoding from the id again fails 5 of them), 25
sandbox tests, ruff clean.

## 25 Oct 2026 — Dev 1: Argon requests time out after 240 s, not 120 s

On `run_a6435773b085` one Argon turn on `local_chess/cli.py`, the largest chess module,
ran past the 120 s request timeout. The unit was blocked and `__main__` was skipped,
while every other unit passed first time. Normal turns take 15-70 s, so the limit was
too tight for a long patch. `REQUEST_TIMEOUT_SECONDS` is now 240. A timeout is still not
retried (#47): the call may have been charged, and a retry would double the wait.
