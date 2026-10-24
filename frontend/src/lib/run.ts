import { useQuery } from '@tanstack/react-query';

import { ApiError, getRun } from '@/api/client';
import type { RunState, RunSummary, UnitRunState } from '@/types/contracts';

/** Spec §9.3: poll every 1.5 s, and only while the run is non-terminal. */
export const POLL_MS = 1500;
// ponytail: a run that ends with no closing event (a backend restart) stops being polled after this.
export const CLOSING_WAIT_MS = 120_000;
const TERMINAL: ReadonlySet<RunState> = new Set(['completed', 'completed_with_warnings', 'failed', 'cancelled']);
export const isLive = (run?: RunSummary) => !run || !TERMINAL.has(run.state);

/**
 * The attempt a unit is on. The worker writes `attempts` only when the unit ends, so while it runs
 * the highest attempt in its events (`patched`) stands in. A repair is working on the next one.
 */
export function attemptOf(u: UnitRunState, patched = 0) {
  const n = u.attempts || patched;
  return u.status === 'repairing' ? n + 1 : Math.max(n, 1);
}

/** One query per run, shared by the app bar's progress, the run screen and the report. A failed poll keeps the last data. */
export function useRun(runId: string | null) {
  return useQuery({
    queryKey: ['run', runId],
    queryFn: () => getRun(runId!),
    enabled: Boolean(runId),
    // A run that isn't there (404) won't turn up by asking again. Before the first load, an answered error (a 503 with
    // Atlas down) isn't retried either: TanStack clears it on each try, so "Loading" would hide it for Atlas's 5 s timeout.
    refetchInterval: (q) => {
      const e = q.state.error instanceof ApiError ? q.state.error : undefined;
      return isLive(q.state.data) && !(e && (e.status === 404 || (!q.state.data && e.status))) ? POLL_MS : false;
    },
  });
}
