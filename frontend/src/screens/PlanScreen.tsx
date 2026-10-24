import { ArrowBendDownRightIcon, CheckCircleIcon, CircleDashedIcon, ListNumbersIcon, XCircleIcon, type Icon } from '@phosphor-icons/react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { cn } from 'cn';
import { useState } from 'react';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom';

import { ApiError, approvePlan, createPlan, getPlan, getRuntime, listTargets, startRun } from '@/api/client';
import { Choice } from '@/components/Choice';
import { Button, buttonVariants } from '@/components/ui/button';
import { TONE_TEXT, TONE_TINT, type Tone } from '@/lib/status';
import { matchingTarget } from '@/lib/targets';
import type { MigrationPlan, PlanStatus } from '@/types/contracts';

const PLAN_STATUS: Record<PlanStatus, [Icon, string, Tone]> = {
  draft: [CircleDashedIcon, 'Draft', 'neutral'],
  approved: [CheckCircleIcon, 'Approved', 'passed'],
  superseded: [ArrowBendDownRightIcon, 'Superseded', 'neutral'],
  rejected: [XCircleIcon, 'Rejected', 'failed'],
};
const time = (iso: string) => new Date(iso).toLocaleTimeString('en-GB');
// Shorter wording on screen only. The API's description also goes into Argon's plan and migrate prompts, so it stays.
const shown = (description: string) => description.replace('Legacy-style Python 3 to modern typed ', 'Convert to ');

// 409 from Create plan or Start migration when the code was imported on another computer (#84): it's not the target's fault.
const elsewhere = (e: Error | null) => e instanceof ApiError && e.code === 'SNAPSHOT_UNAVAILABLE';

function Failure({ error }: { error: Error | null }) {
  return error ? (
    <span role="alert" className="flex items-start gap-1 text-sm font-medium text-failed">
      <XCircleIcon size={16} aria-hidden className="mt-0.5 flex-none" />
      <span>
        {error.message}
        {elsewhere(error) && (
          <>
            {' '}
            <Link to="/" className="whitespace-nowrap text-foreground underline underline-offset-4">
              Import it on this computer
            </Link>
          </>
        )}
      </span>
    </span>
  ) : null;
}

