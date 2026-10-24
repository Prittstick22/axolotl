import { ArrowsLeftRightIcon, XCircleIcon } from '@phosphor-icons/react';
import { useQuery } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { Link, useParams, useSearchParams } from 'react-router-dom';

import { ApiError, getDependencyChanges } from '@/api/client';
import { buttonVariants } from '@/components/ui/button';
import type { DependencyChanges, DependencyModuleChange } from '@/types/contracts';

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="flex flex-col gap-2 rounded-lg border bg-card px-4 py-3">
      <h2 className="font-medium">{title}</h2>
      {children}
    </section>
  );
}

function Heuristic({ basis }: { basis: string }) {
  return basis === 'name_match' ? <span className="ml-2 rounded-sm border border-warning px-1.5 py-0.5 text-xs text-warning">Heuristic</span> : null;
}

function Module({ change }: { change: DependencyModuleChange }) {
  const items = [
    ...change.imports_added.map((i) => ({ label: `Import added: ${i.name}`, basis: i.basis })),
    ...change.imports_removed.map((i) => ({ label: `Import removed: ${i.name}`, basis: i.basis })),
    ...change.stdlib_renamed.map((i) => ({ label: `Standard library: ${i.before} → ${i.after}`, basis: i.basis })),
    ...change.builtins_replaced.map((i) => ({ label: `Builtin: ${i.before} → ${i.after}`, basis: i.basis })),
    ...change.methods_replaced.map((i) => ({ label: `Method: ${i.before} → ${i.after}`, basis: i.basis })),
  ];
  return (
    <section className="flex flex-col gap-2 border-b py-3 last:border-0">
      <h3 className="font-mono text-sm font-medium">{change.path}</h3>
      {items.length ? (
        <ul className="flex flex-col gap-1 text-sm">
          {items.map((item, index) => (
            <li key={`${item.label}-${index}`}>
              {item.label}<Heuristic basis={item.basis} />
            </li>
          ))}
        </ul>
      ) : <p className="text-sm text-muted-foreground">Module {change.status}.</p>}
    </section>
  );
}

function Changes({ changes }: { changes: DependencyChanges }) {
  if (!changes.revision) {
    return <p className="text-sm text-muted-foreground">No revision was accepted in this run, so dependencies can’t be compared.</p>;
  }
  if (!changes.modules.length && !changes.edges.added.length && !changes.edges.removed.length && !changes.packages.added.length && !changes.packages.removed.length && changes.python?.before.declared === changes.python?.after.declared) {
    return <p className="text-sm text-muted-foreground">No dependency changes</p>;
  }
  return (
    <div className="flex flex-col gap-4">
      {changes.python && (
        <Section title="Declared Python version">
          <p className="text-sm">
            {changes.python.before.declared ?? 'Not declared'} <span aria-hidden>→</span> {changes.python.after.declared ?? 'Not declared'}
          </p>
        </Section>
      )}
      <Section title="Third-party packages">
        {changes.packages.added.length || changes.packages.removed.length ? (
          <ul className="flex flex-col gap-1 text-sm">
            {changes.packages.added.map((name) => <li key={`added-${name}`}>Added: <code className="font-mono">{name}</code></li>)}
            {changes.packages.removed.map((name) => <li key={`removed-${name}`}>Removed: <code className="font-mono">{name}</code></li>)}
          </ul>
        ) : <p className="text-sm text-muted-foreground">No added or removed packages.</p>}
        {changes.packages.note && <p className="text-sm text-muted-foreground">{changes.packages.note}</p>}
      </Section>
      <Section title="Module imports and Python 2 replacements">
        {changes.modules.length ? changes.modules.map((change) => <Module key={change.module_id} change={change} />) : <p className="text-sm text-muted-foreground">No per-module import changes.</p>}
      </Section>
      <Section title="Dependency edges">
        <div className="flex flex-wrap gap-x-6 gap-y-2 text-sm">
          <span className="text-passed">Added: {changes.edges.added.length}</span>
          <span className="text-failed">Removed: {changes.edges.removed.length}</span>
        </div>
        {[...changes.edges.added.map((e) => ({ ...e, state: 'Added' })), ...changes.edges.removed.map((e) => ({ ...e, state: 'Removed' }))].map((edge, i) => (
          <p key={`${edge.state}-${edge.source}-${edge.target}-${edge.type}-${i}`} className="font-mono text-sm">
            {edge.state}: {edge.source} → {edge.target} <span className="text-muted-foreground">({edge.type})</span>
          </p>
        ))}
        {!changes.edges.added.length && !changes.edges.removed.length && <p className="text-sm text-muted-foreground">No edge changes.</p>}
      </Section>
      {changes.warnings.length > 0 && (
        <Section title="Comparison notes">
          <ul className="list-disc pl-5 text-sm">{changes.warnings.map((warning) => <li key={warning}>{warning}</li>)}</ul>
        </Section>
      )}
    </div>
  );
}

export default function DependenciesScreen() {
  const { repoId = '' } = useParams();
  const [params] = useSearchParams();
  const runId = params.get('run');
  const query = useQuery({
    queryKey: ['dependency-changes', runId],
    queryFn: () => getDependencyChanges(runId!),
    enabled: Boolean(runId),
  });
  const search = params.size ? `?${params}` : '';
  if (!runId) {
    return (
      <main className="flex flex-1 flex-col items-center justify-center gap-2 p-8 text-center">
        <ArrowsLeftRightIcon size={24} aria-hidden className="text-muted-foreground" />
        <h1 className="text-xl font-semibold">No run picked</h1>
        <p className="max-w-[48ch] text-muted-foreground">Dependency changes belong to a run. Start a migration first.</p>
        <Link to={{ pathname: `/repos/${encodeURIComponent(repoId)}/plan`, search }} className={buttonVariants()}>Go to plan</Link>
      </main>
    );
  }
  return (
    <main className="flex flex-1 flex-col gap-4 p-6">
      <header>
        <h1 className="text-xl font-semibold">Dependencies</h1>
        <p className="text-sm text-muted-foreground">Static comparison of the legacy snapshot and the last accepted revision.</p>
      </header>
      {query.isPending ? <p className="text-sm text-muted-foreground">Loading dependency changes…</p>
        : query.isError ? (
          <section role="alert" className="flex items-start gap-2 rounded-lg border border-failed bg-card p-4">
            <XCircleIcon size={20} aria-hidden className="mt-0.5 flex-none text-failed" />
            <div className="flex flex-col gap-1">
              <h2 className="font-medium">Couldn’t load dependency changes</h2>
              <p className="text-sm text-muted-foreground">{query.error instanceof ApiError ? query.error.message : String(query.error)}</p>
            </div>
          </section>
        ) : query.data ? <Changes changes={query.data} /> : null}
    </main>
  );
}
