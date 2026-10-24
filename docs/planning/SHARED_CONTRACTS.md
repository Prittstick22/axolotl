# Axolotl — Shared Contracts (give this file to EVERY agent)

**Version:** 1.0 · 22 Oct 2026 · Companion to `Axolotl_Product_Requirements_and_Implementation_Handoff.md` (the "Spec").
If this file and the Spec disagree, the Spec wins on *product/safety rules*. This file wins on *names, shapes and ownership*. Change it only through the process in §11.

---

## 0. Rules every agent follows

1. *(Removed on 24 Oct 2026. The number is kept so the §0.2–§0.8 references in the code stay correct.)*
2. **No fakes.** No mocked AI output, hard-coded test results, invented graph edges, fake search, timer-driven progress or in-memory DB fallback in anything shown to judges. Temporary stubs are allowed *only* behind an interface while the real implementation is being built, must be named `*_stub`, must log a warning, and must be deleted before the Sat 17:00 dry run.
3. **The verifier is the only source of pass/fail.** The model never decides success.
4. **Baseline is immutable.** Nothing ever writes into the original snapshot or the test oracle.
5. **Only trusted fixtures execute.** Uploaded ZIPs are static-analysis-only unless `ALLOW_ARBITRARY_CODE_EXECUTION=true` (it stays `false` for the hackathon). No uploaded code ever runs via host `subprocess`.
6. **Stay in your lane.** Only edit files you own (§2). Need a change elsewhere? Ask the owner, or propose it via §11.
7. **Honest wording.** "Behaviour preserved" means *agreement on the tested cases*, never proof of equivalence.
8. Use timezone-aware datetimes (UTC in the DB). UK clocks go back at 02:00 Sun 25 Oct.

---

## 1. Team and roles

| Dev | Role | Short name |
|---|---|---|
| Dev 1 | Gemini 4 Argon agent: model adapter, tool schemas, prompts, agent loop | **Agent** |
| Dev 2 | Orchestrator, scheduler, run lifecycle, events, plans/runs API, reports, **integration lead** | **Orchestrator** |
| Dev 3 | Ingestion, AST analysis, dependency graph, MongoDB Atlas persistence + Search/Vector/Hybrid | **Graph & Atlas** |
| Dev 4 | Demo legacy fixture, behaviour cases, sandbox runner, workspaces, differential verifier | **Verifier** |
| Dev 5 | React/TypeScript frontend, demo flow, video recording | **Frontend** |

Dev 2 is the integration lead: owns `main` branch health, `backend/app/models/contracts.py`, `backend/app/main.py` wiring and the final README.

---

## 2. Repository layout and ownership

```text
axolotl/
├── README.md                         Dev 2
├── .env.example                      Dev 2
├── docker-compose.yml (optional)     Dev 2
├── docs/
│   ├── architecture.md               Dev 2
│   ├── api-contracts.md              Dev 2 (generated/kept in sync with §6)
│   ├── demo-script.md                Dev 5
│   ├── mongodb-atlas.md              Dev 3
│   ├── argon-agent.md                Dev 1
│   └── safety-and-limitations.md     Dev 4
├── frontend/                         Dev 5 (everything)
├── backend/
│   ├── pyproject.toml                Dev 2 (others request deps via PR)
│   ├── app/
│   │   ├── main.py                   Dev 2
│   │   ├── config.py                 Dev 2
│   │   ├── models/contracts.py       Dev 2 (content defined by §4 of this file)
│   │   ├── api/
│   │   │   ├── repositories.py       Dev 3
│   │   │   ├── search.py             Dev 3
│   │   │   ├── plans_runs.py         Dev 2
│   │   │   ├── targets.py            Dev 2
│   │   │   └── system.py             Dev 2 (aggregates health from others)
│   │   ├── persistence/              Dev 3 (db client, indexes, base repo helpers)
│   │   ├── ingestion/                Dev 3
│   │   ├── analysis/                 Dev 3
│   │   ├── graph/                    Dev 3 (graph build + load_networkx)
│   │   ├── search/                   Dev 3
│   │   ├── agents/                   Dev 1
│   │   ├── orchestration/            Dev 2 (state machine, scheduler, events, controller)
│   │   ├── reports/                  Dev 2
│   │   ├── sandbox/                  Dev 4 (runner, workspaces)
│   │   └── verification/             Dev 4 (baseline, cases, comparison, gates)
│   └── tests/{unit,integration,security}/  each dev tests their own package
├── runner/
│   ├── Dockerfile                    Dev 4
│   ├── harness/run_cases.py          Dev 4
│   └── policy.md                     Dev 4
└── demo/
    ├── legacy_orders/                Dev 4
    ├── challenge_cases/              Dev 4 (held-out; never given to the model)
    └── expected_demo_flow.md         Dev 5
```

**Stack versions:** Python 3.12, FastAPI, Pydantic v2, `pymongo>=4.9` (`AsyncMongoClient`, async MongoDB), NetworkX, `google-genai>=2.3.0`, pytest. Frontend: Vite + React 19 + TypeScript, `@xyflow/react`, `@monaco-editor/react`, TanStack Query. Backend on `:8000`, frontend on `:5173`.

---

## 3. Identifiers