function Plan({ plan, runLink, onStart, starting, startError }: {
  plan: MigrationPlan;
  /** Set when ?run= already points at a run of this plan. The API would happily start a second one. */
  runLink?: string;
  onStart: () => void;
  starting: boolean;
  startError: Error | null;
}) {
  const qc = useQueryClient();
  const approve = useMutation({
    mutationFn: () => approvePlan(plan.plan_id),
    onSuccess: (p) => qc.setQueryData(['plan', p.plan_id], p),
  });
  const [StatusIcon, status, tone] = PLAN_STATUS[plan.status];
  const byId = new Map(plan.units.map((u) => [u.unit_id, u]));
  const label = (unitId: string) => byId.get(unitId)?.file_paths.join(', ') ?? unitId;
  const cycles = plan.units.filter((u) => u.is_cycle);

  return (
    <section className="flex min-w-0 flex-1 flex-col rounded-lg border bg-card">
      <div className="flex flex-wrap items-center gap-3 border-b px-4 py-3">
        <h2 className="text-xl leading-tight font-semibold">Migration plan</h2>
        <span className={cn('inline-flex h-6 items-center gap-1 rounded-sm px-2 text-sm', TONE_TINT[tone], TONE_TEXT[tone])}>
          <StatusIcon size={16} aria-hidden />
          {status}
        </span>
        <div className="ml-auto flex gap-3">
          {plan.status === 'draft' && (
            <Button onClick={() => approve.mutate()} disabled={approve.isPending}>
              {approve.isPending ? 'Approving…' : 'Approve plan'}
            </Button>
          )}
          {(plan.status === 'draft' || plan.status === 'approved') && (
            <Button variant={plan.status === 'approved' && !runLink ? 'default' : 'outline'} onClick={onStart} disabled={plan.status !== 'approved' || starting}>
              {starting ? 'Starting…' : runLink ? 'Start another run' : 'Start migration'}
            </Button>
          )}
          {runLink && (
            <Link to={runLink} className={buttonVariants()}>
              View run
            </Link>
          )}
        </div>
      </div>
      <div className="flex flex-col gap-1 px-4 pt-2">
        <p className="text-sm text-muted-foreground">
          {plan.status === 'approved' && plan.approved_at
            ? `Plan approved at ${time(plan.approved_at)}.`
            : plan.status === 'draft'
              ? 'Approve the plan to enable Start migration.'
              : 'A newer plan replaced this one. Create a new plan to migrate.'}{' '}
          Dependencies migrate before the modules that import them.
        </p>
        <Failure error={approve.error ?? startError} />
      </div>

      <div className="overflow-x-auto">
        <table className="mt-2 w-full border-collapse [&_td]:border-t [&_td]:px-3 [&_td]:py-2 [&_th]:px-3 [&_th]:py-2 [&_th]:text-sm [&_th]:font-medium [&_th]:whitespace-nowrap [&_th]:text-muted-foreground">
          <thead>
            <tr className="text-left">
              <th className="text-right">Order</th>
              <th>Unit</th>
              <th>Depends on</th>
              <th>Risk</th>
              <th className="text-right">Visible cases</th>
            </tr>
          </thead>
          <tbody>
            {plan.order.map((unitId, i) => {
              const u = byId.get(unitId);
              return (
                <tr key={unitId}>
                  <td className="text-right">{i + 1}</td>
                  <td className="font-mono text-sm">{label(unitId)}</td>
                  <td className={u?.depends_on_unit_ids.length ? 'font-mono text-sm' : 'text-muted-foreground'}>
                    {u?.depends_on_unit_ids.length ? u.depends_on_unit_ids.map(label).join(', ') : 'None'}
                  </td>
                  <td className="capitalize">{u?.risk}</td>
                  <td className={cn('text-right', !u?.visible_case_count && 'text-muted-foreground')}>{u?.visible_case_count || 'None'}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <div className="flex flex-col border-t md:flex-row">
        <div className="flex flex-1 flex-col gap-2 px-4 py-3 md:border-r">
          <span className="text-sm font-medium text-muted-foreground">Rationale</span>
          <p className="max-w-[60ch] whitespace-pre-line">{plan.rationale}</p>
        </div>
        <div className="flex flex-1 flex-col gap-2 px-4 py-3 max-md:border-t">
          <span className="text-sm font-medium text-muted-foreground">Risks and coverage gaps</span>
          <ul className="flex list-disc flex-col gap-1 pl-5">
            {[...plan.risks, ...plan.coverage_gaps].map((r) => (
              <li key={r}>{r}</li>
            ))}
            {cycles.length ? (
              <li>{cycles.map((u) => label(u.unit_id)).join(', ')} import each other, so each cycle migrates as one unit.</li>
            ) : (
              <li className="text-muted-foreground">No import cycles found.</li>
            )}
          </ul>
        </div>
      </div>
    </section>
  );
}

/** 05 Target and plan. Template: kit.js s05. The plan id lives in ?plan=, as no endpoint lists a repository's plans. */
export default function PlanScreen() {
  const { repoId = '' } = useParams();
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const planId = params.get('plan');
  const targets = useQuery({ queryKey: ['targets'], queryFn: listTargets });
  const plan = useQuery({ queryKey: ['plan', planId], queryFn: () => getPlan(planId!), enabled: Boolean(planId) });
  const runtime = useQuery({ queryKey: ['runtime', repoId], queryFn: () => getRuntime(repoId) });
  const [picked, setPicked] = useState<string>();
  const declared = runtime.data?.declared_python ?? null;
  // Nothing is pre-selected until the declared Python is known, so chess never starts on Modern Python 3.
  const target = picked ?? plan.data?.target_id ?? (runtime.data && targets.data ? matchingTarget(targets.data, declared) : undefined);
  const targetPython = targets.data?.find((t) => t.target_id === target)?.target_python;

  const create = useMutation({
    mutationFn: () => createPlan(repoId, target!),
    onSuccess: (p) => {
      qc.setQueryData(['plan', p.plan_id], p);
      setParams((s) => {
        s.set('plan', p.plan_id);
        s.delete('run');
        return s;
      });
    },
  });
  const start = useMutation({
    mutationFn: () => startRun(planId!),
    onSuccess: (r) => {
      qc.setQueryData(['run', r.run_id], r);
      navigate(`/repos/${encodeURIComponent(repoId)}/run?${new URLSearchParams({ plan: r.plan_id, run: r.run_id })}`);
    },
  });

  return (
    <main className="flex flex-1 flex-col gap-6 p-6 lg:flex-row lg:items-start">
      <section className="flex flex-none flex-col gap-3 lg:w-[360px] min-[1300px]:w-[400px]">
        {/* The action sits in the header, as on the plan panel: five targets push a footer button below a 720 px fold. */}
        <div className="flex items-center justify-between gap-3">
          <h2 className="text-xl leading-tight font-semibold">Target</h2>
          <Button variant={plan.data ? 'outline' : 'default'} onClick={() => create.mutate()} disabled={!target || create.isPending}>
            {create.isPending ? 'Creating plan…' : plan.data ? 'Create a new plan' : 'Create plan'}
          </Button>
        </div>
        {runtime.data && (
          <p className="text-sm">
            {declared ? `Python ${declared} (declared)` : 'No Python version declared'}
            {targetPython && ` → Python ${targetPython} (target)`}
          </p>
        )}
        {elsewhere(create.error) && <Failure error={create.error} />}
        {targets.isError && <p className="text-sm text-muted-foreground">Couldn’t load the targets: {targets.error.message}</p>}
        <fieldset className="flex flex-col gap-2" disabled={create.isPending}>
          <legend className="sr-only">Migration target</legend>
          {targets.data?.map((t) => (
            <Choice
              key={t.target_id}
              name="target"
              value={t.target_id}
              checked={target === t.target_id}
              disabled={!t.supported}
              onPick={(v) => {
                setPicked(v);
                create.reset();
              }}
              title={t.label}
              error={create.isError && target === t.target_id && !elsewhere(create.error) ? create.error.message : undefined}
            >
              {t.supported ? <span className="block text-sm">{shown(t.description)}</span> : null}
            </Choice>
          ))}
        </fieldset>
        {(create.isPending || plan.data) && (
          <span role="status" className="text-sm text-muted-foreground">
            {create.isPending
              ? 'Argon is ordering the units. This can take a minute.'
              : plan.data!.interaction_id
                ? `Plan created by Argon at ${time(plan.data!.created_at)}.`
                : `Plan created at ${time(plan.data!.created_at)}, without Argon.`}
          </span>
        )}
      </section>

      {plan.data ? (
        <Plan
          plan={plan.data}
          runLink={params.get('run') ? `/repos/${encodeURIComponent(repoId)}/run?${params}` : undefined}
          onStart={() => start.mutate()}
          starting={start.isPending}
          startError={start.error}
        />
      ) : (
        <section className="flex min-h-[320px] flex-1 flex-col items-center justify-center gap-2 rounded-lg border bg-card p-8 text-center">
          <ListNumbersIcon size={24} aria-hidden className="text-muted-foreground" />
          <h2 className="text-xl leading-tight font-semibold">
            {plan.isError ? 'Couldn’t load this plan' : planId ? 'Loading the plan…' : create.isPending ? 'Argon is ordering the units' : 'No plan yet'}
          </h2>
          <p className="max-w-[44ch] text-muted-foreground">
            {plan.isError
              ? plan.error.message
              : create.isPending
                ? 'This can take a minute. The plan appears here when Argon returns it.'
                : 'Pick a target and create a plan.'}
          </p>
        </section>
      )}
    </main>
  );
}
