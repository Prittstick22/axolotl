import { CheckCircleIcon, HourglassIcon, WarningIcon, XCircleIcon } from '@phosphor-icons/react';
import { useQuery } from '@tanstack/react-query';
import { cn } from 'cn';
import type { ReactNode } from 'react';
import { Link, useParams, useSearchParams } from 'react-router-dom';

import { getGraph, getIndexStatus, getRepository, getRuntime } from '@/api/client';
import { buttonVariants } from '@/components/ui/button';
import { moduleRows } from '@/lib/graph';
import type { RepositoryRecord } from '@/types/contracts';

const ANALYSIS: Record<RepositoryRecord['analysis_status'], [typeof CheckCircleIcon, string, string]> = {
  ok: [CheckCircleIcon, 'Complete', 'text-passed'],
  partial: [WarningIcon, 'Partial', 'text-warning'],
  failed: [XCircleIcon, 'Failed', 'text-failed'],
};
const INDEX_POLL_MS = 2000;

export function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex flex-col">
      <span className="text-sm font-medium text-muted-foreground">{label}</span>
      {children}
    </div>
  );
}

function Panel({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="flex flex-col gap-2 rounded-lg border bg-card px-4 py-3">
      <h3 className="font-medium">{title}</h3>
      {children}
    </section>
  );
}

