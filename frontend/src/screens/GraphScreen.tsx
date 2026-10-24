import { CornersOutIcon, MagnifyingGlassIcon, MinusIcon, PlusIcon } from '@phosphor-icons/react';
import { useQuery, type UseQueryResult } from '@tanstack/react-query';
import {
  Handle,
  MarkerType,
  Panel,
  Position,
  ReactFlow,
  useReactFlow,
  useStore,
  type Edge,
  type Node,
  type NodeChange,
  type NodeProps,
} from '@xyflow/react';
import '@xyflow/react/dist/base.css';
import { cn } from 'cn';
import dagre from 'dagre';
import { useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { useOutletContext, useParams, useSearchParams } from 'react-router-dom';

import { getDependencyChanges, getGraph, getPlan } from '@/api/client';
import { Button } from '@/components/ui/button';
import { dependencyEdges } from '@/lib/graph';
import { isLive, POLL_MS, useRun } from '@/lib/run';
import { TONE_BORDER, TONE_TEXT, TONE_TINT, UNIT_STATUS } from '@/lib/status';
import { ModuleInspector } from '@/screens/ModuleInspector';
import type { DependencyChanges, GraphEdge, GraphNode, GraphPayload, Resolution } from '@/types/contracts';

const W = 190;
const H = 58;
const FIT = { padding: 0.15, maxZoom: 1 };
const DASH: Record<Resolution, string | undefined> = { confirmed: undefined, heuristic: '6 4', unresolved: '2 3' };
const hidden = { visibility: 'hidden' } as const;

type FlowNode = Node<{ node: GraphNode; dim: boolean; attempt?: number }>;

function ModuleNode({ data: { node, dim, attempt }, selected }: NodeProps<FlowNode>) {
  const { icon: Icon, label, tone } = UNIT_STATUS[node.status];
  return (
    <div
      className={cn(
        // A status change fades between tones rather than snapping, so the eye catches which node moved.
        'flex flex-col justify-center rounded-md border px-2.5 py-1.5 transition-colors duration-200 motion-reduce:transition-none',
        dim ? 'border-border-strong bg-background' : [TONE_BORDER[tone], TONE_TINT[tone]],
        selected && 'outline-2 outline-offset-2 outline-foreground',
      )}
      style={{ width: W, height: H }}
    >
      <Handle type="target" position={Position.Top} isConnectable={false} style={hidden} />
      <span className={cn('truncate font-mono text-sm leading-5 font-medium', dim ? 'text-muted-foreground' : 'text-foreground')}>
        {node.label}
      </span>
      {node.kind === 'test_module' ? (
        <span className="text-sm leading-5 text-muted-foreground">Test module</span>
      ) : (
        <span className={cn('flex items-center gap-1 text-sm leading-5', TONE_TEXT[tone])}>
          <Icon size={16} aria-hidden />
          {label}
          {attempt && attempt > 1 && <span className="ml-auto text-muted-foreground">Attempt {attempt}</span>}
        </span>
      )}
      <Handle type="source" position={Position.Bottom} isConnectable={false} style={hidden} />
    </div>
  );
}

/** Outside the project: dashed, muted, never selectable as a migration target (brief, screen 3). */
function ExternalNode({ data: { node } }: NodeProps<FlowNode>) {
  return (
    <div className="flex flex-col justify-center rounded-md border border-dashed border-border-strong bg-background px-2.5 py-1.5" style={{ width: W, height: H }}>
      <Handle type="target" position={Position.Top} isConnectable={false} style={hidden} />
      <span className="truncate font-mono text-sm leading-5 font-medium text-muted-foreground">{node.label}</span>
      <span className="text-sm leading-5 text-muted-foreground">External package</span>
      <Handle type="source" position={Position.Bottom} isConnectable={false} style={hidden} />
    </div>
  );
}

const NODE_TYPES = { module: ModuleNode, external: ExternalNode };

/** Dependencies sit below the modules that import them. */
function layout(graph: GraphPayload, edges: GraphEdge[]) {
  const g = new dagre.graphlib.Graph();
  g.setGraph({ rankdir: 'TB', nodesep: 32, ranksep: 72 });
  g.setDefaultEdgeLabel(() => ({}));
  for (const n of graph.nodes) g.setNode(n.id, { width: W, height: H });
  for (const e of edges) g.setEdge(e.source, e.target);
  dagre.layout(g);
  return new Map(graph.nodes.map((n) => [n.id, { x: g.node(n.id).x - W / 2, y: g.node(n.id).y - H / 2 }]));
}

const LEGEND: [string, string | undefined, string, number][] = [
  ['Confirmed', undefined, 'var(--muted-foreground)', 1.5],
  ['Heuristic', DASH.heuristic, 'var(--muted-foreground)', 1.5],
  ['Unresolved', DASH.unresolved, 'var(--border-strong)', 1.5],
  ['Linked to selection', undefined, 'var(--foreground)', 3],
];

/** Shown on canvases at least 672px wide; narrower ones get a Key button so it doesn't cover the graph. */
function Legend({ hasChanges = false }: { hasChanges?: boolean }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="flex flex-col items-start gap-2">
      <div
        id="graph-key"
        className={cn(
          'flex-wrap items-center gap-x-4 gap-y-1 rounded-md border bg-card px-3 py-2 text-sm whitespace-nowrap',
          open ? 'flex' : 'hidden @2xl:flex',
        )}
      >
        <span className="font-semibold">A → B: A imports B</span>
        {LEGEND.map(([label, dash, colour, width]) => (
          <span key={label} className="flex items-center gap-2">
            <svg width="32" height="16" aria-hidden className="overflow-visible">
              <line x1="0" y1="8" x2="24" y2="8" style={{ stroke: colour, strokeWidth: width, strokeDasharray: dash }} />
              <polygon points={`32,8 23,${8 - width * 2 - 1.5} 23,${8 + width * 2 + 1.5}`} style={{ fill: colour }} />
            </svg>
            {label}
          </span>
        ))}
        {hasChanges && (
          <>
            <span className="flex items-center gap-2"><span aria-hidden className="w-8 border-t-2 border-passed" />Added</span>
            <span className="flex items-center gap-2"><span aria-hidden className="w-8 border-t-2 border-dashed border-failed" />Removed</span>
          </>
        )}
      </div>
      <Button variant="outline" className="bg-card @2xl:hidden" aria-expanded={open} aria-controls="graph-key" onClick={() => setOpen(!open)}>
        {open ? 'Hide key' : 'Key'}
      </Button>
    </div>
  );
}