| Thing | Format | Example |
|---|---|---|
| repository | `repo_` + 12 hex | `repo_3f9a1c0b77de` |
| snapshot hash | `sha256:` + hex of sorted (path, file-hash) list | `sha256:ab12…` |
| module node | `module_` + dotted name with `.`→`__` | `module_pricing`, `module_billing__tax` |
| external node | `ext_` + top-level package | `ext_requests` |
| symbol | `sym_` + 16 hex of sha256(snapshot_hash, path, qualified_name, start_line) | `sym_9c0e…` |
| edge | `edge_` + 16 hex of sha256(source, target, type, source_line) | |
| migration unit | `unit_` + sorted module ids joined by `+` for SCCs, else module id suffix | `unit_pricing`, `unit_a+b` |
| plan / run | `plan_` / `run_` + 12 hex | |
| case | `case_` + 3-digit for visible, `hcase_` for held-out | `case_012`, `hcase_004` |
| workspace revision | `rev_` + 12 hex; baseline revision is literally `rev_baseline` | |
| verification / counterexample / artefact | `ver_` / `cex_` / `art_` + 12 hex | |

IDs are deterministic where listed; never derived from model output.

---

## 4. Domain models (Pydantic v2) — `backend/app/models/contracts.py`

Dev 2 creates this file in the first hour after kickoff, exactly as below. Everyone imports from it. TypeScript mirrors live in `frontend/src/types/contracts.ts` (Dev 5) and must use identical field names (snake_case).

