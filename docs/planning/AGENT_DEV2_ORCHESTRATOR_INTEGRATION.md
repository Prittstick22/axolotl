# Agent Brief — Dev 2: Orchestrator, Scheduler & Integration Lead

You are the implementation agent for **Dev 2** on Axolotl (Encode London 2026). You have three documents:
1. `Axolotl_Product_Requirements_and_Implementation_Handoff.md` — the Spec
2. `SHARED_CONTRACTS.md` — names, shapes, ownership (you are its custodian)
3. This brief

---

## Your mission

Own the deterministic heart of Axolotl: the migration state machine, dependency-first scheduling, gated progression, retry budgets, the append-only event journal, plans/runs API, and the final report. You also keep `main` runnable and wire everyone's pieces together. **You decide what happens next; Argon only suggests; the verifier decides pass/fail.**

## You own

- `backend/app/main.py`, `config.py`, `models/contracts.py`, `pyproject.toml`, `.env.example`
- `backend/app/orchestration/**` (state machine, scheduler, controller, events, worker)
- `backend/app/reports/**`
- `backend/app/api/plans_runs.py`, `targets.py`, `system.py`
- Collections: `migration_plans`, `migration_runs`, `run_events`, `counters`, `artefacts`
- `README.md`, `docs/architecture.md`, `docs/api-contracts.md`
- Tests T-08, T-09 (with Dev 4), T-13, T-14, T-15, T-16, and state-machine unit tests

## You do NOT own

AST/graph building (Dev 3), executing code or comparing outputs (Dev 4), prompts/model calls (Dev 1), UI (Dev 5).

---

## Hour 0 (Fri 17:00–18:00) — skeleton everyone builds on

1. Create the monorepo exactly as SHARED_CONTRACTS §2. Two smoke tests, README stub, `.env.example`.
2. `models/contracts.py` verbatim from SHARED_CONTRACTS §4.
3. FastAPI app with `/api/v1` router mount, CORS restricted to `FRONTEND_ORIGIN`, error envelope, `GET /system/status` (calls Dev 3 `get_db().command("ping")`, Dev 4 `runner_status()`, a cheap Gemini reachability check from Dev 1).
4. Empty routers for each owner so they can fill files without merge conflicts.
5. Push to `main`, tell the team. **Gate Fri 18:00.**

---

## Components

### Scheduler — `orchestration/scheduler.py` (sync, pure, heavily unit-tested)
- Input: `nx.DiGraph` from Dev 3 `load_networkx` (edge A→B = A depends on B), local modules only.
- `nx.strongly_connected_components` → condensation DAG. Multi-module SCC = one unit with `is_cycle=True`.
- `depends_on_unit_ids` from condensation edges; `dependant_unit_ids` reversed.
- `eligible_units`: units whose dependencies are all `passed` and themselves `queued`. If a dependency is `blocked`, dependants become `skipped` with reason — never migrated through a failed prerequisite (T-13).
- `impacted_units`: transitive reverse dependants.
- Risk label per Spec §5.5 with visible factors (`reverse_dependants=3`, `dynamic_edges=1`, `cycle`, `no_visible_cases`).
- Test modules and `main`-style entry points: include as units only if they're in the fixture's migration scope (ask Dev 4); never migrate test files.

### Plans — `create_plan(repo_id, target_id)`
- Reject unsupported targets with 422 `UNSUPPORTED_TARGET` (T-08). `GET /migration-targets` returns `modern_python3` supported + `fastapi`, `async_python`, `python_to_typescript`, `python2_to_3` with `supported=false` and a reason.
- Reject non-executable repos with 409 `REPOSITORY_NOT_EXECUTABLE` (uploads are static-only).
- Build units → call Dev 1 `propose_plan` → validate `suggested_order` is a topological order of the condensation; if not, use deterministic order and record why in `rationale`.
- Approval endpoint records `approved_at`. A run can only start from an approved plan.

### Run worker — `orchestration/worker.py`
- `start_run` writes `migration_runs` + `run_created`, then launches an `asyncio` task (single-process is fine for the demo). Never tie work to the HTTP request lifetime.
- On startup, any run in a non-terminal state is marked `failed` with `error="interrupted by server restart"` (honest, not resumed).
- Cancellation: a flag checked between steps; ongoing sandbox jobs are killed by Dev 4's timeout.

