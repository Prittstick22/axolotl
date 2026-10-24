/**
 * TypeScript mirror of SHARED_CONTRACTS §4 - owner: Dev 5.
 *
 * Field names are identical to the Pydantic models in
 * `backend/app/models/contracts.py` (snake_case, deliberately).
 * Types only, no logic. Change only through the process in SHARED_CONTRACTS §11.
 */

// ---------- enums ----------
export type NodeKind = 'local_module' | 'external_package' | 'test_module';

export type EdgeType =
  | 'imports'
  | 'from_imports'
  | 'defines'
  | 'inherits'
  | 'calls'
  | 'test_targets'
  | 'unknown_dynamic';

export type Resolution = 'confirmed' | 'heuristic' | 'unresolved';

export type SymbolKind =
  | 'module'
  | 'class'
  | 'function'
  | 'async_function'
  | 'method'
  | 'nested_function';

export type UnitStatus =
  | 'not_planned'
  | 'queued'
  | 'baselining'
  | 'migrating'
  | 'verifying'
  | 'repairing'
  | 'passed'
  | 'blocked'
  | 'failed'
  | 'skipped';

export type RunState =
  | 'queued'
  | 'baselining'
  | 'migrating'
  | 'verifying'
  | 'repairing'
  | 'final_verification'
  | 'completed'
  | 'completed_with_warnings'
  | 'failed'
  | 'cancelled';

export type PlanStatus = 'draft' | 'approved' | 'rejected' | 'superseded';
export type Risk = 'low' | 'medium' | 'high';

export type GateName =
  | 'compile'
  | 'existing_tests'
  | 'differential'
  | 'impacted_tests'
  | 'final_integration'
  | 'sandbox_policy';

export type GateStatus = 'passed' | 'failed' | 'inconclusive' | 'skipped';
export type SearchMode = 'keyword' | 'vector' | 'hybrid';

// ---------- repository / graph ----------
export interface RepositoryRecord {
  repository_id: string;
  name: string;
  snapshot_hash: string;
  origin: 'demo_fixture' | 'zip_upload' | 'folder_upload';
  executable: boolean;
  demo_fixture_id: string | null;
  file_count: number;
  module_count: number;
  symbol_count: number;
  test_file_count: number;
  analysis_status: 'ok' | 'partial' | 'failed';
  warnings: string[];
  created_at: string;
  /**
   * Additive under SHARED_CONTRACTS §11. Filled by GET /repositories/{id} only, so
   * the Overview needs no second call; null on GET /repositories and null when the
   * repository was never indexed. GET /repositories/{id}/index is the authority for
   * live progress and the only place a failure `reason` appears.
   */
  search_index?: { state: 'indexing' | 'ready' | 'failed'; embedded: number; total: number | null } | null;
}

/** GET /demo-fixtures */
export interface DemoFixture {
  demo_fixture_id: string;
  name: string;
  description: string;
}

/** GET /repositories/{id}/runtime - additive (§11, Dev 1). Package metadata only, nothing executed. */
export interface RuntimeInfo {
  declared_python: string | null;
  evidence: string[];
  runtime_verified: false;
  note: string;
}

/** GET /repositories/{id}/index - additive (#28, Dev 3). Background descriptions and embeddings; state null if none was recorded. */
export interface IndexStatus {
  repository_id: string;
  state: 'indexing' | 'ready' | 'failed' | null;
  embedded?: number;
  /** null while a manual re-index is still counting its chunks */
  total?: number | null;
  reason?: string | null;
}

export interface GraphNode {
  id: string;
  label: string;
  kind: NodeKind;
  path: string | null;
  status: UnitStatus;
  unit_id: string | null;
  function_count: number;
  class_count: number;
  warnings: string[];
}

export interface GraphEdge {
  id: string;
  /** importer */
  source: string;
  /** dependency - A -> B means A depends on B */
  target: string;
  type: EdgeType;
  resolution: Resolution;
  basis: 'static_ast' | 'heuristic';
  source_path: string | null;
  source_line: number | null;
}

export interface GraphPayload {
  repository_id: string;
  snapshot_hash: string;
  level: 'module';
  run_id: string | null;
  nodes: GraphNode[];
  edges: GraphEdge[];
  warnings: string[];
}

export interface SymbolRecord {
  repository_id: string;
  snapshot_hash: string;
  symbol_id: string;
  kind: SymbolKind;
  qualified_name: string;
  module_id: string;
  path: string;
  start_line: number;
  end_line: number;
  signature: string | null;
  decorators: string[];
  bases: string[];
  /** untrusted data - never render as HTML */
  docstring: string | null;
  parent_symbol_id: string | null;
  source_chunk_id: string | null;
  analysis_warnings: string[];
}

export interface ModuleDetail {
  module_id: string;
  path: string;
  symbols: SymbolRecord[];
  /** outbound */
  imports: GraphEdge[];
  /** inbound */
  dependants: GraphEdge[];
  source: string;
  warnings: string[];
  test_case_count: number;
}