```python
from typing import Literal, Any
from datetime import datetime
from pydantic import BaseModel, Field

# ---------- enums ----------
NodeKind = Literal["local_module", "external_package", "test_module"]
EdgeType = Literal["imports", "from_imports", "defines", "inherits", "calls", "test_targets", "unknown_dynamic"]
Resolution = Literal["confirmed", "heuristic", "unresolved"]
SymbolKind = Literal["module", "class", "function", "async_function", "method", "nested_function"]

UnitStatus = Literal["not_planned", "queued", "baselining", "migrating", "verifying",
                     "repairing", "passed", "blocked", "failed", "skipped"]
RunState = Literal["queued", "baselining", "migrating", "verifying", "repairing",
                   "final_verification", "completed", "completed_with_warnings",
                   "failed", "cancelled"]
PlanStatus = Literal["draft", "approved", "rejected", "superseded"]
Risk = Literal["low", "medium", "high"]
GateName = Literal["compile", "existing_tests", "differential", "impacted_tests",
                   "final_integration", "sandbox_policy"]
GateStatus = Literal["passed", "failed", "inconclusive", "skipped"]
SearchMode = Literal["keyword", "vector", "hybrid"]

# ---------- repository / graph ----------
class RepositoryRecord(BaseModel):
    repository_id: str
    name: str
    snapshot_hash: str
    origin: Literal["demo_fixture", "zip_upload", "folder_upload"]
    executable: bool                 # True only for allowlisted trusted fixtures
    demo_fixture_id: str | None = None
    file_count: int
    module_count: int
    symbol_count: int
    test_file_count: int
    analysis_status: Literal["ok", "partial", "failed"]
    warnings: list[str] = []
    created_at: datetime

class GraphNode(BaseModel):
    id: str
    label: str                       # "pricing.py"
    kind: NodeKind
    path: str | None = None
    status: UnitStatus = "not_planned"   # overlaid from a run when run_id given
    unit_id: str | None = None
    function_count: int = 0
    class_count: int = 0
    warnings: list[str] = []

class GraphEdge(BaseModel):
    id: str
    source: str                      # importer
    target: str                      # dependency   (A -> B means A depends on B)
    type: EdgeType
    resolution: Resolution
    basis: Literal["static_ast", "heuristic"]
    source_path: str | None = None
    source_line: int | None = None

class GraphPayload(BaseModel):
    repository_id: str
    snapshot_hash: str
    level: Literal["module"] = "module"
    run_id: str | None = None
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    warnings: list[str] = []

class SymbolRecord(BaseModel):
    repository_id: str
    snapshot_hash: str
    symbol_id: str
    kind: SymbolKind
    qualified_name: str              # "pricing.apply_discount"
    module_id: str
    path: str
    start_line: int
    end_line: int
    signature: str | None = None
    decorators: list[str] = []
    bases: list[str] = []
    docstring: str | None = None     # untrusted data
    parent_symbol_id: str | None = None
    source_chunk_id: str | None = None
    analysis_warnings: list[str] = []

class ModuleDetail(BaseModel):
    module_id: str
    path: str
    symbols: list[SymbolRecord]
    imports: list[GraphEdge]         # outbound
    dependants: list[GraphEdge]      # inbound
    source: str
    warnings: list[str] = []
    test_case_count: int = 0         # visible cases only

class SearchHit(BaseModel):
    chunk_id: str
    symbol_id: str | None
    module_id: str
    path: str
    start_line: int
    end_line: int
    snapshot_hash: str
    snippet: str
    description: str | None
    match_method: Literal["keyword", "vector", "hybrid"]
    score: float
    rank: int
    keyword_rank: int | None = None   # contribution breakdown for hybrid
    vector_rank: int | None = None

# ---------- planning ----------
class MigrationTarget(BaseModel):
    target_id: str                   # "modern_python3"
    label: str
    supported: bool
    description: str
    unsupported_reason: str | None = None

class MigrationUnit(BaseModel):
    unit_id: str
    module_ids: list[str]
    file_paths: list[str]            # the ONLY files a patch may touch for this unit
    depends_on_unit_ids: list[str]
    dependant_unit_ids: list[str]
    is_cycle: bool = False
    risk: Risk = "low"
    risk_factors: list[str] = []
    visible_case_count: int = 0

class MigrationPlan(BaseModel):
    plan_id: str
    repository_id: str
    snapshot_hash: str
    target_id: str
    units: list[MigrationUnit]
    order: list[str]                 # unit_ids; validated against scheduler
    rationale: str                   # from Argon
    risks: list[str]                 # from Argon
    coverage_gaps: list[str]
    status: PlanStatus = "draft"
    model: str
    interaction_id: str | None = None
    created_at: datetime
    approved_at: datetime | None = None

class PlanProposal(BaseModel):       # what Dev 1 returns; Dev 2 validates into MigrationPlan
    suggested_order: list[str]
    rationale: str
    risks: list[str]
    coverage_gaps: list[str]
    per_unit_notes: dict[str, str] = {}
    interaction_id: str | None = None

# ---------- behaviour & verification ----------
class BehaviourCase(BaseModel):
    case_id: str
    repository_id: str
    unit_id: str
    function: str                    # dotted, e.g. "pricing.final_total"
    args: list[Any]                  # encoded per §5
    kwargs: dict[str, Any] = {}
    visibility: Literal["visible", "held_out"]
    exposed_to_model: bool = False   # held-out case revealed during repair
    description: str | None = None
    provenance: str                  # e.g. "demo/legacy_orders/tests/cases.json"
    case_hash: str

class CaseOutcome(BaseModel):
    case_id: str
    status: Literal["ok", "raised", "timeout", "harness_error"]
    value: Any = None                # canonical JSON per §5
    exception_type: str | None = None
    exception_message: str | None = None
    duration_ns: int | None = None   # median call time inside the harness, warm-up excluded
    duration_samples_ns: list[int] | None = None  # additive #46: five sandbox CPU samples

class CaseComparison(BaseModel):
    case_id: str
    visibility: Literal["visible", "held_out"]
    verdict: Literal["match", "mismatch", "inconclusive"]
    old: CaseOutcome
    new: CaseOutcome

class GateResult(BaseModel):
    gate: GateName
    status: GateStatus
    required: bool = True
    summary: str
    passed: int = 0
    failed: int = 0
    inconclusive: int = 0
    log_excerpt: str = ""            # truncated to 4 KB, secrets redacted

class VerificationResult(BaseModel):
    verification_id: str
    run_id: str
    unit_id: str | None              # None = final whole-project verification
    attempt: int
    workspace_revision: str
    gates: list[GateResult]
    comparisons: list[CaseComparison]
    counterexample_ids: list[str]
    all_required_gates_passed: bool
    runner_mode: str
    started_at: datetime
    finished_at: datetime
    environment: dict[str, str] | None = None   # §11, #22: python, image, image_digest, runner_mode

class Counterexample(BaseModel):
    counterexample_id: str
    run_id: str
    unit_id: str
    attempt: int
    case_id: str
    visibility: Literal["visible", "held_out"]
    exposed_to_model: bool
    function: str
    args: list[Any]
    kwargs: dict[str, Any]
    old: CaseOutcome
    new: CaseOutcome
    status: Literal["open", "resolved", "unresolved"]
    resolved_by_verification_id: str | None = None

class BaselineSummary(BaseModel):
    run_id: str
    snapshot_hash: str
    unit_ids: list[str]
    cases_captured: int
    cases_failed_to_capture: int
    existing_tests: GateResult
    environment: dict[str, str]      # python version, image digest, runner mode

# ---------- workspaces & patches ----------
class FilePatch(BaseModel):
    path: str                        # repo-relative, must be in unit.file_paths
    new_content: str                 # full-file replacement (max 64 KB)

class PatchProposal(BaseModel):
    unit_id: str
    files: list[FilePatch]
    summary: str
    rationale: str

class PatchAck(BaseModel):
    accepted: bool
    workspace_revision: str | None
    errors: list[str] = []           # structured rejection reasons for the model

class Workspace(BaseModel):
    workspace_id: str
    run_id: str
    root: str                        # absolute path on backend host
    base_revision: str               # last accepted revision or "rev_baseline"
    revision_id: str

# ---------- runs & events ----------
class UnitRunState(BaseModel):
    unit_id: str
    module_ids: list[str]
    status: UnitStatus
    attempts: int = 0
    last_verification_id: str | None = None
    accepted_revision: str | None = None

class RunSummary(BaseModel):
    run_id: str
    plan_id: str
    repository_id: str
    state: RunState
    current_unit: str | None
    units: list[UnitRunState]
    units_total: int
    units_verified: int
    units_blocked: int
    tests_passed: int
    tests_failed: int
    tests_inconclusive: int
    last_event_sequence: int
    model: str
    runner_mode: str
    created_at: datetime
    updated_at: datetime
    error: str | None = None

class RunEvent(BaseModel):
    run_id: str
    sequence: int                    # unique, monotonic per run, starts at 1
    timestamp: datetime
    event_type: str                  # see §7
    unit_id: str | None = None
    attempt: int | None = None
    payload: dict[str, Any] = {}

class UnitTiming(BaseModel):
    unit_id: str
    cases_timed: int
    before_median_us: float          # median over cases, old code
    after_median_us: float           # same cases, accepted revision
    within_noise: bool              # delta <= larger median per-case sample IQR; heuristic

class PerformanceSummary(BaseModel):
    method: str                      # e.g. "median of 5 runs per case after 1 warm-up"
    units: list[UnitTiming]          # units with at least one timed case; the rest are untimed

class RunReport(BaseModel):
    run_id: str
    verdict: Literal["completed", "completed_with_warnings", "failed", "cancelled"]
    units: list[UnitRunState]
    final_verification_id: str | None
    changed_files: list[str]
    tests_passed: int
    tests_failed: int
    tests_inconclusive: int
    counterexamples_found: int
    counterexamples_resolved: int
    baseline_snapshot_hash: str
    final_artefact_hash: str | None
    coverage_caveats: list[str]
    limitations: list[str]
    narrative: str | None            # Argon summary grounded in recorded evidence
    artefact_ids: list[str]
    generated_at: datetime
    performance: PerformanceSummary | None = None   # runtime before/after; None until timed
```