function Zoom() {
  const { zoomIn, zoomOut, fitView } = useReactFlow();
  // Re-fit when the canvas resizes, e.g. the inspector opening or expanding, so no module ends up behind it.
  const width = useStore((s) => s.width);
  const height = useStore((s) => s.height);
  useEffect(() => void fitView(FIT), [width, height, fitView]);
  return (
    <Panel position="bottom-right" className="flex rounded-md border bg-card">
      <Button variant="ghost" size="icon" aria-label="Zoom in" onClick={() => void zoomIn()}>
        <PlusIcon size={20} aria-hidden />
      </Button>
      <Button variant="ghost" size="icon" aria-label="Zoom out" onClick={() => void zoomOut()}>
        <MinusIcon size={20} aria-hidden />
      </Button>
      <Button variant="ghost" size="icon" aria-label="Fit graph to view" onClick={() => void fitView(FIT)}>
        <CornersOutIcon size={20} aria-hidden />
      </Button>
    </Panel>
  );
}

function Canvas({ graph, selected, filter, onSelect, attempts, dependencyChanges }: {
  graph: GraphPayload;
  selected: string | null;
  filter: string;
  onSelect: (id: string | null) => void;
  attempts?: Map<string, number>;
  dependencyChanges?: DependencyChanges;
}) {
  const graphWithChanges = useMemo(() => {
    if (!dependencyChanges) return graph;
    const nodes = new Map(graph.nodes.map((node) => [node.id, node]));
    for (const change of dependencyChanges.modules) {
      if (nodes.has(change.module_id)) continue;
      nodes.set(change.module_id, {
        id: change.module_id,
        label: change.path.split('/').at(-1) || change.module_id,
        kind: 'local_module',
        path: change.path,
        status: 'not_planned',
        unit_id: null,
        function_count: 0,
        class_count: 0,
        warnings: [],
      });
    }
    const changes = [
      ...dependencyChanges.edges.added.map((edge) => ({ edge, resolution: 'added' as const })),
      ...dependencyChanges.edges.removed.map((edge) => ({ edge, resolution: 'removed' as const })),
    ];
    for (const { edge } of changes) {
      for (const id of [edge.source, edge.target]) {
        if (nodes.has(id)) continue;
        const external = id.startsWith('ext_');
        nodes.set(id, {
          id,
          label: external ? id.slice(4) : id,
          kind: external ? 'external_package' : 'local_module',
          path: null,
          status: 'not_planned',
          unit_id: null,
          function_count: 0,
          class_count: 0,
          warnings: [],
        });
      }
    }
    const changeEdges = changes.map(({ edge }) => ({
      ...edge,
      id: `${edge.source}>${edge.target}:${edge.type}`,
      resolution: 'confirmed' as const,
      basis: 'static_ast' as const,
      source_path: null,
      source_line: null,
    }));
    return { ...graph, nodes: [...nodes.values()], edges: [...graph.edges, ...changeEdges] };
  }, [dependencyChanges, graph]);
  const edges = useMemo(() => dependencyEdges(graphWithChanges.edges), [graphWithChanges]);
  const positions = useMemo(() => layout(graphWithChanges, edges), [graphWithChanges, edges]);
  // Selecting a module lights up its reverse dependants: what breaks if it changes.
  const dependants = new Set(edges.filter((e) => e.target === selected).map((e) => e.source));
  const q = filter.trim().toLowerCase();

  const added = new Set((dependencyChanges?.edges.added ?? []).map((e) => `${e.source}>${e.target}`));
  const removed = new Set((dependencyChanges?.edges.removed ?? []).map((e) => `${e.source}>${e.target}`));
  const nodes: FlowNode[] = graphWithChanges.nodes.map((n) => {
    const external = n.kind === 'external_package';
    const dim = Boolean(selected && n.id !== selected && !dependants.has(n.id)) || Boolean(q && !n.label.toLowerCase().includes(q));
    return {
      id: n.id,
      type: external ? 'external' : 'module',
      position: positions.get(n.id)!,
      data: { node: n, dim, attempt: n.unit_id ? attempts?.get(n.unit_id) : undefined },
      selected: n.id === selected,
      selectable: !external && graph.nodes.some((known) => known.id === n.id),
      focusable: !external && graph.nodes.some((known) => known.id === n.id),
      draggable: false,
      width: W,
      height: H,
    };
  });
  const flowEdges: Edge[] = edges.map((e) => {
    const isAdded = added.has(`${e.source}>${e.target}`);
    const isRemoved = removed.has(`${e.source}>${e.target}`) && !isAdded;
    const lit = e.target === selected;
    const colour = isAdded ? 'var(--passed)' : isRemoved ? 'var(--failed)' : lit ? 'var(--foreground)' : selected || e.resolution === 'unresolved' ? 'var(--border-strong)' : 'var(--muted-foreground)';
    return {
      id: `${e.source}>${e.target}`,
      source: e.source,
      target: e.target,
      type: 'straight',
      selectable: false,
      focusable: false,
      zIndex: lit ? 1 : 0,
      style: { stroke: colour, strokeWidth: lit ? 3 : 1.5, strokeDasharray: isAdded ? '5 3' : isRemoved ? '2 4' : DASH[e.resolution] },
      markerEnd: { type: MarkerType.ArrowClosed, color: colour, width: lit ? 18 : 14, height: lit ? 18 : 14, markerUnits: 'userSpaceOnUse' },
    };
  });

  // Mouse and keyboard (Enter, Space, Escape) selection both arrive here; the URL holds the selection.
  const onNodesChange = (changes: NodeChange<FlowNode>[]) => {
    const picks = changes.filter((c) => c.type === 'select');
    const on = picks.find((c) => c.selected);
    if (on) onSelect(on.id);
    else if (picks.some((c) => c.id === selected)) onSelect(null);
  };

  return (
    <ReactFlow
      nodes={nodes}
      edges={flowEdges}
      nodeTypes={NODE_TYPES}
      onNodesChange={onNodesChange}
      nodesDraggable={false}
      nodesConnectable={false}
      edgesFocusable={false}
      fitView
      fitViewOptions={FIT}
      minZoom={0.2}
      proOptions={{ hideAttribution: true }}
      aria-label="Module dependency graph"
    >
      {/* % is of the canvas, leaving room for the zoom panel on the right. */}
      <Panel position="bottom-left" className="max-w-[calc(100%-160px)]">
        <Legend hasChanges={Boolean(dependencyChanges?.revision)} />
      </Panel>
      <Zoom />
    </ReactFlow>
  );
}

