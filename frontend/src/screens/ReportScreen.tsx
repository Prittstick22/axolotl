import { CaretRightIcon, DownloadSimpleIcon, InfoIcon, ListChecksIcon, XCircleIcon, type Icon } from '@phosphor-icons/react';
import { useQuery, type UseQueryResult } from '@tanstack/react-query';
import { cn } from 'cn';
import type { ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { Link, useOutletContext, useParams, useSearchParams } from 'react-router-dom';

import { API_BASE, ApiError, getPlan, getReport, listArtefacts } from '@/api/client';
import { StatusBadge } from '@/components/StatusBadge';
import { Button, buttonVariants } from '@/components/ui/button';
import { narrativeBlocks } from '@/lib/narrative';
import { CLOSING_WAIT_MS, isLive, POLL_MS, useRun } from '@/lib/run';
import { RUN_STATE, TONE_BORDER, TONE_TEXT, TONE_TINT } from '@/lib/status';
import { Field } from '@/screens/Overview';
import type { Artefact, MigrationPlan, RunReport, RunSummary } from '@/types/contracts';

// worker.py's final_verdict: `completed` only when every unit and the final gates passed.
const VERDICT_TEXT: Record<RunReport['verdict'], string> = {
  completed:
    'Every unit and the final check agree with the original code on the tested cases. That is agreement on those cases, not proof the code behaves the same everywhere.',
  completed_with_warnings: 'Not everything could be verified. Read these caveats before you apply the patch.',
  failed: 'The migrated code isn’t verified. Don’t apply the patch as it stands.',
  cancelled: 'The run was stopped before it finished. Units verified before that stay verified; the rest weren’t migrated.',
};
const KIND: Record<Artefact['kind'], string> = { patch: 'Patch', archive: 'Archive', report: 'Report' };
const time = (ms: number) => new Date(ms).toLocaleTimeString('en-GB');

function Empty({ icon: Icon, title, failed, children }: { icon: Icon; title: string; failed?: boolean; children: ReactNode }) {
  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-2 p-8 text-center">
      <Icon size={24} aria-hidden className={failed ? 'text-failed' : 'text-muted-foreground'} />
      <h2 className="text-xl leading-tight font-semibold">{title}</h2>
      {children}
    </div>
  );
}

function Panel({ title, note, children }: { title: string; note?: string; children: ReactNode }) {
  return (
    <section className="flex flex-col gap-2 rounded-lg border bg-card px-4 py-3">
      <h3 className="font-medium">{title}</h3>
      {note && <p className="text-sm text-muted-foreground">{note}</p>}
      {children}
    </section>
  );
}

/** Inline `code` in mono; everything else as written. */
const inline = (text: string) => text.split('`').map((s, i) => (i % 2 ? <code key={i} className="font-mono">{s}</code> : s));

function Narrative({ text }: { text: string | null }) {
  if (!text) {
    return (
      <section className="flex items-start gap-2 rounded-lg border bg-card px-4 py-3 text-sm">
        <InfoIcon size={20} aria-hidden className="flex-none text-muted-foreground" />
        <p>
          <span className="font-medium">Argon’s summary</span> isn’t available for this run. Everything else here comes from recorded results.
        </p>
      </section>
    );
  }
  return (
    <details className="group rounded-lg border bg-card">
      <summary className="flex cursor-pointer items-center gap-2 rounded-lg px-4 py-3 hover:bg-muted focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-ring">
        <CaretRightIcon size={16} aria-hidden className="text-muted-foreground transition-transform group-open:rotate-90 motion-reduce:transition-none" />
        <h3 className="font-medium">Argon’s summary</h3>
        <span className="ml-auto text-sm text-muted-foreground">Written by Argon</span>
      </summary>
      <div className="flex flex-col gap-2 border-t px-4 py-3 text-sm break-words">
        <p className="text-muted-foreground">Written by Argon from the recorded events. Check it against the evidence.</p>
        {narrativeBlocks(text).map((b, i) =>
          b.kind === 'heading' ? (
            <h4 key={i} className="mt-2 font-medium">{inline(b.lines[0])}</h4>
          ) : b.kind === 'list' ? (
            <ul key={i} className="flex list-disc flex-col gap-1 pl-5">
              {b.lines.map((l, j) => <li key={j}>{inline(l)}</li>)}
            </ul>
          ) : (
            <p key={i}>{inline(b.lines.join(' '))}</p>
          ),
        )}
      </div>
    </details>
  );
}