---

## 5. Value encoding for behaviour cases (Dev 4 owns, everyone respects)

JSON with tagged types, used for case args/kwargs and canonical outputs:

| Python | Encoding |
|---|---|
| `Decimal("12.35")` | `{"__decimal__": "12.35"}` (string form, normalised exponent kept as-is) |
| `float` | `{"__float__": "repr(x)"}` |
| `tuple` | `{"__tuple__": [...]}` |
| `datetime` / `date` | `{"__datetime__": iso}` / `{"__date__": iso}` |
| dataclass / object with `__dict__` | `{"__object__": "ClassName", "fields": {...}}` |
| `dict` | sorted keys; non-str keys → `{"__dict_items__": [[k, v], ...]}` |
| `set` | `{"__set__": sorted list}` |
| exceptions | outcome `status="raised"`, `exception_type` = class `__name__` |

**Comparison contract:** outputs equal ⇔ canonical encodings equal. Exceptions match on type name only (messages reported, not compared). Note: a modernised module returning a dataclass instead of a dict *is a public behaviour change* and will fail — that's intended; the agent must preserve public return shapes.

---

## 6. HTTP API — prefix `/api/v1`

Errors: `{"error": {"code": "SNAKE_CASE", "message": "...", "details": {}}}` with proper status codes.