export interface SearchHit {
  chunk_id: string;
  symbol_id: string | null;
  module_id: string;
  path: string;
  start_line: number;
  end_line: number;
  snapshot_hash: string;
  snippet: string;
  description: string | null;
  match_method: 'keyword' | 'vector' | 'hybrid';
  score: number;
  rank: number;
  keyword_rank: number | null;
  vector_rank: number | null;
  /** additive (§11): which function or class the hit is in; null when not filled */
  qualified_name?: string | null;
  kind?: SymbolKind | null;
}

/** GET /repositories/{id}/search. Hybrid falls back to keyword when vector search is unavailable, so mode_used can differ from the mode asked for. */
export interface SearchResults {
  mode_used: SearchMode;
  native_hybrid: boolean;
  hits: SearchHit[];
}

export interface DependencyChangeItem {
  name: string;
  basis: 'static_ast' | 'py2_rename_table' | 'name_match';
}

export interface DependencyReplacement {
  before: string;
  after: string;
  basis: 'py2_rename_table' | 'name_match';
}

export interface DependencyModuleChange {
  module_id: string;
  path: string;
  status: 'added' | 'removed' | 'changed';
  imports_added: DependencyChangeItem[];
  imports_removed: DependencyChangeItem[];
  stdlib_renamed: DependencyReplacement[];
  builtins_replaced: DependencyReplacement[];
  methods_replaced: DependencyReplacement[];
}

export interface DependencyEdgeChange {
  source: string;
  target: string;
  type: EdgeType;
}

export interface DependencyChanges {
  run_id: string;
  repository_id: string;
  base_revision: string;
  revision: string | null;
  modules: DependencyModuleChange[];
  edges: { added: DependencyEdgeChange[]; removed: DependencyEdgeChange[]; basis?: string };
  packages: { added: string[]; removed: string[]; kept: string[]; basis?: string; note?: string };
  python: {
    before: { declared: string | null; evidence: string[] };
    after: { declared: string | null; evidence: string[] };
    basis: string;
  } | null;
  warnings: string[];
}

// ---------- planning ----------
export interface MigrationTarget {
  target_id: string;
  label: string;
  supported: boolean;
  description: string;
  unsupported_reason: string | null;
  /** additive (§11, #21): the Python version the target produces, such as "3.14" */
  target_python?: string | null;
}

export interface MigrationUnit {
  unit_id: string;
  module_ids: string[];
  /** the ONLY files a patch may touch for this unit */
  file_paths: string[];
  depends_on_unit_ids: string[];
  dependant_unit_ids: string[];
  is_cycle: boolean;
  risk: Risk;
  risk_factors: string[];
  visible_case_count: number;
}

export interface MigrationPlan {
  plan_id: string;
  repository_id: string;
  snapshot_hash: string;
  target_id: string;
  units: MigrationUnit[];
  order: string[];
  rationale: string;
  risks: string[];
  coverage_gaps: string[];
  status: PlanStatus;
  model: string;
  interaction_id: string | null;
  created_at: string;
  approved_at: string | null;
}

export interface PlanProposal {
  suggested_order: string[];
  rationale: string;
  risks: string[];
  coverage_gaps: string[];
  per_unit_notes: Record<string, string>;
  interaction_id: string | null;
}

// ---------- behaviour & verification ----------
export interface BehaviourCase {
  case_id: string;
  repository_id: string;
  unit_id: string;
  function: string;
  /** encoded per SHARED_CONTRACTS §5 */
  args: unknown[];
  kwargs: Record<string, unknown>;
  visibility: 'visible' | 'held_out';
  exposed_to_model: boolean;
  description: string | null;
  provenance: string;
  case_hash: string;
}

export interface CaseOutcome {
  case_id: string;
  status: 'ok' | 'raised' | 'timeout' | 'harness_error';
  /** canonical JSON per SHARED_CONTRACTS §5 */
  value: unknown;
  exception_type: string | null;
  exception_message: string | null;
  /** median call time inside the harness, warm-up excluded */
  duration_ns: number | null;
  duration_samples_ns?: number[] | null;
}

export interface CaseComparison {
  case_id: string;
  visibility: 'visible' | 'held_out';
  verdict: 'match' | 'mismatch' | 'inconclusive';
  old: CaseOutcome;
  new: CaseOutcome;
  /** set by the unit endpoint on a sealed held-out case: the outcomes' values are a placeholder */
  redacted?: boolean;
}

export interface GateResult {
  gate: GateName;
  status: GateStatus;
  required: boolean;
  summary: string;
  passed: number;
  failed: number;
  inconclusive: number;
  log_excerpt: string;
}

export interface VerificationResult {
  verification_id: string;
  run_id: string;
  /** null = final whole-project verification */
  unit_id: string | null;
  attempt: number;
  workspace_revision: string;
  gates: GateResult[];
  comparisons: CaseComparison[];
  counterexample_ids: string[];
  all_required_gates_passed: boolean;
  runner_mode: string;
  started_at: string;
  finished_at: string;
}