function Downloads({ query }: { query: UseQueryResult<Artefact[]> }) {
  const artefacts = query.data;
  const missing = (Object.keys(KIND) as Artefact['kind'][]).filter((k) => !artefacts?.some((a) => a.kind === k));
  return (
    <Panel title="Downloads">
      {query.isError ? (
        <p className="text-sm text-muted-foreground">Couldn’t load the file list: {query.error.message}</p>
      ) : !artefacts ? (
        <p className="text-sm text-muted-foreground">Loading the files…</p>
      ) : !artefacts.length ? (
        <p className="text-sm text-muted-foreground">No files were returned for this run.</p>
      ) : (
        <>
          <div className="flex flex-wrap gap-2">
            {artefacts.map((a) => (
              <a
                key={a.artefact_id}
                href={new URL(a.download_url, new URL(API_BASE, location.href)).href}
                title={`SHA-256 ${a.sha256}`}
                className={buttonVariants({ variant: 'outline' })}
              >
                <DownloadSimpleIcon aria-hidden />
                {KIND[a.kind]}
              </a>
            ))}
          </div>
          {missing.length > 0 && (
            <p className="text-sm text-muted-foreground">No {missing.map((k) => KIND[k].toLowerCase()).join(' or ')} was returned for this run.</p>
          )}
        </>
      )}
    </Panel>
  );
}