| Method & path | Owner | Request | Response |
|---|---|---|---|
| `GET /system/status` | Dev 2 | — | `{mongodb, search_indexes:{keyword,vector}, runner:{mode,available}, gemini:{model,reachable}}` each `"ok"|"unavailable"|"building"` |
| `GET /demo-fixtures` | Dev 3 | — | `[{demo_fixture_id, name, description}]` |
| `POST /repositories/import` | Dev 3 | multipart `file` (ZIP) **or** JSON `{demo_fixture_id, name?}` | `RepositoryRecord` |
| `GET /repositories` | Dev 3 | — | `RepositoryRecord[]` |
| `GET /repositories/{repo_id}` | Dev 3 | — | `RepositoryRecord` |
| `GET /repositories/{repo_id}/graph` | Dev 3 | `?level=module&run_id=` | `GraphPayload` (status overlay comes from Dev 2's `get_unit_statuses(run_id)`) |
| `GET /repositories/{repo_id}/modules/{module_id}` | Dev 3 | — | `ModuleDetail` |
| `GET /repositories/{repo_id}/symbols/{symbol_id}` | Dev 3 | — | `SymbolRecord` + `{source}` |
| `GET /repositories/{repo_id}/search` | Dev 3 | `?q=&mode=keyword|vector|hybrid&limit=10` | `{mode_used, native_hybrid: bool, hits: SearchHit[]}` — 503 `SEARCH_INDEX_NOT_READY` while an index is building **or** the repository is still being indexed; never an empty `hits` list for either, because "nothing matched" is a different answer |
| `POST /repositories/{repo_id}/index` | Dev 3 | — | `{chunks, described, embedded, warnings}` — 409 `INDEXING_IN_PROGRESS` if indexing is already running, 409 `SNAPSHOT_UNAVAILABLE` if the snapshot is not on this machine |
| `GET /repositories/{repo_id}/index` | Dev 3 | — | `{repository_id, state: "indexing"|"ready"|"failed"|null, embedded, total, reason}` — `state` is null when no indexing was ever recorded |
| `GET /migration-targets` | Dev 2 | — | `MigrationTarget[]` |
| `POST /repositories/{repo_id}/plans` | Dev 2 | `{target_id}` | `MigrationPlan` (status `draft`) — 422 `UNSUPPORTED_TARGET`, 409 `REPOSITORY_NOT_EXECUTABLE` |
| `GET /plans/{plan_id}` | Dev 2 | — | `MigrationPlan` |
| `POST /plans/{plan_id}/approve` | Dev 2 | `{approved: bool}` | `MigrationPlan` |
| `POST /plans/{plan_id}/runs` | Dev 2 | — | `RunSummary` (state `queued`) — 409 `PLAN_NOT_APPROVED` if the plan is not approved, 409 `RUN_ALREADY_ACTIVE` if a run is already active for that **repository** |
| `GET /runs/{run_id}` | Dev 2 | — | `RunSummary` |
| `GET /runs/{run_id}/events` | Dev 2 | `?after_sequence=0&limit=200` | `RunEvent[]` ascending |
| `GET /runs/{run_id}/units/{unit_id}` | Dev 2 | — | `{unit: UnitRunState, verifications: VerificationResult[], counterexamples: Counterexample[]}` (held-out case inputs redacted unless `exposed_to_model`) |
| `GET /runs/{run_id}/diff` | Dev 2 (calls Dev 4) | `?unit_id=` | `{base_revision, revision, files:[{path, old_content, new_content, unified_diff}]}` |
| `GET /runs/{run_id}/report` | Dev 2 | — | `RunReport` (404 until generated) |
| `GET /runs/{run_id}/artefacts` | Dev 2 | — | `[{artefact_id, kind:"patch"|"report"|"archive", sha256, download_url}]` |
| `GET /artefacts/{artefact_id}/download` | Dev 2 | — | file stream |
| `POST /runs/{run_id}/cancel` | Dev 2 | — | `RunSummary` |

Frontend polls `GET /runs/{id}` and `/events?after_sequence=N` every 1.5 s while state is non-terminal. CORS allows only `FRONTEND_ORIGIN`.

---

## 7. Run events — `run_events` collection

Written **only** through Dev 2's `append_event(run_id, event_type, unit_id=None, attempt=None, payload={})`. Sequence comes from an atomic `$inc` on `counters` (`_id: run_id`); unique index `(run_id, sequence)`.

| event_type | Emitted by | Payload |
|---|---|---|
| `run_created` | Dev 2 | `{plan_id, target_id}` |
| `run_state_changed` | Dev 2 | `{from, to}` |
| `unit_state_changed` | Dev 2 | `{from, to}` |
| `baseline_captured` | Dev 2 (data from Dev 4) | `BaselineSummary` subset |
| `agent_turn` | Dev 1 via emitter | `{interaction_id, thinking_level, text_excerpt}` |
| `agent_tool_call` | Dev 1 via emitter | `{tool, args_excerpt}` |
| `agent_tool_result` | Dev 1 via emitter | `{tool, ok, result_excerpt}` |
| `patch_proposed` | Dev 2 | `{files, summary, bytes}` |
| `patch_rejected` | Dev 2 | `{errors}` |
| `patch_applied` | Dev 2 | `{workspace_revision}` |
| `verification_started` | Dev 2 | `{workspace_revision, scope:"unit"|"final"}` |
| `gate_result` | Dev 2 (data from Dev 4) | `GateResult` |
| `verification_passed` / `verification_failed` | Dev 2 | `{verification_id, failed_case_ids, counterexample_ids}` |
| `counterexample_recorded` | Dev 2 | `{counterexample_id, case_id, visibility, old_value, new_value}` (held-out values only if exposed) |
| `repair_started` | Dev 2 | `{attempt, budget_remaining}` |
| `snapshot_accepted` | Dev 2 | `{revision}` |
| `unit_blocked` | Dev 2 | `{reason, verification_id}` — a unit that **was tried** and could not be verified |
| `unit_skipped` | Dev 2 | `{reason, verification_id}` — a unit that **will not be attempted**: a blocked prerequisite (T-13), a cancellation, or a restart. Additive (#27): one blocked unit used to journal four `unit_blocked` events, so the Run screen showed one failure as four red rows |
| `report_generated` | Dev 2 | `{artefact_ids}` |
| `error` | anyone via Dev 2 | `{code, message}` |

Excerpts are truncated to 2 KB and pass through `redact()` (Dev 2) which strips anything matching env secret values.

---

## 8. Python service interfaces between packages

All async unless noted. Owners implement; consumers call only these.

### Dev 3 — `app.ingestion`, `app.graph`, `app.analysis`, `app.search`, `app.persistence`
```python
async def get_db() -> AsyncDatabase
async def import_repository(*, zip_path: Path | None = None, demo_fixture_id: str | None = None, name: str | None = None) -> RepositoryRecord
async def get_repository(repo_id: str) -> RepositoryRecord
def snapshot_path(repo_id: str) -> Path                       # read-only baseline tree (sync)
async def get_graph(repo_id: str, run_id: str | None = None) -> GraphPayload
async def load_networkx(repo_id: str) -> nx.DiGraph           # local modules only; edge attr: type, resolution
async def get_module_detail(repo_id: str, module_id: str) -> ModuleDetail
async def get_symbol(repo_id: str, symbol_id: str) -> tuple[SymbolRecord, str]   # (record, source slice)
async def find_symbol(repo_id: str, qualified_name: str) -> SymbolRecord | None
async def search_repository(repo_id: str, query: str, mode: SearchMode = "hybrid", limit: int = 8) -> dict
#   -> {"mode_used": SearchMode, "native_hybrid": bool, "hits": list[SearchHit]}
async def search_status() -> dict                              # index readiness
```

### Dev 4 — `app.sandbox`, `app.verification`
```python
async def runner_status() -> dict                              # {mode, available, image_digest}
async def load_cases(repo_id: str, unit_id: str | None, *, include_held_out: bool) -> list[BehaviourCase]
async def capture_baseline(run_id: str, repo_id: str, units: list[MigrationUnit]) -> BaselineSummary
async def create_workspace(run_id: str, repo_id: str, base_revision: str) -> Workspace   # fresh copy of last accepted revision
async def apply_patch(ws: Workspace, patch: PatchProposal, allowed_paths: list[str]) -> PatchAck
async def verify_unit(run_id: str, unit: MigrationUnit, ws: Workspace, impacted: list[MigrationUnit], attempt: int) -> VerificationResult
async def verify_final(run_id: str, ws: Workspace) -> VerificationResult
async def accept_workspace(ws: Workspace) -> str               # returns accepted revision id
async def diff(run_id: str, base_revision: str, revision: str, paths: list[str] | None = None) -> list[dict]
async def get_counterexamples(run_id: str, unit_id: str, *, for_model: bool) -> list[Counterexample]
async def expose_case(run_id: str, case_id: str) -> None       # marks a held-out case exposed_to_model
```

### Dev 2 — `app.orchestration`, `app.reports`
```python
def build_units(g: nx.DiGraph, files_by_module: dict[str, str]) -> list[MigrationUnit]   # sync, SCC-collapsed
def eligible_units(units: list[MigrationUnit], statuses: dict[str, UnitStatus]) -> list[str]
def impacted_units(unit_id: str, units: list[MigrationUnit]) -> list[str]                # transitive reverse dependants
async def append_event(run_id: str, event_type: str, unit_id: str | None = None, attempt: int | None = None, payload: dict | None = None) -> RunEvent
async def list_events(run_id: str, after_sequence: int, limit: int = 200) -> list[RunEvent]
async def get_unit_statuses(run_id: str) -> dict[str, UnitStatus]                         # module_id -> status (for graph overlay)
async def create_plan(repo_id: str, target_id: str) -> MigrationPlan
async def start_run(plan_id: str) -> RunSummary
```

### Dev 1 — `app.agents`
```python
async def propose_plan(ctx: PlanContext) -> PlanProposal
async def run_unit_session(ctx: UnitSessionContext, controller: UnitController) -> UnitSessionOutcome
async def narrate_report(evidence: dict) -> str               # optional; must cite only provided evidence

class PlanContext(BaseModel):
    repository: RepositoryRecord
    graph: GraphPayload
    units: list[MigrationUnit]             # from build_units; Argon may only reorder within eligibility
    target: MigrationTarget
    module_summaries: dict[str, str]       # module_id -> symbol list / signatures

class UnitSessionContext(BaseModel):
    run_id: str
    repository_id: str
    unit: MigrationUnit
    target: MigrationTarget
    plan_notes: str
    previous_interaction_id: str | None
    max_patch_attempts: int                # 3 (1 initial + 2 repairs)
    max_tool_calls: int                    # 25

class UnitSessionOutcome(BaseModel):
    ended_because: Literal["verified", "budget_exhausted", "model_gave_up", "error"]
    interaction_ids: list[str]
    final_message: str
```

`UnitController` is a Protocol **implemented by Dev 2**, consumed by Dev 1:
```python
class UnitController(Protocol):
    async def submit_patch(self, patch: PatchProposal) -> PatchAck          # fresh workspace from last accepted revision, validates, applies
    async def run_verification(self) -> dict                                 # runs verify_unit; returns model-safe summary; authoritative result persisted by Dev 2
    async def get_counterexamples(self) -> list[dict]                        # model-safe (held-out redacted unless exposed)
    async def emit(self, event_type: str, payload: dict) -> None             # agent_turn / agent_tool_call / agent_tool_result only
    def patch_attempts_remaining(self) -> int
    def is_verified(self) -> bool
```

---

## 9. MongoDB collections and owners

Database: `axolotl`. Dev 3 owns the client, index creation script (`app/persistence/indexes.py`, idempotent, runs at startup) and Atlas Search index definitions.

| Collection | Writer | Key indexes |
|---|---|---|
| `repositories` | Dev 3 | `repository_id` unique |
| `symbols` | Dev 3 | `(repository_id, symbol_id)` unique, `(repository_id, qualified_name)` |
| `edges` | Dev 3 | `(repository_id, source_symbol_id)`, `(repository_id, target_symbol_id)` |
| `code_chunks` | Dev 3 | `(repository_id, chunk_id)` unique; Atlas Search index `code_chunks_text`; Vector index `code_chunks_vector` |
| `search_index_status` | Dev 3 | `repository_id` unique, index name `index_status_repository_unique` (one row per repository: indexing progress, not the Atlas index definitions) |
| `migration_plans` | Dev 2 | `plan_id` unique |
| `migration_runs` | Dev 2 | `run_id` unique, `(state, created_at)` |
| `run_events` | Dev 2 | `(run_id, sequence)` unique |
| `counters` | Dev 2 | `_id` |
| `behaviour_cases` | Dev 4 | `(repository_id, unit_id)`, `case_id` unique per repo |
| `verification_results` | Dev 4 | `verification_id` unique, `(run_id, unit_id)` |
| `counterexamples` | Dev 4 | `counterexample_id` unique, `(run_id, unit_id, status)` |
| `artefacts` | Dev 2 | `artefact_id` unique, `run_id` (metadata + path + sha256 only, no blobs) |

If MongoDB is unreachable, endpoints return 503 `DATABASE_UNAVAILABLE`. No in-memory fallback.

---

## 10. Environment (`.env.example`)

```dotenv
GEMINI_API_KEY=
GEMINI_MODEL=gemini-4-argon
GEMINI_THINKING_LEVEL=medium
GEMINI_EMBEDDING_MODEL=            # verify available model + dimensions at kickoff
EMBEDDING_DIMENSIONS=
MONGODB_URI=
MONGODB_DATABASE=axolotl
RUNNER_MODE=trusted_docker         # trusted_docker | disabled
RUNNER_IMAGE=axolotl-runner:latest
RUNNER_IMAGE_PY27=axolotl-runner:py2.7
RUNNER_IMAGE_PY314=axolotl-runner:py3.14
ALLOW_ARBITRARY_CODE_EXECUTION=false
WORKSPACE_ROOT=./.axolotl_workspaces
FRONTEND_ORIGIN=http://localhost:5173
MAX_PATCH_ATTEMPTS=3
MAX_TOOL_CALLS_PER_UNIT=25
```

---

## 11. Changing a contract

1. Post the proposed change in the team channel tagging affected owners.
2. Open a PR that edits this file **and** `contracts.py` / `contracts.ts` together.
3. Dev 2 merges after affected owners ack. Additive optional fields can merge immediately; renames/removals need acks.

---

## 12. Integration gates (whole team)

| Time (London) | Gate — demonstrable on `main` |
|---|---|
| Fri 18:00 | Skeleton boots: `GET /system/status` returns real Mongo status; frontend renders it |
| Fri 21:00 | Import demo fixture → real graph in Atlas → rendered in UI; one real baseline case executed in the sandbox |
| Sat 11:00 | Argon produces a real patch for one unit → verifier runs it → result shown in timeline; first real repair attempt |
| Sat 14:00 | Two units verified in dependency order; Atlas keyword + vector search live in UI |
| Sat 17:00 | Full cold-start E2E dry run, all stubs deleted |
| Sat 22:00 | **Feature freeze.** Bug fixes only |
| Sun 10:45 | Internal submission complete (official cutoff 12:00) |

Scope cuts follow Spec §13 "Milestone gates and scope cuts".

Branching: short-lived `devN/<topic>` branches, PR into `main`, `main` must always boot. Merge at least every 3 hours.


### Additive runtime inspection endpoint (review proposal, Dev 1 chess UI)

`GET /repositories/{repo_id}/runtime` reads package declarations only and returns
`{declared_python: string|null, evidence: string[], runtime_verified: false, note: string}`.
This does not alter RepositoryRecord or enable Python 2 execution. Implementation and
UI are on the chess demo branch for integration-owner review before merge.

### Additive: `search_index` on `RepositoryRecord` (Dev 3 proposed, Dev 2 acked 24 Oct)

Agreed, additive and optional, so it merges immediately under §11.3:

```python
search_index: dict | None = None   # {state, embedded, total} or None
```

**Composed on read, never stored.** Three conditions, and the reasons matter more
than the field:

- `GET /repositories/{repo_id}` fills it from `search_index_status`. The Overview reads
  one repository, which is the case Dev 3 raised, and it gets the value without a
  second call.
- `GET /repositories` leaves it `null`. `progress.get()` is one query per repository
  **and writes** when it finds indexing that a restart interrupted, so filling the list
  would turn one page load into N reads and up to N writes.
- Nothing writes it into the `repositories` document. A record is written once at
  import and indexing finishes after that, so a persisted copy would be wrong within
  seconds and wrong for good.

`GET /repositories/{repo_id}/index` stays the authority for live progress: it is the
endpoint to poll, and the only one that reports `reason` for a failure.

### Additive: three changes already on `main`, acked (Dev 2, 24 Oct)

Recorded because §11.2 says the doc and the code move together, and these moved
ahead of the doc.

**`folder_upload` as a `RepositoryRecord.origin`** (Dev 3/Dev 5, for the chess upload
screen). Additive to the Literal, so it merges immediately. It carries no new
execution rights: a folder upload is `executable=False` like a ZIP unless it matches a
trusted tree exactly (`app/ingestion/trust.py`).

**`search_repository` returns a dict, not `list[SearchHit]`.** §6 has said
`{mode_used, native_hybrid, hits}` since the search layer landed; §8 above said
`list[SearchHit]` until now, and the signature is corrected rather than the code,
because the caller needs both of the extra fields: `mode_used` to say which engine
answered when hybrid degrades, and `native_hybrid` to say whether `$rankFusion` ran or
a Python fallback did. Dev 1's tool dispatcher already handles the dict (and keeps a
list branch it can now drop, `app/agents/tools.py`).

**§6.2's state diagram vs the `RunState` enum.** The Spec's diagram has `parsing`,
`planning` and `awaiting_approval`, which the enum does not, and the enum has
`cancelled`, which the diagram does not. Resolved in favour of the enum, per §0:
contracts win on names and shapes. `skipped` is reachable from every in-flight unit
state and carries both meanings - never attempted (T-13) and abandoned on
cancellation - which the diagram does not distinguish either.

### Additive: `VerificationResult.environment` and one runner image per interpreter (Dev 1, #22)

Additive and optional, so it merges immediately under §11.3:

```python
environment: dict[str, str] | None = None   # {python, image, image_digest, runner_mode}
```

It records what actually produced the verdict: the interpreter version **probed from
inside the container**, the image tag and its digest. `None` on records written before
#22 and on inconclusive results where nothing ran. `BaselineSummary.environment` keeps
its shape and gains `image` and `candidate_python`; its `python` is now probed too,
where it used to be the constant `"3.12"`.

Two settings join §10, both with defaults: `RUNNER_IMAGE_PY27` and `RUNNER_IMAGE_PY314`.
`python2_to_3` baselines on 2.7 and verifies on 3.14; every other target keeps
`RUNNER_IMAGE` (3.12) on both sides.

**For Dev 5:** `frontend/src/types/contracts.ts` should gain
`environment?: Record<string, string> | null` on `VerificationResult` to mirror this.

### Additive: `unit_skipped`, and the attempt budget on `RunSummary` (Dev 5 asked, Dev 2 acked 24 Oct)

Both additive, so they merge immediately under §11.3.

**`unit_skipped`** separates "not attempted" from "tried and could not be verified".
Every unit abandoned downstream of a block, by a cancellation or by a restart used to
be journalled as `unit_blocked`, the same type as the block that caused it, so the Run
screen rendered one real failure as four red rows. `unit_blocked` now means only the
second thing. Clients that match on `unit_blocked` keep working and simply stop seeing
the skips; a client that does not know `unit_skipped` yet shows nothing for them, which
is better than showing them as failures.

**`RunSummary.max_patch_attempts`** records the budget the run actually ran under, so
the Run screen's header can say "Attempt 2 of 3". Read from config at run creation
rather than at display time, so a finished run keeps its own budget if the setting
changes later.

**And a decision Dev 5 asked for: a cancelled run always gets a report.** It used to
depend on when the user clicked - cancelling during final verification produced one,
cancelling during baselining or unit migration did not. A cancelled run has usually
verified some units; that work is real and the report is the record of it, verdict and
NOT-A-PROOF statement included. The Report screen can rely on a report existing for any
terminal run.

### Additive: chess is a built-in fixture with one id per content (Dev 1, #81–#84)

No model changes. Three behaviours:

- **`GET /demo-fixtures` lists `chess` first**, then `legacy_orders`.
- **A built-in fixture's `repository_id` is deterministic:** `repo_` + 12 hex, as
  before, derived from the fixture id and its line-ending-normalised tree hash. Importing
  the same fixture again returns the existing `RepositoryRecord`. Uploads keep random
  ids.
- **`snapshot_path` rebuilds a missing built-in fixture snapshot** from the checkout,
  written with LF line endings so the bytes, and `snapshot_hash`, are the same on every
  laptop. Uploads are still only on the machine that imported them (#26).

And one error on two more endpoints: `POST /repositories/{id}/plans` and
`POST /plans/{id}/runs` answer `409 SNAPSHOT_UNAVAILABLE` before any model call when this
machine has no copy of the code.

### Additive: `POST /repositories/{id}/ask` (Dev 2, #123)

A new endpoint. No change to any existing model, response or error, so nothing that
exists today has to move.

```
POST /repositories/{repository_id}/ask
  { "question": "where is the rounding done?",
    "history": [{"role": "user"|"assistant", "content": "..."}] }   # optional

200 { "answer": "...",
      "grounded": true,
      "citations": [{"path": "pricing.py", "start_line": 41, "end_line": 58,
                     "symbol": "pricing.finalise_amount", "snippet": "..."}],
      "retrieval": [{"tool": "search_repository", "query": "rounding",
                     "mode_used": "hybrid", "native_hybrid": true, "hit_count": 8}],
      "model": "gemini-4-argon",
      "truncated": false }
```

Errors are the §6 envelope: `404 NOT_FOUND` for an unknown repository, raised before
any model call; `503 MODEL_UNAVAILABLE` when Gemini cannot be reached; `422` for a
malformed request. A question is never answered from general knowledge because the
model was unreachable.

Three things the shape commits to, which consumers can rely on:

- **`grounded` is false when retrieval returned no hits**, and the answer says it could
  not find anything. Searching and finding nothing is not the same as not searching, so
  `retrieval` is still populated in that case with `hit_count: 0`.
- **`citations` are read off the tool results**, not asked of the model. A model invited
  to write its own citations can cite a file it never opened.
- **`retrieval` is part of the contract, not a debug field.** It is how the UI shows
  which engine answered and whether `$rankFusion` ran.

`history` lives in the request payload only. Nothing is stored, so there is no new
collection and no conversation state to keep in step.

**Tool surface.** The endpoint reuses the migration agent's own tools with the
controller removed, limited to the five that need nothing but a repository id:
`get_repository_overview`, `get_dependency_graph`, `get_module_source`, `get_symbol`,
`search_repository`. `propose_patch`, `run_verification` and `finish_unit` are excluded
because a chat must not change anything; `get_counterexamples` because it is derived
from held-out failures; `get_behaviour_cases` because it needs a unit, which a question
does not have. `backend/tests/unit/test_ask_allowlist.py` pins that set, so widening it
is a decision rather than a detail. **Dev 1**: `app/agents/` is your lane - the new
module is `app/agents/ask.py` and it subclasses `ToolDispatcher` rather than changing
it, but please say if you would rather own it.

### Additive: module documentation (Dev 1, #39)

Three endpoints and two collections, all new, so they merge immediately under §11.3:

- `GET /repositories/{id}/docs`, `GET /runs/{id}/docs`, `POST /repositories/{id}/docs`
  (202). Shapes in `docs/api-contracts.md`.
- `module_docs`: one cached purpose per `(repository_id, path, source_hash)`.
- `docs_generation`: one status document per repository.
- One error code: `409 DOCS_GENERATION_ACTIVE`.

Facts are computed from the trees on request and never stored, like #34.

**For Dev 5 (#130):** each module has `legacy` and, on a run, `migrated`, each with
structured `facts` and a ready-made `markdown`. Model-written text is labelled in both
(`purpose.basis: model_generated`, and Argon's `change.summary`).

### Additive: migration summary (Dev 1, #35)

One new endpoint, `GET /runs/{run_id}/summary`, so it merges immediately under §11.3.
Nothing is stored and no model is called: it is computed from the `RunReport` and the
run's journal, plus the diff and #34's tree comparison when the trees are on this
machine. Shape in `docs/api-contracts.md`.

**For Dev 5 (#129):** per unit, `argon_summary` carries `basis: "model_written"` and
`accepted`; show it labelled. `caveats` and `limitations` are the report's own, to be
shown uncollapsed. `lines` is `null`, not 0, when the trees are elsewhere.