/** The grid-backed canvas: the graph, or a line saying why there isn't one. Shared with the run screen. */
export function GraphArea({ graph, ...canvas }: {
  graph: UseQueryResult<GraphPayload>;
  selected: string | null;
  filter: string;
  onSelect: (id: string | null) => void;
  attempts?: Map<string, number>;
  dependencyChanges?: DependencyChanges;
}) {
  return (
    <div className="@container relative min-h-[420px] flex-1 bg-background bg-[linear-gradient(var(--grid)_1px,transparent_1px),linear-gradient(90deg,var(--grid)_1px,transparent_1px)] bg-size-[24px_24px]">
      {graph.data?.nodes.length ? (
        <div className="absolute inset-0">
          <Canvas graph={graph.data} {...canvas} />
        </div>
      ) : (
        <p className="p-6 text-muted-foreground">
          {graph.isError
            ? `Couldn’t load the graph: ${graph.error.message}`
            : graph.data
              ? 'No modules were found in this snapshot.'
              : 'Loading the graph…'}
        </p>
      )}
    </div>
  );
}

/** Lives in the tab bar, as in the template. `/` focuses it from anywhere outside a text field. */
function FilterField({ value, onChange }: { value: string; onChange: (value: string) => void }) {
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const t = event.target as HTMLElement;
      if (event.key !== '/' || t.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(t.tagName)) return;
      event.preventDefault();
      input.current?.focus();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, []);
  return (
    <label className="flex h-8 w-36 items-center gap-2 rounded-sm border border-input bg-card px-2 text-sm has-focus-visible:outline-2 has-focus-visible:outline-offset-2 has-focus-visible:outline-ring sm:w-[280px]">
      <MagnifyingGlassIcon size={16} aria-hidden className="flex-none text-muted-foreground" />
      <input
        ref={input}
        type="search"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        onKeyDown={(event) => event.key === 'Escape' && onChange('')}
        aria-label="Filter modules"
        placeholder="Filter modules"
        className="min-w-0 flex-1 bg-transparent outline-none placeholder:text-muted-foreground [&::-webkit-search-cancel-button]:hidden"
      />
      <kbd className="hidden rounded-sm border border-input px-1.5 font-mono text-sm leading-5 text-muted-foreground sm:block">/</kbd>
    </label>
  );
}