### Run algorithm (Spec §6.6, implemented by you)
```
state baselining: Dev4.capture_baseline(all units) -> event baseline_captured
for unit in eligible order (Argon's order where valid):
    unit -> migrating
    controller = UnitControllerImpl(run, unit, base_revision=last accepted revision)
    outcome = Dev1.run_unit_session(ctx, controller)
    if controller.is_verified(): accept_workspace -> snapshot_accepted, unit passed
    else: unit blocked (reason from last verification / outcome), dependants skipped
    recompute eligibility
state final_verification: Dev4.verify_final(workspace at last accepted revision)  (always run, T-14)
verdict: completed only if every unit passed AND final gates passed;
         completed_with_warnings if some blocked/skipped or final inconclusive;
         failed if the final candidate is unusable (compile fails) or infra error
generate report + artefacts
```

### UnitControllerImpl — `orchestration/controller.py`
Implements the Protocol in SHARED_CONTRACTS §8:
- `submit_patch`: decrement attempts; if none left → reject. `create_workspace` from last **accepted** revision (fresh each attempt, no accumulation), Dev 4 `apply_patch(..., allowed_paths=unit.file_paths)`. Emit `patch_proposed`, then `patch_applied` or `patch_rejected`. On attempt ≥2 emit `repair_started` and set unit `repairing`.
- `run_verification`: unit → `verifying`; Dev 4 `verify_unit(..., impacted=impacted_units)`; persist via Dev 4; emit `verification_started`, one `gate_result` per gate, `counterexample_recorded` per cex, then `verification_passed|failed`. Return a model-safe summary (gate statuses, failing visible case ids, count of failing held-out cases).
- Held-out exposure policy: if only held-out cases fail and the model has had one attempt with no visible failures to work from, call Dev 4 `expose_case` for **one** failing held-out case and include it, labelled exposed. Report counts exposed cases separately.
- `is_verified` = the latest persisted `VerificationResult.all_required_gates_passed` for this unit's latest revision. Nothing else sets it.

### Events — `orchestration/events.py`
`append_event` with atomic `counters` `$inc`, unique `(run_id, sequence)`, timestamps UTC, `redact()` for any env secret values, excerpts ≤2 KB. Every state transition goes through one `transition(run, to)` function that validates against the allowed transitions from Spec §6.2 and raises on illegal ones.

### Reports — `reports/`
- Numbers come from `verification_results`, `counterexamples`, `run_events` — never from model text.
- Patch artefact: unified diff of baseline → final accepted revision (Dev 4 `diff`), stored on disk under `WORKSPACE_ROOT/artefacts`, sha256 recorded in `artefacts`.
- Archive artefact: zip of final accepted tree.
- Coverage caveats always include: "Agreement shown only for N tested cases across M functions; not a proof of equivalence", untested public functions (from Dev 4 case coverage), unresolved/dynamic edges (from Dev 3 warnings), exposed held-out cases.
- Optional `narrative` from Dev 1 `narrate_report`.
- T-16: diff shown == diff of the verified accepted revision (assert by hash).

---

## Tasks with checks

| # | When | Task | Done when |
|---|---|---|---|
| 1 | Fri 17:00–18:00 | Skeleton (above) | **Gate Fri 18:00** |
| 2 | Fri 18:00–21:00 | Scheduler + unit tests incl. cycle fixture (T-05 with Dev 3), events, targets endpoint | Units computed from real fixture graph |
| 3 | Fri 21:00–23:30 | Plans API + Dev 1 `propose_plan`, run worker skeleton, baseline step via Dev 4 | Approved plan → run → baseline events visible from `/events` |
| 4 | Sat 08:00–11:00 | UnitControllerImpl + one-unit loop with real Argon + real verifier | **Gate Sat 11:00** |
| 5 | Sat 11:00–14:00 | Multi-unit progression, blocked/skipped propagation, impacted tests | **Gate Sat 14:00** two units verified |
| 6 | Sat 14:00–17:00 | Final verification, reports, artefacts, restart handling, cancel | **Sat 17:00 cold-start E2E**; run `grep -r _stub` → nothing |
| 7 | Sat 17:00–22:00 | Integration bug fixes, freeze at 22:00 | Three clean cold-start runs |
| 8 | Sun 07:30–10:45 | README (problem/user, architecture, setup, exact model ids, Atlas features, execution limits, fixture provenance, caveats, known failures, credits), submission forms with team | Submitted by 10:45 |

## Integration-lead duties

- Review every PR touching shared files; keep `main` booting.
- Run a cold-start E2E at each gate in SHARED_CONTRACTS §12 and post the result honestly.
- Enforce Spec §13 scope cuts when gates are missed — say "cut" early.
- Confirm with organisers at kickoff: multi-bounty submission eligibility, Gemini quotas, Atlas tier. Record answers in README.

## Definition of done

Spec §16 "Final handoff completion rule" reproducible from a cold start by someone else on the team following only the README.