export interface Counterexample {
  counterexample_id: string;
  run_id: string;
  unit_id: string;
  attempt: number;
  case_id: string;
  visibility: 'visible' | 'held_out';
  exposed_to_model: boolean;
  function: string;
  args: unknown[];
  kwargs: Record<string, unknown>;
  old: CaseOutcome;
  new: CaseOutcome;
  status: 'open' | 'resolved' | 'unresolved';
  resolved_by_verification_id: string | null;
  /** set by the unit endpoint on a sealed held-out case: function, args, kwargs, old and new are a placeholder string */
  redacted?: boolean;
}

export interface BaselineSummary {
  run_id: string;
  snapshot_hash: string;
  unit_ids: string[];
  cases_captured: number;
  cases_failed_to_capture: number;
  existing_tests: GateResult;
  environment: Record<string, string>;
}

// ---------- workspaces & patches ----------
export interface FilePatch {
  path: string;
  new_content: string;
}

export interface PatchProposal {
  unit_id: string;
  files: FilePatch[];
  summary: string;
  rationale: string;
}

export interface PatchAck {
  accepted: boolean;
  workspace_revision: string | null;
  errors: string[];
}

export interface Workspace {
  workspace_id: string;
  run_id: string;
  root: string;
  base_revision: string;
  revision_id: string;
}

// ---------- runs & events ----------
export interface UnitRunState {
  unit_id: string;
  module_ids: string[];
  status: UnitStatus;
  attempts: number;
  last_verification_id: string | null;
  accepted_revision: string | null;
}

export interface RunSummary {
  run_id: string;
  plan_id: string;
  repository_id: string;
  state: RunState;
  current_unit: string | null;
  units: UnitRunState[];
  units_total: number;
  units_verified: number;
  units_blocked: number;
  tests_passed: number;
  tests_failed: number;
  tests_inconclusive: number;
  last_event_sequence: number;
  model: string;
  runner_mode: string;
  created_at: string;
  updated_at: string;
  error: string | null;
  /**
   * The patch-attempt budget this run ran under, for headers like "Attempt 2 of 3"
   * (SHARED_CONTRACTS §11, #27). Recorded per run rather than read from config at
   * display time, so a finished run keeps its own budget. Null for runs started
   * before this field existed.
   */
  max_patch_attempts?: number | null;
}

export interface RunEvent {
  run_id: string;
  /** unique, monotonic per run, starts at 1 */
  sequence: number;
  timestamp: string;
  /** see SHARED_CONTRACTS §7 */
  event_type: string;
  unit_id: string | null;
  attempt: number | null;
  payload: Record<string, unknown>;
}

export interface UnitTiming {
  unit_id: string;
  cases_timed: number;
  /** median over cases, old code */
  before_median_us: number;
  /** same cases, accepted revision */
  after_median_us: number;
  /** |after - before| <= the larger IQR of the two */
  within_noise: boolean;
}

export interface PerformanceSummary {
  method: string;
  /** units with at least one timed case; the rest are untimed */
  units: UnitTiming[];
}

export interface RunReport {
  run_id: string;
  verdict: 'completed' | 'completed_with_warnings' | 'failed' | 'cancelled';
  units: UnitRunState[];
  final_verification_id: string | null;
  changed_files: string[];
  tests_passed: number;
  tests_failed: number;
  tests_inconclusive: number;
  counterexamples_found: number;
  counterexamples_resolved: number;
  baseline_snapshot_hash: string;
  final_artefact_hash: string | null;
  coverage_caveats: string[];
  limitations: string[];
  /** Argon summary grounded in recorded evidence - always label as model-generated */
  narrative: string | null;
  artefact_ids: string[];
  generated_at: string;
  /** runtime before/after; null until timed - hide the Runtime panel */
  performance: PerformanceSummary | null;
}

// ---------- GET /runs/{run_id}/artefacts ----------
export interface Artefact {
  artefact_id: string;
  kind: 'patch' | 'archive' | 'report';
  sha256: string;
  /** Rooted at the API's origin: /api/v1/artefacts/{id}/download */
  download_url: string;
}

// ---------- GET /runs/{run_id}/units/{unit_id} ----------
export interface UnitDetail {
  unit: UnitRunState;
  verifications: VerificationResult[];
  counterexamples: Counterexample[];
}

// ---------- GET /runs/{run_id}/diff ----------
export interface FileDiff {
  path: string;
  old_content: string;
  new_content: string;
  unified_diff: string;
}

export interface RunDiff {
  base_revision: string;
  /** null until a revision is accepted */
  revision: string | null;
  files: FileDiff[];
}

// ---------- GET /system/status (SHARED_CONTRACTS §6) ----------
export type DependencyStatus = 'ok' | 'unavailable' | 'building';

export interface SystemStatus {
  mongodb: DependencyStatus;
  search_indexes: { keyword: DependencyStatus; vector: DependencyStatus };
  runner: { mode: string; available: boolean };
  gemini: { model: string; reachable: boolean };
  /** additive, non-contract diagnostics; safe to ignore */
  details?: Record<string, string>;
}

// ---------- error envelope (SHARED_CONTRACTS §6) ----------
export interface ApiErrorBody {
  error: { code: string; message: string; details: Record<string, unknown> };
}
