# API contracts

Owner: Dev 2. Kept in sync with [`planning/SHARED_CONTRACTS.md`](planning/SHARED_CONTRACTS.md) §6.
If the two disagree, SHARED_CONTRACTS wins and this file is wrong — raise it.

Base URL: `http://localhost:8000/api/v1`
Interactive docs while the backend runs: <http://localhost:8000/docs>

**Status key:** ✅ implemented · 🔲 router exists, no routes yet · ⏳ needs another owner's code

---

## Error envelope

Every error, without exception:

```json
{ "error": { "code": "SNAKE_CASE", "message": "human readable", "details": {} } }
```

FastAPI's own validation failures are rewritten into it too, so a client only ever
parses one shape.

| Code | HTTP | When |
|---|---|---|
| `DATABASE_UNAVAILABLE` | 503 | Atlas unreachable or `MONGODB_URI` unset. **There is no in-memory fallback** (§9). |
| `UNSUPPORTED_TARGET` | 422 | Target unknown, or known with `supported=false` (**T-08**). |
| `REPOSITORY_NOT_EXECUTABLE` | 409 | Plan requested for a ZIP upload; those are static-analysis only. |
| `SNAPSHOT_UNAVAILABLE` | 409 | This machine has no copy of the repository's code: an upload imported on another laptop. Returned by re-indexing, and by creating a plan or starting a run **before any Argon call** (#84). Built-in fixtures never hit it: their copy is rebuilt from the checkout (#83). |
| `DOCS_GENERATION_ACTIVE` | 409 | `POST /repositories/{id}/docs` while a generation is already running for that repository (#39). Its progress is on `GET /repositories/{id}/docs`. |
| `INTEGRATION_UNAVAILABLE` | 503 | A collaborator's function isn't implemented yet. Names the owner in `details`. |
| `MODEL_UNAVAILABLE` | 503 | Argon was called and failed: a spent quota, a rate limit that outlasted the wait, a dropped connection. The message says which (#47). |
| `PLAN_NOT_FOUND` / `RUN_NOT_FOUND` / `ARTEFACT_NOT_FOUND` / `UNIT_NOT_FOUND` | 404 | Unknown id. |
| `PLAN_NOT_APPROVED` | 409 | Starting a run from a plan that is still `draft`. |
| `PLAN_ALREADY_DECIDED` | 409 | Approving or rejecting a plan twice. |
| `REPORT_NOT_READY` | 404 | Report requested before the run finished. |
| `RUN_ALREADY_FINISHED` | 409 | Cancelling a terminal run. |
| `VALIDATION_ERROR` | 422 | Malformed request body. |
| `NOT_FOUND` | 404 | Unknown route. |

> §6 names only the first three plus `NOT_FOUND`; it describes the rest by status alone.
> The others were named here by Dev 2. Additive codes merge immediately under §11 —
> tell the team if you add more.

---

## System — Dev 2

### ✅ `GET /health`
Liveness only, no dependency checks. Always `200 {"status": "ok"}`.

### ✅ `GET /system/status`

```json
{
  "mongodb": "ok" | "unavailable",
  "search_indexes": { "keyword": "ok|unavailable|building", "vector": "..." },
  "runner": { "mode": "trusted_docker|disabled", "available": false },
  "gemini": { "model": "gemini-4-argon", "reachable": false },
  "details": { "mongodb": "why it is unavailable" }
}
```

`mongodb` is a real `ping`. `search_indexes` is a real `listSearchIndexes` on
`code_chunks`. `gemini.reachable` is a real `models.get`, cached per process so polling
does not burn quota. `details` is additive and safe to ignore.

`runner.available` is true only when every interpreter image a target can need exists
(3.12, 2.7 and 3.14), and `details.runner` names the missing ones (#95). `runner.mode`
is the 3.12 image's.

**With an empty `.env` every value is honestly `unavailable`. That is correct, not a bug.**

---

## Repositories & search — Dev 3

All ✅ — implemented and exercised against the real cluster.

| Endpoint | Response |
|---|---|
| `GET /demo-fixtures` | `[{demo_fixture_id, name, description}]`. `chess` first, then the `legacy_orders` test fixture. Importing a fixture again returns the existing repository: one id per fixture content (#82). |
| `POST /repositories/import` | `RepositoryRecord` — multipart `files` + one `paths` entry each (a folder), multipart `file` (ZIP), **or** `{demo_fixture_id, name?}` |
| `GET /repositories` | `RepositoryRecord[]` — **newest first** |
| `GET /repositories/{repo_id}` | `RepositoryRecord` |
| `GET /repositories/{repo_id}/graph?level=module&run_id=` | `GraphPayload` |
| `GET /repositories/{repo_id}/modules/{module_id}` | `ModuleDetail` |
| `GET /repositories/{repo_id}/symbols/{symbol_id}` | `SymbolRecord` + `{source}` |
| `GET /repositories/{repo_id}/search?q=&mode=&limit=` | `{mode_used, native_hybrid, hits}` |
| `POST /repositories/{repo_id}/index` | `{chunks, described, embedded, warnings}` |

Import sets `executable=True` **only** for the allowlisted fixture `legacy_orders`.
Folder and ZIP uploads get `executable=False` and can never run. A folder upload's
`paths` are relative to the folder and pass the same checks as ZIP member names.

### Module docs — Dev 1 (#39)

| Endpoint | Response |
|---|---|
| `GET /repositories/{repo_id}/docs` | `{repository_id, python, prose, modules: [{module_id, path, is_test, legacy: {facts, purpose, markdown}}], warnings}`. No model call. |
| `GET /runs/{run_id}/docs` | The same, with `run_id`, `revision`, `python: {legacy, migrated}`, and per module `migrated: {facts, change, markdown}` or `null`. `change` is `{run_id, unit: {unit_id, status, attempts}, summary, dependencies}`. No model call. |
| `POST /repositories/{repo_id}/docs` | **202** `{repository_id, prose}`, then generates the missing purposes in the background |

- `facts` come from the parse tree (`basis: static_ast`): docstring as written,
  public classes and methods, public functions with signatures, `depends_on`,
  `packages`, `used_by`.
- `purpose` is one model-written sentence per non-test module, from the legacy source,
  `basis: model_generated`, or `null` until generated. `prose.state` is
  `not_generated | generating | ready | partial | failed`, with `done` and `total`.
- `change.summary` is Argon's own summary of the accepted patch, from the run journal.
- Errors: 404 for an unknown repository or run; 409 `SNAPSHOT_UNAVAILABLE` when a tree
  isn't on this machine; 409 `DOCS_GENERATION_ACTIVE`; 503 `MODEL_UNAVAILABLE` with no
  key.
- The export zip (`GET /runs/{id}/export`) carries the same pages under
  `axolotl/docs/`, with cached purposes only: an export never calls a model.

### Search

`mode` is `keyword | vector | hybrid`, default `hybrid`; `limit` is 1–50, default 10.
`q` is required and capped at 500 characters.

- `native_hybrid` says whether `$rankFusion` actually ran, or a Python fallback did.
  It is measured per request, never assumed.
- Each `SearchHit` carries `keyword_rank` / `vector_rank` where known, so the UI can
  show why something ranked where it did.
- `qualified_name` and `kind` are **additive** fields on `SearchHit` (§11: optional,
  default `None`, no consumer breaks). §4 gives a hit a path and a line range but no
  name, which would make a result list read `pricing.py:48-106` with no way to say
  which function that is without a second request per hit. Dev 3 fills them; ignore
  them if you prefer.
- Errors: **404** for an unknown repository, **422** for a bad query or mode, and
  **503 `SEARCH_INDEX_NOT_READY`** (with `details.state`) while an index is building
  or absent. None of these is ever an empty `hits` list — "nothing matched" and "the
  index is not ready" must not look the same.

`POST .../index` re-chunks, re-describes and re-embeds from the stored snapshot. Use
it when `INDEX_ON_IMPORT` was off, when a Gemini key arrives late, or when the
embedding model changes. See `docs/mongodb-atlas.md`.

The status overlay on `/graph` comes from Dev 2's
`orchestration.worker.get_unit_statuses(run_id)` — call it, don't recompute it.

---

## Migration targets — Dev 2

### ✅ `GET /migration-targets` → `MigrationTarget[]`

| `target_id` | supported | why not |
|---|---|---|
| `modern_python3` | ✅ | — |
| `fastapi` | ❌ | stretch goal, not in the MVP |
| `async_python` | ❌ | changing concurrency changes observable behaviour, so differential verification is meaningless |
| `python_to_typescript` | ❌ | no shared runtime for the verifier to execute both sides in |
| `python2_to_3` | ❌ | needs a Python 2 runtime in the sandbox image. **The MVP target since 24 Oct:** [#21](https://github.com/Prittstick22/axolotl/issues/21) |

Unsupported targets are returned so the UI can show them **disabled with a reason**,
not hidden. Asking for one is 422 `UNSUPPORTED_TARGET`.

---

## Plans — Dev 2

### ⏳ `POST /repositories/{repo_id}/plans` → `MigrationPlan` (`draft`)
Body `{"target_id": "modern_python3"}`.

Validates the target (422) → loads the repository and graph from Dev 3 → `build_units`
→ asks Argon to order them → validates that order → persists.

Returns 503 `INTEGRATION_UNAVAILABLE` until Dev 3's graph exists. **Target validation
happens first**, so T-08 holds even with Atlas down.

Argon may only *reorder*. An order that is not a valid topological order of the
condensation is discarded for the deterministic one, with the reason in `rationale`.
If Argon is unreachable the plan is still built, deterministically, and says so.

### ✅ `GET /plans/{plan_id}` → `MigrationPlan`

### ✅ `POST /plans/{plan_id}/approve` → `MigrationPlan`
Body `{"approved": true}`. Sets `approved_at`. 409 `PLAN_ALREADY_DECIDED` on a second call.

---

## Runs — Dev 2

### ✅ `POST /plans/{plan_id}/runs` → `RunSummary` (`queued`)
409 `PLAN_NOT_APPROVED` unless the plan is approved. Starts a background asyncio task —
**work is never tied to the HTTP request lifetime**.

409 `RUN_ALREADY_ACTIVE` if a run is already active **for that repository** (#18), with
`details.active_run_id` and its state. Scoped to the repository rather than the plan:
the hazard is two Argon sessions migrating one snapshot at once, spending the quota
twice and producing two competing sets of accepted revisions, and a second plan on the
same repository does that just as well as a second click on one plan. Cancel the active
run, or wait for it; a repository can be migrated again once its run has finished.

503 `RUNNER_UNAVAILABLE` if the image for the plan's baseline or candidate interpreter
can't run here (#95), checked before the run is created. The message names the
missing image and how to build it. Chess (`python2_to_3`) needs `axolotl-runner:py2.7`
and `axolotl-runner:py3.14`.

The Plan screen already shows "View run" when the URL carries a run, so this closes the
API side of the same hazard.

### ✅ `GET /runs/{run_id}` → `RunSummary`
Poll this every 1.5 s while `state` is non-terminal.

### ✅ `GET /runs/{run_id}/events?after_sequence=0&limit=200` → `RunEvent[]`
Ascending, unique monotonic `sequence` starting at 1. Poll with the last sequence you
saw; you cannot miss or double-count an event.

### ✅ `POST /runs/{run_id}/cancel` → `RunSummary`
Sets a flag the worker checks between steps. 409 `RUN_ALREADY_FINISHED` on a terminal run.

### ⏳ `GET /runs/{run_id}/units/{unit_id}`
`{unit, verifications, counterexamples}`. Held-out inputs are redacted unless
`exposed_to_model`. `verifications` stays empty until Dev 4 writes `verification_results`.

### ⏳ `GET /runs/{run_id}/diff?unit_id=` → `{base_revision, revision, files[]}`
**T-16**: the diff shown must be the diff of the verified accepted revision. Needs Dev 4's `diff`.

### ✅ `GET /runs/{run_id}/report` → `RunReport`
404 `REPORT_NOT_READY` until the run finishes. Every number comes from
`verification_results`, `counterexamples` and `run_events` — **never from model text**.
`narrative` is the only model-written prose and is labelled as such.
`coverage_caveats` always states that agreement on N cases is not a proof of equivalence.

### ✅ `GET /runs/{run_id}/summary` → migration summary (#35)
One overview of what the run changed. 404 `REPORT_NOT_READY` until the report exists.
- `units[]`: `{unit_id, paths, status, attempts, accepted_revision, tests: {passed,
  failed, inconclusive, gates}, counterexamples: {found, resolved}, lines: {added,
  removed} | null, argon_summary: {text, basis: "model_written", accepted} | null}`.
- `totals`: units, verified, blocked, skipped, files changed, lines added and removed,
  the report's test and counterexample counts, and `final_integration`.
- `python: {before, after}`, `dependencies` (from #34), `caveats`, `limitations`,
  `basis` per section, `warnings`, and `markdown` (also `axolotl/SUMMARY.md` in the export).
- Tests run per unit are the last `gate_result` per gate, so a repair isn't counted
  twice; with the final-integration gate they sum to the report's `tests_passed`.
- `lines`, `python` and `dependencies` need the run's trees: on another machine they
  are `null` with a warning, and everything from the report and journal still answers.

### ✅ `GET /runs/{run_id}/artefacts` → `[{artefact_id, kind, sha256, download_url}]`
### ✅ `GET /artefacts/{artefact_id}/download` → file stream
### ✅ `GET /runs/{run_id}/export` → ZIP of the migrated project (#40)
The final accepted tree with `axolotl/MIGRATION.md`, `report.json`, `SUMMARY.md` (#35)
and `docs/` (#39). Not the patch (#133): that stays its own artefact.
Built on request, not listed as an artefact. 404 `REPORT_NOT_READY` before the report,
404 `ARTEFACT_NOT_FOUND` when no unit was accepted or the tree is gone.

---

## Run events — §7

Written only through `events.append_event`. Payloads are redacted and capped at 2 KB
(`GateResult.log_excerpt` is a different field with a 4 KB cap — don't conflate them).

| Emitted by Dev 2 | Payload |
|---|---|
| `run_created` | `{plan_id, target_id}` |
| `run_state_changed` / `unit_state_changed` | `{from, to}` |
| `baseline_captured` | `BaselineSummary` subset |
| `patch_proposed` | `{files, summary, bytes}` |
| `patch_applied` / `patch_rejected` | `{workspace_revision}` / `{errors}` |
| `verification_started` | `{workspace_revision, scope}` |
| `gate_result` | `GateResult`. A harness gate's `log_excerpt`, or one that mentions a held-out case, is replaced with "case outputs withheld" (#94) |
| `verification_passed` / `verification_failed` | `{verification_id, failed_case_ids, counterexample_ids}` |
| `counterexample_recorded` | `{counterexample_id, case_id, visibility, ...}` |
| `repair_started` | `{attempt, budget_remaining}` |
| `snapshot_accepted` | `{revision}` |
| `unit_blocked` | `{reason, verification_id}` — tried, could not be verified |
| `unit_skipped` | `{reason, verification_id}` — will not be attempted: blocked prerequisite, cancellation, or restart (#27) |
| `report_generated` | `{artefact_ids}` |
| `error` | `{code, message}` |

| Emitted by Dev 1, via `UnitController.emit` | Payload |
|---|---|
| `agent_turn` | `{interaction_id, thinking_level, text_excerpt}` |
| `agent_tool_call` | `{tool, args_excerpt}` |
| `agent_tool_result` | `{tool, ok, result_excerpt}` |

`emit()` rejects anything else. The model cannot write itself a passing verification.

---

## Conventions

- **Polling:** `GET /runs/{id}` and `/events?after_sequence=N` every 1.5 s while non-terminal.
- **CORS:** only `FRONTEND_ORIGIN` (`http://localhost:5173` by default).
- **Datetimes:** timezone-aware, UTC in the database. UK clocks go back at 02:00 on
  Sun 25 Oct, mid-hackathon — never use naive timestamps.
- **Money:** `Decimal`, encoded per §5 as `{"__decimal__": "12.35"}`. Never float.
- **Field names:** snake_case everywhere, including in TypeScript, so the mirror in
  `frontend/src/types/contracts.ts` is one-to-one with `backend/app/models/contracts.py`.


### Declared runtime metadata

`GET /api/v1/repositories/{repo_id}/runtime` verifies repository existence and reads
literal package requirements from `setup.py` or `pyproject.toml` in the snapshot.
Supports a project root or a single top-level archive directory. No source executes.
Returns `{declared_python: string|null, evidence: string[], runtime_verified: false,
note: string}`. Contradictory/missing declarations yield null. `2.7` means an explicit
2.7 requirement excluding Python 3; it does not imply Python 2 execution occurred.
