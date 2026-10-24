"""Domain models - owner: Dev 2. Transcribed verbatim from SHARED_CONTRACTS §4.

Everyone imports from this module. TypeScript mirrors live in
`frontend/src/types/contracts.ts` (Dev 5) and must use identical snake_case field names.

Change this file only through the process in SHARED_CONTRACTS §11.
"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field  # noqa: F401  (Field declared by §4; kept for fidelity)

# ---------- enums ----------
NodeKind = Literal["local_module", "external_package", "test_module"]
EdgeType = Literal[
    "imports", "from_imports", "defines", "inherits", "calls", "test_targets", "unknown_dynamic"
]
Resolution = Literal["confirmed", "heuristic", "unresolved"]
SymbolKind = Literal["module", "class", "function", "async_function", "method", "nested_function"]

UnitStatus = Literal[
    "not_planned", "queued", "baselining", "migrating", "verifying",
    "repairing", "passed", "blocked", "failed", "skipped",
]
RunState = Literal[
    "queued", "baselining", "migrating", "verifying", "repairing",
    "final_verification", "completed", "completed_with_warnings",
    "failed", "cancelled",
]
PlanStatus = Literal["draft", "approved", "rejected", "superseded"]
Risk = Literal["low", "medium", "high"]
GateName = Literal[
    "compile", "existing_tests", "differential", "impacted_tests",
    "final_integration", "sandbox_policy",
]
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
    # Additive under §11 (Dev 3 proposed, Dev 2 acked 24 Oct): {state, embedded,
    # total}, so the Overview does not need a second call. **Composed on read by
    # GET /repositories/{id} and never stored** - a record is written once at import
    # and indexing finishes after that, so a persisted copy would be stale within
    # seconds. GET /repositories leaves it None; see SHARED_CONTRACTS §11.
    search_index: dict[str, Any] | None = None


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
    # Additive under §11 (optional, defaults to None, breaks no consumer). §4 gives a
    # hit a path and a line range but no name, so a result list would read
    # "pricing.py:48-104" with no way to say which function that is without a second
    # request per hit. Dev 3 fills it; Dev 5 may ignore it.
    qualified_name: str | None = None
    kind: SymbolKind | None = None


# ---------- planning ----------
class MigrationTarget(BaseModel):
    target_id: str                   # "modern_python3"
    label: str
    supported: bool
    description: str
    unsupported_reason: str | None = None
    # Additive under §11 (optional, defaults to None, breaks no consumer). The Plan
    # screen shows "Python 2.7 (declared) -> Python 3.14 (target)"; without this it
    # had "Python 3.14" written into the page. The declared side comes from the
    # repository's own runtime metadata, so only the target belongs here.
    target_python: str | None = None


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
    within_noise: bool               # |after - before| <= the larger IQR of the two


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
