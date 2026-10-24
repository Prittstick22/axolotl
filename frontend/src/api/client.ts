/**
 * Typed fetch wrapper - owner: Dev 5 (seeded by Dev 2 for the skeleton gate).
 *
 * Parses the SHARED_CONTRACTS §6 error envelope into a thrown `ApiError` so callers and
 * TanStack Query see a real failure rather than a shape they have to sniff.
 */

import type { FolderFile } from '../lib/folder';
import type {
  ApiErrorBody,
  Artefact,
  DemoFixture,
  GraphPayload,
  IndexStatus,
  MigrationPlan,
  MigrationTarget,
  ModuleDetail,
  RepositoryRecord,
  RunDiff,
  RunEvent,
  RunReport,
  RunSummary,
  RuntimeInfo,
  SearchMode,
  SearchResults,
  SystemStatus,
  UnitDetail,
} from '../types/contracts';

export const API_BASE: string =
  (import.meta.env.VITE_API_BASE as string | undefined) ?? 'http://localhost:8000/api/v1';

export class ApiError extends Error {
  readonly code: string;
  readonly status: number;
  readonly details: Record<string, unknown>;

  constructor(code: string, message: string, status: number, details: Record<string, unknown>) {
    super(message);
    this.name = 'ApiError';
    this.code = code;
    this.status = status;
    this.details = details;
  }
}

function isErrorEnvelope(value: unknown): value is ApiErrorBody {
  if (typeof value !== 'object' || value === null || !('error' in value)) return false;
  const inner = (value as { error: unknown }).error;
  return typeof inner === 'object' && inner !== null && 'code' in inner && 'message' in inner;
}

export async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const url = `${API_BASE}${path.startsWith('/') ? path : `/${path}`}`;

  let response: Response;
  try {
    response = await fetch(url, {
      ...init,
      headers: { Accept: 'application/json', ...(init?.headers ?? {}) },
    });
  } catch (cause) {
    // Network-level failure: the backend is not running, or CORS blocked the request.
    throw new ApiError('NETWORK_ERROR', `Could not reach ${url}`, 0, {
      cause: String(cause),
    });
  }

  const text = await response.text();
  let body: unknown = null;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    // Not our API: a proxy or gateway page. An error status is reported below as HTTP_ERROR.
    if (response.ok) {
      throw new ApiError('HTTP_ERROR', 'The server replied with something that is not JSON', response.status, {});
    }
  }

  if (!response.ok) {
    if (isErrorEnvelope(body)) {
      throw new ApiError(
        body.error.code,
        body.error.message,
        response.status,
        body.error.details ?? {},
      );
    }
    throw new ApiError('HTTP_ERROR', `${response.status} ${response.statusText}`, response.status, {});
  }

  return body as T;
}

/** GET /api/v1/health - liveness only. */
export function getHealth(): Promise<{ status: string }> {
  return apiFetch<{ status: string }>('/health');
}

/** GET /api/v1/system/status - SHARED_CONTRACTS §6. */
export function getSystemStatus(): Promise<SystemStatus> {
  return apiFetch<SystemStatus>('/system/status');
}

const repo = (repoId: string) => `/repositories/${encodeURIComponent(repoId)}`;

/** GET /api/v1/demo-fixtures - SHARED_CONTRACTS §6. */
export function listDemoFixtures(): Promise<DemoFixture[]> {
  return apiFetch<DemoFixture[]>('/demo-fixtures');
}

/** POST /api/v1/repositories/import - a fixture as JSON, a folder as multipart `files` with one `paths` entry each, in order. */
export function importRepository(source: { files: FolderFile[] } | { demo_fixture_id: string }, name?: string): Promise<RepositoryRecord> {
  if ('files' in source) {
    const body = new FormData();
    for (const { path, file } of source.files) {
      body.append('files', file);
      body.append('paths', path);
    }
    if (name) body.append('name', name);
    return apiFetch<RepositoryRecord>('/repositories/import', { method: 'POST', body });
  }
  return apiFetch<RepositoryRecord>('/repositories/import', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ...source, name }),
  });
}

/** GET /api/v1/repositories/{repo_id} - SHARED_CONTRACTS §6. */
export function getRepository(repoId: string): Promise<RepositoryRecord> {
  return apiFetch<RepositoryRecord>(repo(repoId));
}

