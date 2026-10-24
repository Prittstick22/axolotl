import { ArrowClockwiseIcon, CheckCircleIcon, EyeIcon, GitDiffIcon, XCircleIcon } from '@phosphor-icons/react';
import { useQuery, type UseQueryResult } from '@tanstack/react-query';
import { cn } from 'cn';
import { lazy, Suspense, useState } from 'react';
import { Link, useParams, useSearchParams } from 'react-router-dom';

import { getPlan, getRunDiff, getRunUnit } from '@/api/client';
import { StatusBadge } from '@/components/StatusBadge';
import { buttonVariants } from '@/components/ui/button';
import { changes } from '@/lib/diff';
import { gateChip } from '@/lib/events';
import { isLive, POLL_MS, useRun } from '@/lib/run';
import { GATE_STATUS, TONE_TEXT, type Tone } from '@/lib/status';
import { call, outcome } from '@/lib/values';
import { Sec } from '@/screens/ModuleInspector';
import { Field } from '@/screens/Overview';
import type { CaseOutcome, Counterexample, RunDiff, RunSummary, UnitDetail, UnitRunState, VerificationResult } from '@/types/contracts';

const DiffView = lazy(() => import('@/components/SourceView').then((m) => ({ default: m.DiffView })));

const IN_PROGRESS = new Set<UnitRunState['status']>(['queued', 'baselining', 'migrating', 'verifying', 'repairing']);
const CASE_STATUS: Record<Counterexample['status'], { icon: typeof CheckCircleIcon; label: string; tone: Tone }> = {
  open: { icon: ArrowClockwiseIcon, label: 'Open', tone: 'repairing' },
  resolved: { icon: CheckCircleIcon, label: 'Resolved', tone: 'passed' },
  unresolved: { icon: XCircleIcon, label: 'Unresolved', tone: 'failed' },
};
const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? '' : 's'}`;
const muted = 'text-sm text-muted-foreground';


function DiffPane({ unit, diff }: { unit: UnitRunState; diff: UseQueryResult<RunDiff> }) {
  const [picked, setPicked] = useState(0);
  const files = diff.data?.files ?? [];
  const file = files[picked] ?? files[0];

  const note = !unit.accepted_revision
    ? IN_PROGRESS.has(unit.status)
      ? 'Nothing accepted yet. The diff appears here once an attempt passes verification.'
      : unit.status === 'skipped'
        ? 'This unit was skipped, so its code wasn’t changed.'
        : 'No attempt was accepted, so there’s no diff to show.'
    : diff.isError
      ? `Couldn’t load the diff: ${diff.error.message}`
      : !diff.data
        ? 'Loading the diff…'
        : !file && 'The accepted revision leaves this unit’s files as they were.';
  if (note) return <p className="p-4 text-muted-foreground">{note}</p>;

  const { added, removed } = changes(file.unified_diff);
  return (
    <>
      <div className="flex h-10 flex-none items-center gap-3 border-b px-4 text-sm whitespace-nowrap">
        <GitDiffIcon size={20} aria-hidden className="flex-none" />
        {files.length > 1 ? (
          <div className="flex gap-1">
            {files.map((f, i) => (
              <button
                key={f.path}
                type="button"
                aria-pressed={f === file}
                onClick={() => setPicked(i)}
                className="rounded-sm px-2 py-0.5 font-mono transition-colors hover:bg-muted aria-pressed:bg-muted aria-pressed:font-medium"
              >
                {f.path}
              </button>
            ))}
          </div>
        ) : (
          <span className="font-mono font-medium">{file.path}</span>
        )}
        <span className="text-muted-foreground">
          {plural(added, 'line')} added, {removed} removed
        </span>
        <span className="ml-auto text-muted-foreground">Inline diff, read-only</span>
      </div>
      <div className="relative min-h-0 flex-1">
        <div className="absolute inset-0">
          <Suspense fallback={<span className={cn('block p-3', muted)}>Loading the editor…</span>}>
            <DiffView key={file.path} original={file.old_content} modified={file.new_content} path={file.path} />
          </Suspense>
        </div>
      </div>
    </>
  );
}

/** Template: kit.js s07, a table rather than the run screen's chips. The full summary is in the tooltip. */
function Gates({ verdict, pending }: { verdict?: VerificationResult; pending: boolean }) {
  if (!verdict) {
    return (
      <Sec label="Gates">
        <p className={muted}>{pending ? 'Not verified yet. Gates report here when this unit is verified.' : 'This unit was never verified.'}</p>
      </Sec>
    );
  }
  return (
    <Sec label={<>Gates on <span className="font-mono">{verdict.workspace_revision}</span></>}>
      <table className="w-full text-sm">
        <thead className="sr-only">
          <tr><th>Gate</th><th>Result</th><th>Detail</th></tr>
        </thead>
        <tbody>
          {verdict.gates.map((g) => {
            const { label, value } = gateChip(g);
            const { icon: Icon, tone } = GATE_STATUS[g.status] ?? GATE_STATUS.skipped;
            return (
              <tr key={g.gate} title={g.summary || undefined}>
                <td className="py-1 pr-3 align-top">{label}</td>
                <td className="py-1 pr-3 align-top">
                  <span className={cn('inline-flex items-center gap-1', TONE_TEXT[tone])}>
                    <Icon size={16} aria-hidden />
                    {g.status}
                  </span>
                </td>
                <td className="py-1 text-right align-top text-muted-foreground tabular-nums">
                  {value === g.status ? '' : value}
                  {g.status !== 'passed' && g.summary && <span className="block text-left">{g.summary}</span>}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </Sec>
  );
}

/** `now`: the case on the latest attempt, when that is a later one. */
function Case({ cx, now }: { cx: Counterexample; now?: CaseOutcome }) {
  const held = cx.visibility === 'held_out';
  // A sealed case shows no values even if the API sent some: the held-out suite depends on it.
  const sealed = cx.redacted || (held && !cx.exposed_to_model);
  const { icon: Icon, label, tone } = CASE_STATUS[cx.status];
  const value = 'min-w-0 font-mono break-words';
  return (
    <div className="flex flex-col gap-1 border-t py-2 first:border-t-0 first:pt-0">
      <div className="flex items-center gap-2 text-sm">
        <span className="font-mono font-medium">{cx.case_id}</span>
        <span className={cn('inline-flex items-center gap-1', held && cx.exposed_to_model ? 'text-repairing' : 'text-muted-foreground')}>
          {held && cx.exposed_to_model && <EyeIcon size={16} aria-hidden />}
          {!held ? 'Visible' : sealed ? 'Held-out case — redacted' : 'Held-out, exposed during repair'}
        </span>
        <span className={cn('ml-auto inline-flex items-center gap-1 whitespace-nowrap', TONE_TEXT[tone])}>
          <Icon size={16} aria-hidden />
          {label}
        </span>
      </div>
      {!sealed && (
        <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-sm">
          <dt className="text-muted-foreground">Input</dt>
          <dd className={value}>{call(cx.function, cx.args, cx.kwargs)}</dd>
          <dt className="text-muted-foreground">Old</dt>
          <dd className={value}>{outcome(cx.old)}</dd>
          <dt className="text-muted-foreground whitespace-nowrap">Attempt {cx.attempt}</dt>
          <dd className={value}>{outcome(cx.new)}</dd>
          {now && (
            <>
              <dt className="text-muted-foreground">Now</dt>
              <dd className={value}>{outcome(now)}</dd>
            </>
          )}
        </dl>
      )}
    </div>
  );
}

function Counterexamples({ detail, latest, pending }: { detail: UnitDetail; latest?: VerificationResult; pending: boolean }) {
  const cases = [...detail.counterexamples].sort((a, b) => a.attempt - b.attempt || a.case_id.localeCompare(b.case_id));
  const now = (cx: Counterexample) =>
    latest && latest.attempt > cx.attempt ? latest.comparisons.find((c) => c.case_id === cx.case_id && !c.redacted)?.new : undefined;
  return (
    <Sec label={cases.length ? `Counterexamples, ${cases.length}` : 'Counterexamples'}>
      {cases.length ? (
        <div className="flex flex-col">
          {cases.map((cx) => (
            <Case key={cx.counterexample_id} cx={cx} now={now(cx)} />
          ))}
        </div>
      ) : (
        <p className={muted}>{detail.verifications.length ? 'None. No compared case differed on any attempt.' : pending ? 'None yet.' : 'None.'}</p>
      )}
      {cases.some((cx) => cx.redacted || (cx.visibility === 'held_out' && !cx.exposed_to_model)) && (
        <p className={muted}>Argon never saw the redacted cases, so they still count as held-out checks.</p>
      )}
    </Sec>
  );
}

/** `detail`: the required gates that didn't pass, which decide a failed attempt. */
function verdictOf(v: VerificationResult, accepted: boolean) {
  if (v.all_required_gates_passed) return { icon: CheckCircleIcon, tone: 'passed' as const, text: accepted ? 'Passed, accepted' : 'Passed', detail: '' };
  const missed = v.gates.filter((g) => g.required && g.status !== 'passed').map((g) => `${gateChip(g).label} ${gateChip(g).value}`);
  return { icon: XCircleIcon, tone: 'failed' as const, text: v.gates.some((g) => g.status === 'failed') ? 'Failed' : 'Not verified', detail: missed.join(', ') };
}

function Attempts({ attempts, unit }: { attempts: VerificationResult[]; unit: UnitRunState }) {
  return (
    <Sec label="Attempt history">
      {attempts.length ? (
        attempts.map((v) => {
          const { icon: Icon, tone, text, detail } = verdictOf(v, v.workspace_revision === unit.accepted_revision);
          return (
            <div key={v.verification_id} className="flex flex-col text-sm">
              <div className="flex items-baseline justify-between gap-3">
                <span className="whitespace-nowrap">
                  Attempt {v.attempt} <span className="font-mono text-muted-foreground">{v.workspace_revision}</span>
                </span>
                <span className={cn('inline-flex items-center gap-1 whitespace-nowrap', TONE_TEXT[tone])}>
                  <Icon size={16} aria-hidden className="self-center" />
                  {text}
                </span>
              </div>
              {detail && <span className="text-muted-foreground">{detail}</span>}
            </div>
          );
        })
      ) : (
        <p className={muted}>{IN_PROGRESS.has(unit.status) ? 'No attempt verified yet.' : 'No attempt reached verification.'}</p>
      )}
    </Sec>
  );
}

function Unit({ run, unit, back }: { run: RunSummary; unit: UnitRunState; back: { pathname: string; search: string } }) {
  const live = isLive(run);
  // The unit's own state, not the run's: a unit can be skipped or blocked while the run goes on.
  const pending = IN_PROGRESS.has(unit.status);
  const plan = useQuery({ queryKey: ['plan', run.plan_id], queryFn: () => getPlan(run.plan_id) });
  const detail = useQuery({
    queryKey: ['run-unit', run.run_id, unit.unit_id],
    queryFn: () => getRunUnit(run.run_id, unit.unit_id),
    refetchInterval: live ? POLL_MS : false,
  });
  // Keyed on the accepted revision, so it loads again when the unit is accepted mid-run.
  const diff = useQuery({
    queryKey: ['run-diff', run.run_id, unit.unit_id, unit.accepted_revision],
    queryFn: () => getRunDiff(run.run_id, unit.unit_id),
    enabled: Boolean(unit.accepted_revision),
  });

  const name = plan.data?.units.find((u) => u.unit_id === unit.unit_id)?.file_paths.join(', ') ?? unit.unit_id;
  const attempts = [...(detail.data?.verifications ?? [])].sort((a, b) => a.attempt - b.attempt);
  // The verdict is the attempt that was accepted; until one is, the latest.
  const verdict = attempts.find((v) => v.workspace_revision === unit.accepted_revision) ?? attempts.at(-1);
  const total = Math.max(unit.attempts, attempts.at(-1)?.attempt ?? 0);

  return (
    <>
      <div className="flex min-h-14 flex-none flex-wrap items-center gap-x-6 gap-y-2 border-b bg-card px-4 py-2">
        <Link to={back} className={buttonVariants({ variant: 'outline' })}>
          Back to run
        </Link>
        <span aria-hidden className="h-6 w-px bg-border" />
        <h1 className="text-base font-medium">{name}</h1>
        <StatusBadge status={unit.status} />
        <Field label="Attempt">
          {verdict ? `${verdict.attempt} of ${total}${verdict.workspace_revision === unit.accepted_revision ? ', accepted' : ''}` : pending ? 'None yet' : 'None'}
        </Field>
        <Field label="Revision">
          {unit.accepted_revision ? (
            <span className="font-mono">{diff.data ? `${diff.data.base_revision} to ${diff.data.revision}` : '…'}</span>
          ) : (
            'Nothing accepted'
          )}
        </Field>
      </div>
      <div className="flex min-h-[480px] flex-1">
        <section aria-label="Diff" className="flex min-w-0 flex-1 flex-col bg-card">
          <DiffPane unit={unit} diff={diff} />
        </section>
        <aside aria-label="Verification" className="relative w-[440px] flex-none border-l bg-card min-[1300px]:w-[520px]">
          <div className="absolute inset-0 overflow-y-auto">
            {detail.data ? (
              <>
                <Gates verdict={verdict} pending={pending} />
                <Counterexamples detail={detail.data} latest={attempts.at(-1)} pending={pending} />
                <Attempts attempts={attempts} unit={unit} />
              </>
            ) : (
              <p className={cn('p-4', muted)}>{detail.isError ? `Couldn’t load this unit’s verification: ${detail.error.message}` : 'Loading the verification…'}</p>
            )}
          </div>
        </aside>
      </div>
    </>
  );
}

/** 07 Verification and diff, one unit of a run. Template: kit.js s07. Opened from a module on the Run screen. */
export default function VerificationScreen() {
  const { repoId = '', unitId = '' } = useParams();
  const [params] = useSearchParams();
  const run = useRun(params.get('run'));
  const back = { pathname: `/repos/${encodeURIComponent(repoId)}/run`, search: params.size ? `?${params}` : '' };
  const unit = run.data?.units.find((u) => u.unit_id === unitId);

  return (
    <main className="flex flex-1 flex-col">
      {unit && run.data ? (
        <Unit run={run.data} unit={unit} back={back} />
      ) : (
        <div className="flex flex-col items-start gap-3 p-6">
          <p className="text-muted-foreground">
            {run.data
              ? `This run has no unit ${unitId}.`
              : run.isError
                ? `Couldn’t load this run: ${run.error.message}`
                : params.get('run') ? 'Loading the run…' : 'No run in this address.'}
          </p>
          <Link to={back} className={buttonVariants({ variant: 'outline' })}>
            Back to run
          </Link>
        </div>
      )}
    </main>
  );
}