function Report({ report, plan, artefacts, run }: { report: RunReport; plan?: MigrationPlan; artefacts: UseQueryResult<Artefact[]>; run?: RunSummary }) {
  const { icon: VerdictIcon, label, tone } = RUN_STATE[report.verdict];
  const paths = new Map(plan?.units.map((u) => [u.unit_id, u.file_paths.join(', ')]));
  const rank = (id: string) => plan?.order.indexOf(id) ?? 0;
  const units = [...report.units].sort((a, b) => rank(a.unit_id) - rank(b.unit_id));
  // Each unit is migrated on top of the last accepted one, so the last in plan order is the final tree.
  const final = plan && units.findLast((u) => u.accepted_revision)?.accepted_revision;
  const verified = units.filter((u) => u.status === 'passed').length;
  const tests = report.tests_passed + report.tests_failed + report.tests_inconclusive;

  return (
    <div className="flex flex-1 items-start gap-6 p-6">
      <div className="flex max-w-[880px] min-w-0 flex-1 flex-col gap-4">
        <section className={cn('flex gap-3 rounded-lg border px-5 py-4', TONE_TINT[tone], TONE_BORDER[tone])}>
          <VerdictIcon size={24} aria-hidden className={cn('mt-1 flex-none', TONE_TEXT[tone])} />
          <div className="flex min-w-0 flex-1 flex-col gap-3">
            <div className="flex flex-col gap-1">
              <h2 className={cn('text-2xl leading-tight font-semibold', TONE_TEXT[tone])}>{label}</h2>
              <p>{VERDICT_TEXT[report.verdict]}</p>
              {report.verdict === 'failed' && run?.error && <p className="font-medium text-failed">{run.error}</p>}
            </div>
            {report.coverage_caveats.length > 0 && (
              <ul className="flex list-disc flex-col gap-1 pl-5">
                {report.coverage_caveats.map((c) => <li key={c}>{c}</li>)}
              </ul>
            )}
            <hr className={TONE_BORDER[tone]} />
            <div className="flex flex-wrap gap-x-8 gap-y-2 font-medium">
              <Field label="Units">{`${verified} of ${units.length} verified`}</Field>
              <Field label="Tests">
                {tests ? `${report.tests_passed} passed, ${report.tests_failed} failed${report.tests_inconclusive ? `, ${report.tests_inconclusive} inconclusive` : ''}` : 'None run'}
              </Field>
              <Field label="Counterexamples">{`${report.counterexamples_found} found, ${report.counterexamples_resolved} resolved`}</Field>
              {final && (
                <Field label="Final revision">
                  <span className="font-mono">{final}</span>
                </Field>
              )}
            </div>
          </div>
        </section>

        <section className="rounded-lg border bg-card">
          <div className="flex items-baseline gap-4 border-b px-4 py-3">
            <h2 className="text-xl leading-tight font-semibold">Units</h2>
            <span className="ml-auto text-sm text-muted-foreground">
              {report.changed_files.length} changed {report.changed_files.length === 1 ? 'file' : 'files'}
            </span>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full border-collapse [&_td]:border-t [&_td]:px-3 [&_td]:py-2 [&_th]:px-3 [&_th]:py-2 [&_th]:text-sm [&_th]:font-medium [&_th]:whitespace-nowrap [&_th]:text-muted-foreground">
              <thead>
                <tr className="text-left">
                  <th>Unit</th>
                  <th>Status</th>
                  <th className="text-right">Attempts</th>
                  <th>Accepted revision</th>
                </tr>
              </thead>
              <tbody>
                {units.map((u) => (
                  <tr key={u.unit_id}>
                    <td className="font-mono text-sm">{paths.get(u.unit_id) ?? u.unit_id}</td>
                    <td>
                      <StatusBadge status={u.status} />
                    </td>
                    <td className="text-right">{u.attempts}</td>
                    <td className={cn('font-mono text-sm', !u.accepted_revision && 'text-muted-foreground')}>{u.accepted_revision ?? 'None'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      </div>

      {/* No Runtime panel: report.performance is null until the runner times cases (#46). */}
      <div className="flex w-[360px] flex-none flex-col gap-4 min-[1300px]:w-[400px]">
        {report.limitations.length > 0 && (
          <Panel title="Limitations" note="What this kind of check can’t tell you, for every run.">
            <ul className="flex list-disc flex-col gap-1 pl-5 text-sm">
              {report.limitations.map((l) => <li key={l}>{l}</li>)}
            </ul>
          </Panel>
        )}
        <Downloads query={artefacts} />
        <Narrative text={report.narrative} />
      </div>
    </div>
  );
}

/** 08 Final report. Template: kit.js s08. Everything but Argon's summary comes from recorded results. */
export default function ReportScreen() {
  const { repoId = '' } = useParams();
  const [params] = useSearchParams();
  const navSlot = useOutletContext<HTMLElement | null>();
  const runId = params.get('run');
  const search = params.size ? `?${params}` : '';
  const run = useRun(runId);
  // The worker writes the report about 20 s after the run ends. Until then the API answers 404.
  // Timed by the fetch, not the render, so the last 404 both stops the polling and says so.
  const ended = run.data && !isLive(run.data) ? Date.parse(run.data.updated_at) : undefined;
  const waiting = (e: Error | null, at: number) =>
    e instanceof ApiError && e.code === 'REPORT_NOT_READY' && (ended === undefined || at - ended < CLOSING_WAIT_MS);
  const report = useQuery({
    queryKey: ['report', runId],
    queryFn: () => getReport(runId!),
    enabled: Boolean(runId),
    refetchInterval: (q) => (waiting(q.state.error, q.state.errorUpdatedAt) ? POLL_MS : false),
  });
  const plan = useQuery({ queryKey: ['plan', run.data?.plan_id], queryFn: () => getPlan(run.data!.plan_id), enabled: Boolean(run.data) });
  const artefacts = useQuery({ queryKey: ['artefacts', runId], queryFn: () => listArtefacts(runId!), enabled: report.isSuccess });
  const toRun = { pathname: `/repos/${encodeURIComponent(repoId)}/run`, search };

  if (!runId) {
    return (
      <main className="flex flex-1 flex-col">
        <Empty icon={ListChecksIcon} title="No run picked">
          <p className="max-w-[44ch] text-muted-foreground">The report belongs to a run. Approve a plan and start one first.</p>
          <Link to={{ pathname: `/repos/${encodeURIComponent(repoId)}/plan`, search }} className={cn(buttonVariants(), 'mt-2')}>
            Go to plan
          </Link>
        </Empty>
      </main>
    );
  }
  const err = report.error;
  return (
    <main className="flex flex-1 flex-col">
      {navSlot &&
        createPortal(
          <span className="text-sm whitespace-nowrap text-muted-foreground">
            Run ID <span className="font-mono text-foreground">{runId}</span>
          </span>,
          navSlot,
        )}
      {report.data ? (
        <Report report={report.data} plan={plan.data} artefacts={artefacts} run={run.data} />
      ) : err instanceof ApiError && err.code === 'REPORT_NOT_READY' ? (
        waiting(err, report.errorUpdatedAt) ? (
          <Empty icon={ListChecksIcon} title="Report not ready yet">
            <p className="max-w-[48ch] text-muted-foreground">
              Axolotl writes the report after final verification.{' '}
              {ended === undefined
                ? run.data && `${run.data.units_verified} of ${run.data.units_total} units verified so far.`
                : 'The run has ended, so it should arrive within a minute.'}
            </p>
            <Link to={toRun} className={cn(buttonVariants(), 'mt-2')}>
              Go to run
            </Link>
          </Empty>
        ) : (
          <Empty icon={ListChecksIcon} title="No report for this run">
            <p className="max-w-[48ch] text-muted-foreground">The run ended without writing one. Its last events on the Run screen say why.</p>
            <Link to={toRun} className={cn(buttonVariants(), 'mt-2')}>
              Go to run
            </Link>
          </Empty>
        )
      ) : err ? (
        <Empty icon={XCircleIcon} title="Couldn’t load the report" failed>
          <p className="max-w-[52ch] text-muted-foreground">
            {/* A 4xx is about the request (no such run), so the server's own words say it best. */}
            {!(err instanceof ApiError) || (err.status >= 400 && err.status < 500)
              ? err.message.replace(/\.?$/, '.')
              : `${err.status ? `The server returned an error (${err.status})` : `Couldn’t reach the API at ${API_BASE}`} at ${time(report.errorUpdatedAt)}. The run’s results are still recorded, so nothing is lost.`}
          </p>
          <div className="mt-2 flex gap-3">
            <Button onClick={() => void report.refetch()} disabled={report.isFetching}>
              {report.isFetching ? 'Trying…' : 'Try again'}
            </Button>
            <Link to={toRun} className={buttonVariants({ variant: 'outline' })}>
              Go to run
            </Link>
          </div>
        </Empty>
      ) : (
        <p className="p-6 text-muted-foreground">Loading the report…</p>
      )}
    </main>
  );
}