/** GET /api/v1/repositories/{repo_id}/graph - with a run, node status is that run's unit status. */
export function getGraph(repoId: string, runId?: string | null): Promise<GraphPayload> {
  return apiFetch<GraphPayload>(`${repo(repoId)}/graph${runId ? `?run_id=${encodeURIComponent(runId)}` : ''}`);
}

/** GET /api/v1/repositories/{repo_id}/modules/{module_id} - SHARED_CONTRACTS §6. */
export function getModule(repoId: string, moduleId: string): Promise<ModuleDetail> {
  return apiFetch<ModuleDetail>(`${repo(repoId)}/modules/${encodeURIComponent(moduleId)}`);
}

/** GET /api/v1/repositories/{repo_id}/runtime - declared Python version (§11 addition). */
export function getRuntime(repoId: string): Promise<RuntimeInfo> {
  return apiFetch<RuntimeInfo>(`${repo(repoId)}/runtime`);
}

/** GET /api/v1/repositories/{repo_id}/index - search indexing progress after an import (#28). */
export function getIndexStatus(repoId: string): Promise<IndexStatus> {
  return apiFetch<IndexStatus>(`${repo(repoId)}/index`);
}

/** GET /api/v1/repositories/{repo_id}/search - 503 SEARCH_INDEX_NOT_READY while an index builds, never an empty result. */
export function searchRepository(repoId: string, q: string, mode: SearchMode): Promise<SearchResults> {
  return apiFetch<SearchResults>(`${repo(repoId)}/search?${new URLSearchParams({ q, mode })}`);
}

const post = <T>(path: string, body?: unknown) =>
  apiFetch<T>(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body ?? {}) });
const plan = (planId: string) => `/plans/${encodeURIComponent(planId)}`;
const run = (runId: string) => `/runs/${encodeURIComponent(runId)}`;

/** GET /api/v1/migration-targets - unsupported targets come back with a reason. */
export function listTargets(): Promise<MigrationTarget[]> {
  return apiFetch<MigrationTarget[]>('/migration-targets');
}

/** POST /api/v1/repositories/{repo_id}/plans - Argon orders the units; 409 for static-only repositories. */
export function createPlan(repoId: string, targetId: string): Promise<MigrationPlan> {
  return post<MigrationPlan>(`${repo(repoId)}/plans`, { target_id: targetId });
}

export function getPlan(planId: string): Promise<MigrationPlan> {
  return apiFetch<MigrationPlan>(plan(planId));
}

export function approvePlan(planId: string): Promise<MigrationPlan> {
  return post<MigrationPlan>(`${plan(planId)}/approve`, { approved: true });
}

/** POST /api/v1/plans/{plan_id}/runs - 409 PLAN_NOT_APPROVED until approved. */
export function startRun(planId: string): Promise<RunSummary> {
  return post<RunSummary>(`${plan(planId)}/runs`);
}

export function getRun(runId: string): Promise<RunSummary> {
  return apiFetch<RunSummary>(run(runId));
}

export function cancelRun(runId: string): Promise<RunSummary> {
  return post<RunSummary>(`${run(runId)}/cancel`);
}

/** GET /api/v1/runs/{run_id}/events - only events after `afterSequence`, oldest first. */
export function listRunEvents(runId: string, afterSequence: number): Promise<RunEvent[]> {
  return apiFetch<RunEvent[]>(`${run(runId)}/events?after_sequence=${afterSequence}`);
}

/** GET /api/v1/runs/{run_id}/report - 404 REPORT_NOT_READY until the worker writes it. */
export function getReport(runId: string): Promise<RunReport> {
  return apiFetch<RunReport>(`${run(runId)}/report`);
}

export function listArtefacts(runId: string): Promise<Artefact[]> {
  return apiFetch<Artefact[]>(`${run(runId)}/artefacts`);
}

/** GET /api/v1/runs/{run_id}/units/{unit_id} - sealed held-out cases come back redacted. */
export function getRunUnit(runId: string, unitId: string): Promise<UnitDetail> {
  return apiFetch<UnitDetail>(`${run(runId)}/units/${encodeURIComponent(unitId)}`);
}

/** GET /api/v1/runs/{run_id}/diff?unit_id= - baseline against an accepted revision, the unit's files only. */
export function getRunDiff(runId: string, unitId: string): Promise<RunDiff> {
  return apiFetch<RunDiff>(`${run(runId)}/diff?unit_id=${encodeURIComponent(unitId)}`);
}