/** 02 Repository overview: what the import found. Template: kit.js s02. */
export default function Overview() {
  const { repoId = '' } = useParams();
  const [params] = useSearchParams();
  const search = params.size ? `?${params}` : '';
  const repo = useQuery({ queryKey: ['repository', repoId], queryFn: () => getRepository(repoId) });
  const graph = useQuery({ queryKey: ['graph', repoId, null], queryFn: () => getGraph(repoId) });
  const runtime = useQuery({ queryKey: ['runtime', repoId], queryFn: () => getRuntime(repoId) });
  // Descriptions and embeddings finish in the background after the import responds (#28).
  const index = useQuery({
    queryKey: ['index', repoId],
    queryFn: () => getIndexStatus(repoId),
    refetchInterval: (q) => (q.state.data?.state === 'indexing' ? INDEX_POLL_MS : false),
  });

  if (repo.isError) {
    return (
      <main className="flex flex-1 flex-col items-center justify-center gap-2 p-8 text-center">
        <h1 className="text-2xl leading-tight font-semibold">Couldn’t open this repository</h1>
        <p className="text-muted-foreground">{repo.error.message}</p>
        <Link to="/" className="underline underline-offset-4">Import a repository</Link>
      </main>
    );
  }
  if (!repo.data) return <main className="p-6 text-muted-foreground">Loading the repository…</main>;

  const r = repo.data;
  const [AnalysisIcon, analysis, analysisColour] = ANALYSIS[r.analysis_status];
  const rows = graph.data ? moduleRows(graph.data) : [];
  const nodes = graph.data?.nodes ?? [];
  const external = nodes.filter((n) => n.kind === 'external_package').map((n) => n.label);
  const warnings = [...new Set([...r.warnings, ...(graph.data?.warnings ?? [])])];

  return (
    <main className="flex flex-1 flex-col gap-6 p-6">
      <div className="flex flex-wrap items-end gap-6">
        {/* grow, not flex-1: a zero basis never lets the buttons wrap below on narrow screens. */}
        <div className="flex min-w-0 grow flex-col gap-2">
          <h1 className="font-mono text-2xl leading-tight font-medium">{r.name}</h1>
          <div className="flex flex-wrap gap-x-8 gap-y-2">
            <Field label="Origin">{r.origin === 'demo_fixture' ? 'Demo fixture, trusted' : `${r.origin === 'folder_upload' ? 'Folder' : 'ZIP'} upload, static analysis only`}</Field>
            <Field label="Snapshot">
              <span className="font-mono text-sm leading-6" title={r.snapshot_hash}>{r.snapshot_hash.slice(0, 19)}</span>
            </Field>
            <Field label="Analysis">
              <span className={cn('flex items-center gap-1', analysisColour)}>
                <AnalysisIcon size={16} aria-hidden />
                {analysis}
              </span>
            </Field>
            <Field label="Imported">
              {new Date(r.created_at).toLocaleString('en-GB', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', second: '2-digit' })}
            </Field>
          </div>
        </div>
        <div className="flex gap-3">
          {/* cn() resolves the base border-transparent against the variant's border, as <Button> does. */}
          <Link to={`/repos/${encodeURIComponent(repoId)}/graph${search}`} className={cn(buttonVariants({ variant: 'outline' }))}>
            Open graph
          </Link>
          <Link to={`/repos/${encodeURIComponent(repoId)}/plan${search}`} className={cn(buttonVariants())}>
            Choose target
          </Link>
        </div>
      </div>

      <div className="flex flex-col gap-6 lg:flex-row lg:items-start">
        <section className="min-w-0 flex-1 rounded-lg border bg-card">
          <div className="flex flex-wrap items-baseline gap-x-4 border-b px-4 py-3">
            <h2 className="text-xl leading-tight font-semibold">Modules</h2>
            <span className="ml-auto text-sm text-muted-foreground">
              {graph.data &&
                `${nodes.filter((n) => n.kind === 'local_module').length} local, ${nodes.filter((n) => n.kind === 'test_module').length} test, ` +
                  (external.length ? `${external.length} external (${external.join(', ')})` : 'no external packages')}
            </span>
          </div>
          {graph.isError ? (
            <p className="px-4 py-3 text-muted-foreground">Couldn’t load the module graph: {graph.error.message}</p>
          ) : !graph.data ? (
            <p className="px-4 py-3 text-muted-foreground">Loading modules…</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full border-collapse [&_td]:border-t [&_td]:px-3 [&_td]:py-2 [&_th]:px-3 [&_th]:py-2 [&_th]:text-sm [&_th]:font-medium [&_th]:whitespace-nowrap [&_th]:text-muted-foreground">
                <thead>
                  <tr className="text-left">
                    <th>Module</th>
                    <th className="text-right">Functions</th>
                    <th className="text-right">Classes</th>
                    <th className="text-right">Local imports</th>
                    <th className="text-right">Imported by</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((m) => (
                    <tr key={m.id}>
                      <td className="font-mono text-sm">{m.label}</td>
                      <td className="text-right">{m.functions}</td>
                      <td className="text-right">{m.classes}</td>
                      <td className="text-right">{m.localImports}</td>
                      <td className={cn('text-right', !m.importedBy && 'text-muted-foreground')}>{m.importedBy || 'None'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>

        <div className="flex flex-none flex-col gap-4 lg:w-[360px] min-[1300px]:w-[400px]">
          <Panel title="Found in this snapshot">
            {(
              [
                ['Files', r.file_count],
                ['Modules', r.module_count],
                ['Symbols', r.symbol_count],
                ['Test files', r.test_file_count],
              ] as const
            ).map(([k, v]) => (
              <div key={k} className="flex items-baseline justify-between gap-3">
                <span className="text-muted-foreground">{k}</span>
                <span>{v}</span>
              </div>
            ))}
          </Panel>

          <Panel title="Python version">
            {runtime.isError ? (
              <p className="text-sm text-muted-foreground">Couldn’t read the package metadata: {runtime.error.message}</p>
            ) : !runtime.data ? (
              <p className="text-sm text-muted-foreground">Reading the package metadata…</p>
            ) : (
              <>
                <div className="flex items-baseline justify-between gap-3">
                  <span className="text-muted-foreground">Declared</span>
                  <span>{runtime.data.declared_python ? `Python ${runtime.data.declared_python}` : 'Not declared'}</span>
                </div>
                {runtime.data.evidence.map((e) => (
                  <span key={e} className="font-mono text-sm break-all text-muted-foreground">{e}</span>
                ))}
                <p className="text-sm text-muted-foreground">{runtime.data.note}</p>
              </>
            )}
          </Panel>

          <Panel title="Warnings">
            {warnings.length ? (
              warnings.map((w) => (
                <div key={w} className="flex items-start gap-2">
                  <WarningIcon size={16} aria-hidden className="mt-1 flex-none text-muted-foreground" />
                  <span className="text-sm break-words">{w}</span>
                </div>
              ))
            ) : (
              <span className="text-sm text-muted-foreground">None</span>
            )}
          </Panel>

          {/* Ready, never indexed, or no answer: nothing to say. Template: kit.js s02 notice. */}
          {(index.data?.state === 'indexing' || index.data?.state === 'failed') && (
            <div className="flex items-start gap-2 rounded-md border bg-card px-4 py-3">
              {index.data.state === 'indexing' ? (
                <HourglassIcon size={20} aria-hidden className="flex-none" />
              ) : (
                <XCircleIcon size={20} aria-hidden className="flex-none text-failed" />
              )}
              {index.data.state === 'indexing' ? (
                <span className="text-sm">
                  Indexing for search{index.data.total ? `: ${index.data.embedded ?? 0} of ${index.data.total} chunks embedded` : ''}.
                  Keyword search works now; vector search is available when it finishes.
                </span>
              ) : (
                <span className="flex flex-col text-sm">
                  Search indexing stopped. Keyword search still works.
                  {index.data.reason && <span className="break-words text-muted-foreground">{index.data.reason}</span>}
                </span>
              )}
            </div>
          )}
        </div>
      </div>
    </main>
  );
}
