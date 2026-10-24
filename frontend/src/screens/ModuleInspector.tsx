import { CornersInIcon, CornersOutIcon, WarningIcon, XIcon } from '@phosphor-icons/react';
import { useQuery } from '@tanstack/react-query';
import { cn } from 'cn';
import { lazy, Suspense, useState, type ReactNode } from 'react';
import { Link } from 'react-router-dom';

import { getModule } from '@/api/client';
import { StatusBadge } from '@/components/StatusBadge';
import { Button } from '@/components/ui/button';
import type { GraphPayload, MigrationUnit, NodeKind } from '@/types/contracts';

const SourceView = lazy(() => import('@/components/SourceView'));

const KIND: Record<NodeKind, string> = {
  local_module: 'Local module',
  test_module: 'Test module',
  external_package: 'External package',
};

export function Sec({ label, className, children }: { label?: ReactNode; className?: string; children: ReactNode }) {
  return (
    <div className={cn('flex flex-none flex-col gap-2 border-b px-4 py-3 last:border-b-0', className)}>
      {label && <span className="text-sm font-medium text-muted-foreground">{label}</span>}
      {children}
    </div>
  );
}

const none = <span className="text-sm text-muted-foreground">None</span>;

/** 04 Module inspector, the graph's side panel. Template: kit.js `inspector` (03) and s04 (expanded, with source). */
export function ModuleInspector({ repoId, moduleId, graph, runId, unit, expanded, onExpand, onClose }: {
  repoId: string;
  moduleId: string;
  graph: GraphPayload;
  runId: string | null;
  /** The module's unit in the plan from ?plan=; risk and visible cases exist only once there is a plan. */
  unit?: MigrationUnit;
  expanded: boolean;
  onExpand: (expanded: boolean) => void;
  onClose: () => void;
}) {
  const detail = useQuery({ queryKey: ['module', repoId, moduleId], queryFn: () => getModule(repoId, moduleId) });
  const [symbolId, setSymbolId] = useState<string>();
  const byId = new Map(graph.nodes.map((n) => [n.id, n]));
  const node = byId.get(moduleId);
  const d = detail.data;

  const prefix = `${d?.symbols.find((s) => s.kind === 'module')?.qualified_name}.`;
  const short = (name: string) => (name.startsWith(prefix) ? name.slice(prefix.length) : name);
  const symbols = (d?.symbols ?? []).filter((s) => s.kind !== 'module').sort((a, b) => a.start_line - b.start_line);
  const symbol = symbols.find((s) => s.symbol_id === symbolId);
  const imports = [...new Set(d?.imports.map((e) => e.target))];
  const dependants = [...new Set(d?.dependants.map((e) => e.source))];
  const warnings = [...new Set([...(node?.warnings ?? []), ...(d?.warnings ?? [])])];

  return (
    <>
      <Sec className="gap-1">
        <div className="flex items-center justify-between gap-3">
          <h2 className="truncate font-mono text-base font-medium">{node?.label ?? moduleId}</h2>
          <div className="flex flex-none">
            <Button variant="ghost" size="icon" onClick={() => onExpand(!expanded)} aria-label={expanded ? 'Collapse inspector' : 'Expand inspector'}>
              {expanded ? <CornersInIcon size={20} aria-hidden /> : <CornersOutIcon size={20} aria-hidden />}
            </Button>
            <Button variant="ghost" size="icon" onClick={onClose} aria-label="Close inspector">
              <XIcon size={20} aria-hidden />
            </Button>
          </div>
        </div>
        {node?.path && node.path !== node.label && <span className="font-mono text-sm break-all text-muted-foreground">{node.path}</span>}
        {node && (
          <div className="mt-1 flex flex-wrap items-center gap-2">
            <StatusBadge status={node.status} />
            <span className="text-sm text-muted-foreground">{runId ? `In run ${runId}` : 'No run yet'}</span>
            {node.path && (
              <Link
                to={{ pathname: `/repos/${encodeURIComponent(repoId)}/docs`, search: `?${new URLSearchParams({ ...(runId && { run: runId }), path: node.path })}` }}
                className="ml-auto text-sm underline underline-offset-4"
              >
                Docs
              </Link>
            )}
          </div>
        )}
      </Sec>

      {unit && (
        <Sec label="Risk">
          <span className="font-medium capitalize">{unit.risk}</span>
          {unit.risk_factors.length > 0 && (
            <ul className="flex list-disc flex-col gap-1 pl-5 text-sm text-muted-foreground">
              {unit.risk_factors.map((f) => (
                <li key={f}>{f}</li>
              ))}
            </ul>
          )}
          <span className="text-sm">
            <span className="text-muted-foreground">Visible cases </span>
            {unit.visible_case_count || 'None'}
          </span>
        </Sec>
      )}

      {detail.isError ? (
        <Sec>
          <p className="text-sm text-muted-foreground">Couldn’t load this module: {detail.error.message}</p>
        </Sec>
      ) : !d ? (
        <Sec>
          <p className="text-sm text-muted-foreground">Loading the module…</p>
        </Sec>
      ) : (
        <>
          <Sec label="Warnings">
            {warnings.length
              ? warnings.map((w) => (
                  <div key={w} className="flex items-start gap-2">
                    <WarningIcon size={16} aria-hidden className="mt-0.5 flex-none text-muted-foreground" />
                    <span className="text-sm">{w}</span>
                  </div>
                ))
              : none}
          </Sec>

          <Sec label="Functions and classes">
            {symbols.length
              ? symbols.map((s) => (
                  <button
                    key={s.symbol_id}
                    type="button"
                    aria-pressed={s.symbol_id === symbolId}
                    onClick={() => {
                      setSymbolId(s.symbol_id);
                      onExpand(true);
                    }}
                    className="-mx-2 flex items-baseline justify-between gap-3 rounded-sm px-2 py-0.5 text-left transition-colors hover:bg-muted aria-pressed:bg-muted aria-pressed:font-medium"
                  >
                    <span className="min-w-0 truncate font-mono text-sm">{short(s.qualified_name)}</span>
                    <span className="flex-none text-sm font-normal text-muted-foreground">
                      lines {s.start_line}–{s.end_line}
                    </span>
                  </button>
                ))
              : none}
          </Sec>

          {expanded && (
            <Sec
              className="min-h-[360px] flex-1"
              label={
                <span className="flex items-baseline justify-between gap-3">
                  Source, read-only
                  {symbol && (
                    <span className="font-normal">
                      {short(symbol.qualified_name)}, lines {symbol.start_line}–{symbol.end_line}
                    </span>
                  )}
                </span>
              }
            >
              {d.source ? (
                <div className="relative min-h-0 flex-1 overflow-hidden rounded-sm border">
                  <div className="absolute inset-0">
                    <Suspense fallback={<span className="block p-3 text-sm text-muted-foreground">Loading the editor…</span>}>
                      <SourceView source={d.source} path={d.path} start={symbol?.start_line} end={symbol?.end_line} />
                    </Suspense>
                  </div>
                </div>
              ) : (
                <span className="text-sm text-muted-foreground">No source to show. The warning above says why.</span>
              )}
            </Sec>
          )}

          <Sec label="Imports">
            {imports.length
              ? imports.map((id) => (
                  <div key={id} className="flex items-baseline justify-between gap-3">
                    <span className="font-mono text-sm">{byId.get(id)?.label ?? id}</span>
                    <span className="text-sm text-muted-foreground">{KIND[byId.get(id)?.kind ?? 'external_package']}</span>
                  </div>
                ))
              : none}
          </Sec>

          <Sec label="Dependants, highlighted on the graph">
            {dependants.length
              ? dependants.map((id) => (
                  <div key={id} className="flex items-center justify-between gap-3">
                    <span className="font-mono text-sm">{byId.get(id)?.label ?? id}</span>
                    {byId.get(id) && <StatusBadge status={byId.get(id)!.status} />}
                  </div>
                ))
              : none}
          </Sec>
        </>
      )}
    </>
  );
}