/** 03 Architecture graph with the 04 inspector beside it. Template: kit.js s03, s04. */
export default function GraphScreen() {
  const { repoId = '' } = useParams();
  const [params, setParams] = useSearchParams();
  const navSlot = useOutletContext<HTMLElement | null>();
  const selected = params.get('module');
  const runId = params.get('run');
  const [filter, setFilter] = useState('');
  const [expanded, setExpanded] = useState(false);
  const run = useRun(runId);
  // Unit statuses come from the run, so they move while it does.
  const graph = useQuery({
    queryKey: ['graph', repoId, runId],
    queryFn: () => getGraph(repoId, runId),
    refetchInterval: runId && isLive(run.data) ? POLL_MS : false,
  });
  const dependencyChanges = useQuery({
    queryKey: ['dependency-changes', runId],
    queryFn: () => getDependencyChanges(runId!),
    enabled: Boolean(runId),
  });
  const planId = params.get('plan');
  const plan = useQuery({ queryKey: ['plan', planId], queryFn: () => getPlan(planId!), enabled: Boolean(planId) });

  const select = (id: string | null) =>
    setParams(
      (p) => {
        if (id) p.set('module', id);
        else p.delete('module');
        return p;
      },
      { replace: true },
    );

  return (
    <main className="flex min-h-[480px] flex-1 flex-col md:flex-row">
      {navSlot && createPortal(<FilterField value={filter} onChange={setFilter} />, navSlot)}
      <GraphArea graph={graph} selected={selected} filter={filter} onSelect={select} dependencyChanges={dependencyChanges.data} />
      {selected && graph.data && (
        <aside
          aria-label="Module inspector"
          className={cn(
            'flex-none border-t bg-card md:relative md:border-t-0 md:border-l',
            expanded ? 'md:w-[560px] min-[1300px]:w-[640px]' : 'md:w-[360px]',
          )}
        >
          <div className="flex flex-col md:absolute md:inset-0 md:overflow-y-auto">
            <ModuleInspector
              key={selected}
              repoId={repoId}
              moduleId={selected}
              graph={graph.data}
              runId={runId}
              unit={plan.data?.units.find((u) => u.module_ids.includes(selected))}
              expanded={expanded}
              onExpand={setExpanded}
              onClose={() => select(null)}
            />
          </div>
        </aside>
      )}
    </main>
  );
}
