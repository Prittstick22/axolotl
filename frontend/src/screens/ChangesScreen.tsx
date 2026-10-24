import { GitDiffIcon } from '@phosphor-icons/react';
import { useQuery } from '@tanstack/react-query';
import { cn } from 'cn';
import { lazy, Suspense, useState } from 'react';
import { createPortal } from 'react-dom';
import { Link, useOutletContext, useParams, useSearchParams } from 'react-router-dom';

import { getPlan, getRunDiff } from '@/api/client';
import { StatusBadge } from '@/components/StatusBadge';
import { buttonVariants } from '@/components/ui/button';
import { changes } from '@/lib/diff';
import { isLive, useRun } from '@/lib/run';
import { Field } from '@/screens/Overview';

const DiffView = lazy(() => import('@/components/SourceView').then((m) => ({ default: m.DiffView })));
const VIEWS = [[false, 'Inline'], [true, 'Side by side']] as const;
const muted = 'text-sm text-muted-foreground';

/**
 * Every file the run changed, like `git diff` (#36): baseline against the last accepted revision, the one the
 * verdict is on (T-16). The template has no screen for it, so it follows 07's diff pane with a file list beside it.
 */
export default function ChangesScreen() {
  const { repoId = '' } = useParams();
  const [params] = useSearchParams();
  const navSlot = useOutletContext<HTMLElement | null>();
  const search = params.size ? `?${params}` : '';
  const runId = params.get('run');
  const run = useRun(runId);
  const plan = useQuery({ queryKey: ['plan', run.data?.plan_id], queryFn: () => getPlan(run.data!.plan_id), enabled: Boolean(run.data) });
  // Keyed on the verified count, so it loads again as units are accepted mid-run.
  const diff = useQuery({ queryKey: ['run-diff', runId, null, run.data?.units_verified], queryFn: () => getRunDiff(runId!), enabled: Boolean(run.data) });
  const [unitId, setUnitId] = useState(() => params.get('unit') ?? '');
  const [picked, setPicked] = useState<string>();
  const [sideBySide, setSideBySide] = useState(false);

  const unitOf = (path: string) => plan.data?.units.find((u) => u.file_paths.includes(path));
  const all = (diff.data?.files ?? []).map((f) => ({ ...f, unit: unitOf(f.path), ...changes(f.unified_diff) }));
  const units = [...new Map(all.flatMap((f) => (f.unit ? [[f.unit.unit_id, f.unit]] : []))).values()];
  // A filter left over from an earlier diff falls back to every file.
  const filtered = all.filter((f) => f.unit?.unit_id === unitId);
  const files = filtered.length ? filtered : all;
  const file = files.find((f) => f.path === picked) ?? files[0];
  const status = file?.unit && run.data?.units.find((u) => u.unit_id === file.unit!.unit_id)?.status;
  const total = (k: 'added' | 'removed') => all.reduce((n, f) => n + f[k], 0);

  const r = run.data;
  const note = !runId
    ? 'Changes belong to a run. Approve a plan and start one first.'
    : run.isError
      ? `Couldn’t load this run: ${run.error.message}`
      : diff.isError
        ? `Couldn’t load the changes: ${diff.error.message}`
        : !diff.data
          ? 'Loading the changes…'
          : !diff.data.revision
            ? isLive(r) ? 'Nothing accepted yet. Files appear here as units pass verification.' : 'No unit was accepted in this run, so no file changed.'
            : !all.length && 'The accepted revision leaves every file as it was.';

  return (
    <main className="flex flex-1 flex-col">
      {navSlot &&
        runId &&
        createPortal(
          <span className="text-sm whitespace-nowrap text-muted-foreground">
            Run ID <span className="font-mono text-foreground">{runId}</span>
          </span>,
          navSlot,
        )}
      {note || !file ? (
        <div className="flex flex-1 flex-col items-center justify-center gap-2 p-8 text-center">
          <GitDiffIcon size={24} aria-hidden className="text-muted-foreground" />
          <h2 className="text-xl leading-tight font-semibold">{runId ? 'Changes' : 'No run picked'}</h2>
          <p className="max-w-[48ch] text-muted-foreground">{note}</p>
          <Link to={{ pathname: `/repos/${encodeURIComponent(repoId)}/${runId ? 'run' : 'plan'}`, search }} className={cn(buttonVariants(), 'mt-2')}>
            {runId ? 'Go to run' : 'Go to plan'}
          </Link>
        </div>
      ) : (
        <>
          <div className="flex min-h-14 flex-none flex-wrap items-center gap-x-6 gap-y-2 border-b bg-card px-4 py-2">
            <h1 className="text-base font-medium">Changes</h1>
            <Field label="Revision">
              <span className="font-mono">
                {diff.data!.base_revision} to {diff.data!.revision}
              </span>
            </Field>
            <Field label="Changed">
              {all.length} {all.length === 1 ? 'file' : 'files'}, {total('added')} lines added, {total('removed')} removed
            </Field>
            {r && (isLive(r) || r.units_verified < r.units_total) && (
              <span className={cn('max-w-[40ch]', muted)}>
                {isLive(r)
                  ? 'The run is still going: these are the units accepted so far.'
                  : `${r.units_total - r.units_verified} of ${r.units_total} units weren’t verified, so their files keep the legacy code.`}
              </span>
            )}
            <div className="ml-auto flex items-center gap-3">
              <label className="flex items-center gap-2 text-sm">
                <span className="font-medium text-muted-foreground">Unit</span>
                <select
                  value={unitId}
                  onChange={(event) => setUnitId(event.target.value)}
                  className="h-8 rounded-sm border border-input bg-card px-2 font-mono text-sm"
                >
                  <option value="">All units</option>
                  {units.map((u) => (
                    <option key={u.unit_id} value={u.unit_id}>
                      {u.file_paths.join(', ')}
                    </option>
                  ))}
                </select>
              </label>
              <div role="group" aria-label="Diff view" className="flex h-8 flex-none overflow-hidden rounded-md border border-input">
                {VIEWS.map(([value, label]) => (
                  <button
                    key={label}
                    type="button"
                    aria-pressed={value === sideBySide}
                    onClick={() => setSideBySide(value)}
                    className="border-input px-3 text-sm font-medium text-muted-foreground transition-colors not-first:border-l focus-visible:outline-2 focus-visible:-outline-offset-4 focus-visible:outline-current aria-pressed:bg-foreground aria-pressed:text-card"
                  >
                    {label}
                  </button>
                ))}
              </div>
            </div>
          </div>

          <div className="flex min-h-[480px] flex-1">
            <nav aria-label="Changed files" className="relative w-[280px] flex-none border-r bg-card min-[1300px]:w-[320px]">
              <ul className="absolute inset-0 overflow-y-auto">
                {files.map((f) => (
                  <li key={f.path}>
                    <button
                      type="button"
                      aria-pressed={f === file}
                      onClick={() => setPicked(f.path)}
                      className="flex w-full items-baseline gap-3 border-b px-4 py-2 text-left text-sm transition-colors hover:bg-muted focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-ring aria-pressed:bg-muted aria-pressed:font-medium"
                    >
                      <span className="min-w-0 flex-1 truncate font-mono">{f.path}</span>
                      <span className="flex-none whitespace-nowrap text-muted-foreground">
                        <span className="sr-only">{f.added} added, {f.removed} removed</span>
                        <span aria-hidden>+{f.added} −{f.removed}</span>
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            </nav>

            <section aria-label="Diff" className="flex min-w-0 flex-1 flex-col bg-card">
              <div className="flex h-10 flex-none items-center gap-3 border-b px-4 text-sm whitespace-nowrap">
                <GitDiffIcon size={20} aria-hidden className="flex-none" />
                <span className="truncate font-mono font-medium">{file.path}</span>
                {status && <StatusBadge status={status} />}
                <span className="text-muted-foreground">
                  {file.added} {file.added === 1 ? 'line' : 'lines'} added, {file.removed} removed
                </span>
                {file.unit && (
                  <Link
                    to={{ pathname: `/repos/${encodeURIComponent(repoId)}/run/${encodeURIComponent(file.unit.unit_id)}`, search }}
                    className="ml-auto underline underline-offset-4"
                  >
                    Open its verification
                  </Link>
                )}
              </div>
              <div className="relative min-h-0 flex-1">
                <div className="absolute inset-0">
                  <Suspense fallback={<span className={cn('block p-3', muted)}>Loading the editor…</span>}>
                    <DiffView key={`${file.path} ${sideBySide}`} original={file.old_content} modified={file.new_content} path={file.path} sideBySide={sideBySide} />
                  </Suspense>
                </div>
              </div>
            </section>
          </div>
        </>
      )}
    </main>
  );
}
