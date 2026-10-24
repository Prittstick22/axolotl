import { CaretRightIcon, DownloadSimpleIcon, InfoIcon, ListChecksIcon, XCircleIcon, type Icon } from '@phosphor-icons/react';
import { useQuery } from '@tanstack/react-query';
import { cn } from 'cn';
import type { ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { Link, useOutletContext, useParams, useSearchParams } from 'react-router-dom';

import { API_BASE, ApiError, exportUrl, getDependencyChanges, getPlan, getReport, getRunDiff, getRunSecurity, listAllRunEvents } from '@/api/client';
import { StatusBadge } from '@/components/StatusBadge';
import { Button, buttonVariants } from '@/components/ui/button';
import { narrativeBlocks } from '@/lib/narrative';
import { CLOSING_WAIT_MS, isLive, POLL_MS, useRun } from '@/lib/run';
import { RUN_STATE, TONE_BORDER, TONE_TEXT, TONE_TINT } from '@/lib/status';
import { changes as diffChanges } from '@/lib/diff';
import { scopedSearch } from '@/lib/utils';
import { Field } from '@/screens/Overview';
import type { MigrationPlan, RunEvent, RunReport, RunSummary, RunSecurity, SecurityPackages, SecurityTree } from '@/types/contracts';

// worker.py's final_verdict: `completed` only when every unit and the final gates passed.
const VERDICT_TEXT: Record<RunReport['verdict'], string> = {
  completed:
    'Every unit and the final check agree with the original code on the tested cases. That is agreement on those cases, not proof the code behaves the same everywhere.',
  completed_with_warnings: 'Not everything could be verified. Read these caveats before you use the migrated code.',
  failed: 'The migrated code isn’t verified. Don’t use it as it stands.',
  cancelled: 'The run was stopped before it finished. Units verified before that stay verified; the rest weren’t migrated.',
};
const time = (ms: number) => new Date(ms).toLocaleTimeString('en-GB');
const TEST_COUNTS = ['passed', 'failed', 'inconclusive'] as const;

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

function Downloads({ report }: { report: RunReport }) {
  // With nothing accepted the project is still the legacy code and the API answers 404,
  // so the panel says that instead of offering a button that fails when clicked.
  const accepted = report.units.some((u) => u.accepted_revision);
  return (
    <Panel title="Downloads">
      {accepted ? (
        <>
          <a href={exportUrl(report.run_id)} className={cn(buttonVariants(), 'self-start')}>
            <DownloadSimpleIcon aria-hidden />
            Download migrated codebase (.zip)
          </a>
          <p className="text-sm text-muted-foreground">
            A ZIP of the whole project at the final revision. Its <code className="font-mono">axolotl/MIGRATION.md</code> inside says which files were migrated
            and verified. It is built on the machine that ran the migration, so the download fails elsewhere.
          </p>
        </>
      ) : (
        <p className="text-sm text-muted-foreground">
          No unit was accepted in this run, so the project is still the legacy code and there is nothing to download.
        </p>
      )}
    </Panel>
  );
}

function advisoryUrl(url: string) {
  try {
    const parsed = new URL(url);
    return parsed.protocol === 'https:' && parsed.hostname === 'osv.dev' ? parsed.href : undefined;
  } catch {
    return undefined;
  }
}

/** security.py writes lower-case clauses; shown as sentences. */
const sentence = (s: string) => `${s.charAt(0).toUpperCase()}${s.slice(1)}${/[.!?]$/.test(s) ? '' : '.'}`;

function PackageChecks({ title, packages }: { title: string; packages: SecurityPackages }) {
  const unchecked = packages.status === 'not_run' || packages.status === 'nothing_pinned';
  return (
    <section className="flex flex-col gap-2 border-t pt-3">
      <h4 className="font-medium">{title}</h4>
      <p role={packages.status === 'not_run' ? 'alert' : undefined} className={cn('text-sm', unchecked ? 'text-warning' : 'text-muted-foreground')}>
        {sentence(packages.message)}
        {packages.status === 'checked' && ' A clean result only covers these pinned versions.'}
      </p>
      {packages.unpinned.map((item) => (
        <p key={`${item.name}-${item.source}`} className="text-sm text-warning">
          <code className="font-mono">{item.name}</code>: {item.note}
        </p>
      ))}
      {packages.checked.flatMap((item) => item.vulnerabilities.map((vulnerability) => (
        <div key={`${item.name}-${item.version}-${vulnerability.id}`} className="rounded-sm border px-3 py-2 text-sm">
          <p className="font-mono">{item.name} {item.version} · {vulnerability.id}</p>
          {vulnerability.summary && <p className="mt-1">{vulnerability.summary}</p>}
          <p className="text-muted-foreground">
            Fixed version: {vulnerability.fixed_versions.length ? vulnerability.fixed_versions.join(', ') : 'Not stated'}
          </p>
          {advisoryUrl(vulnerability.url) && <a className="underline underline-offset-4" href={advisoryUrl(vulnerability.url)} target="_blank" rel="noreferrer">View advisory</a>}
        </div>
      )))}
      {packages.warnings.map((warning) => <p key={warning} className="text-sm text-muted-foreground">{warning}</p>)}
    </section>
  );
}

function SecuritySection({ runId }: { runId: string }) {
  const security = useQuery({ queryKey: ['run-security', runId], queryFn: () => getRunSecurity(runId) });
  if (security.isPending) return <Panel title="Security"><p className="text-sm text-muted-foreground">Loading the security checks…</p></Panel>;
  if (security.isError) {
    return (
      <Panel title="Security">
        <p role="alert" className="text-sm text-failed">The security report could not be loaded: {security.error.message}</p>
        <p className="text-sm text-muted-foreground">No result is inferred from a failed request.</p>
      </Panel>
    );
  }
  const result: RunSecurity = security.data;
  const changeGroups = result.changes
    ? [
        ['Resolved package advisories', result.changes.fixed],
        ['Introduced package advisories', result.changes.introduced],
        ['Unchanged package advisories', result.changes.still_present],
      ] as const
    : [];
  const runtime = (side: SecurityTree, label: string) => (
    <p key={label} className="text-sm">
      {label}: Python {side.runtime.declared_python ?? 'version not declared'}
      {side.runtime.note && <span className="block text-warning">{side.runtime.note}</span>}
    </p>
  );
  return (
    <Panel title="Security">
      <p className="text-sm text-muted-foreground">{result.disclaimer}</p>
      <PackageChecks title="Legacy packages" packages={result.legacy.packages} />
      {result.migrated && <PackageChecks title="Migrated packages" packages={result.migrated.packages} />}
      {result.migrated && result.changes && (
        <section className="flex flex-col gap-2 border-t pt-3">
          <h4 className="font-medium">Known package advisory changes</h4>
          {changeGroups.map(([label, items]) => (
            <div key={label}>
              <h5 className="text-sm font-medium">{label}</h5>
              {items.length ? items.map((item) => (
                <p key={`${item.package}-${item.id}`} className="text-sm">
                  <code className="font-mono">{item.package} {item.version}</code> · {item.id}
                  {advisoryUrl(item.url) && <> · <a className="underline underline-offset-4" href={advisoryUrl(item.url)} target="_blank" rel="noreferrer">Advisory</a></>}
                </p>
              )) : <p className="text-sm text-muted-foreground">None reported.</p>}
            </div>
          ))}
        </section>
      )}
      <section className="flex flex-col gap-2 border-t pt-3">
        <h4 className="font-medium">Runtime</h4>
        {runtime(result.legacy, 'Legacy')}
        {result.migrated && runtime(result.migrated, 'Migrated')}
      </section>
      <section className="flex flex-col gap-1 border-t pt-3">
        <h4 className="font-medium">Code scan</h4>
        <p className="text-sm text-muted-foreground">No code scan ran. Only the declared packages and the Python runtime are checked.</p>
      </section>
      {result.warnings.map((warning) => <p key={warning} className="text-sm text-warning">{warning}</p>)}
    </Panel>
  );
}

function Report({ report, plan, run, repoId, search }: { report: RunReport; plan?: MigrationPlan; run?: RunSummary; repoId: string; search: string }) {
  const { icon: VerdictIcon, label, tone } = RUN_STATE[report.verdict];
  const paths = new Map(plan?.units.map((u) => [u.unit_id, u.file_paths.join(', ')]));
  const rank = (id: string) => plan?.order.indexOf(id) ?? 0;
  const units = [...report.units].sort((a, b) => rank(a.unit_id) - rank(b.unit_id));
  // Each unit is migrated on top of the last accepted one, so the last in plan order is the final tree.
  const final = plan && units.findLast((u) => u.accepted_revision)?.accepted_revision;
  const verified = units.filter((u) => u.status === 'passed').length;
  const tests = report.tests_passed + report.tests_failed + report.tests_inconclusive;
  const log = useQuery({ queryKey: ['run-events-all', report.run_id], queryFn: () => listAllRunEvents(report.run_id) });
  const diff = useQuery({ queryKey: ['run-diff', report.run_id], queryFn: () => getRunDiff(report.run_id) });
  const deps = useQuery({ queryKey: ['dependency-changes', report.run_id], queryFn: () => getDependencyChanges(report.run_id) });
  const files = new Map(plan?.units.map((u) => [u.unit_id, u.file_paths]));
  // The last gate_result per (unit, gate), as reports/builder.py counts them: every repair attempt re-runs the unit's gates.
  const lastGates = new Map<string, RunEvent>();
  const summaries = new Map<string, string>();
  for (const e of log.data ?? []) {
    if (!e.unit_id) continue;
    if (e.event_type === 'gate_result') lastGates.set(`${e.unit_id} ${String(e.payload.gate)}`, e);
    if (e.event_type === 'patch_proposed' && typeof e.payload.summary === 'string') summaries.set(e.unit_id, e.payload.summary);
  }
  const unitTests = (unitId: string) => {
    const [passed, failed, inconclusive] = TEST_COUNTS.map((k) =>
      [...lastGates.values()].filter((e) => e.unit_id === unitId).reduce((n, e) => n + (Number(e.payload[k]) || 0), 0),
    );
    if (!passed && !failed && !inconclusive) return 'None recorded';
    return `${passed} passed${failed ? `, ${failed} failed` : ''}${inconclusive ? `, ${inconclusive} inconclusive` : ''}`;
  };
  const unitDiff = (unitId: string) => diff.data?.files.filter((f) => files.get(unitId)?.includes(f.path)) ?? [];
  const lines = (unitId: string) => {
    const n = unitDiff(unitId).map((f) => diffChanges(f.unified_diff)).reduce((t, c) => ({ added: t.added + c.added, removed: t.removed + c.removed }), { added: 0, removed: 0 });
    return `+${n.added} / −${n.removed}`;
  };
  const withParam = (key: string, value: string) => scopedSearch(new URLSearchParams(search), { [key]: value });
  const repoPath = `/repos/${encodeURIComponent(repoId)}`;
  const python = deps.data?.python;

  return (
    <div className="flex flex-1 items-start gap-6 p-6">
      <div className="flex max-w-[880px] min-w-0 flex-1 flex-col gap-4 min-[1600px]:max-w-none">
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
              <Field label="Files changed">{report.changed_files.length}</Field>
              {python && (
                <Field label="Python">
                  {`${python.before.declared ?? 'not declared'} → ${python.after.declared ?? 'not declared'}`}
                  <Link to={{ pathname: `${repoPath}/dependencies`, search }} className="text-sm font-normal underline underline-offset-4">
                    Dependency changes
                  </Link>
                </Field>
              )}
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
            <span className="ml-auto text-sm text-muted-foreground">Tests count each check’s last attempt, as the totals above do.</span>
          </div>
          {log.isError && <p role="alert" className="px-4 pt-2 text-sm text-failed">Tests and Argon’s summaries couldn’t be loaded: {log.error.message}</p>}
          {diff.isError && <p role="alert" className="px-4 pt-2 text-sm text-failed">Line counts couldn’t be loaded: {diff.error.message}</p>}
          {deps.isError && <p role="alert" className="px-4 pt-2 text-sm text-failed">The Python version and dependency changes couldn’t be loaded: {deps.error.message}</p>}
          <div className="overflow-x-auto">
            <table className="w-full border-collapse [&_td]:px-3 [&_td]:py-2 [&_th]:px-3 [&_th]:py-2 [&_th]:text-sm [&_th]:font-medium [&_th]:whitespace-nowrap [&_th]:text-muted-foreground">
              <thead>
                <tr className="text-left">
                  <th>Unit</th>
                  <th>Status</th>
                  <th>Lines</th>
                  <th className="text-right">Attempts</th>
                  <th>Tests</th>
                  <th>Accepted revision</th>
                  <th><span className="sr-only">Links</span></th>
                </tr>
              </thead>
              {units.map((u) => {
                const summary = u.status === 'passed' ? summaries.get(u.unit_id) : undefined;
                const first = files.get(u.unit_id)?.[0];
                return (
                  // One tbody per unit, so its summary row reads as part of it.
                  <tbody key={u.unit_id} className="border-t text-sm">
                    <tr>
                      <td className="font-mono">{paths.get(u.unit_id) ?? u.unit_id}</td>
                      <td>
                        <StatusBadge status={u.status} />
                      </td>
                      <td className="whitespace-nowrap">{diff.data ? lines(u.unit_id) : diff.isError ? 'Unknown' : '…'}</td>
                      <td className="text-right">{u.attempts}</td>
                      <td>{log.data ? unitTests(u.unit_id) : log.isError ? 'Unknown' : '…'}</td>
                      <td className={cn('font-mono', !u.accepted_revision && 'text-muted-foreground')}>{u.accepted_revision ?? 'None'}</td>
                      <td className="whitespace-nowrap">
                        {unitDiff(u.unit_id).length > 0 && (
                          <Link to={{ pathname: `${repoPath}/changes`, search: withParam('unit', u.unit_id) }} className="underline underline-offset-4">
                            Diff
                          </Link>
                        )}
                        {first && (
                          <Link to={{ pathname: `${repoPath}/docs`, search: withParam('path', first) }} className="ml-3 underline underline-offset-4">
                            Docs
                          </Link>
                        )}
                        <Link to={{ pathname: `${repoPath}/run/${encodeURIComponent(u.unit_id)}`, search }} className="ml-3 underline underline-offset-4">
                          Verification
                        </Link>
                      </td>
                    </tr>
                    {summary && (
                      <tr>
                        <td colSpan={7} className="pt-0 text-muted-foreground">
                          <span className="font-medium">Argon’s summary, model-written:</span> {summary}
                        </td>
                      </tr>
                    )}
                  </tbody>
                );
              })}
            </table>
          </div>
        </section>
      </div>

      <div className="flex w-[360px] flex-none flex-col gap-4 min-[1300px]:w-[400px] min-[1600px]:w-[480px]">
        {report.performance && report.performance.units.length > 0 && (
          <Panel title="Sandbox runtime" note={report.performance.method}>
            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm [&_td]:py-2 [&_th]:py-2 [&_th]:pr-3">
                <caption className="sr-only">Before and after CPU time on tested cases only, in microseconds</caption>
                <thead><tr><th>Unit / cases</th><th>Before (µs)</th><th>After (µs)</th></tr></thead>
                <tbody>
                  {report.performance.units.map((u) => (
                    <tr key={u.unit_id}>
                      <td className="pr-3 break-all">
                        <span className="font-mono">{paths.get(u.unit_id) ?? u.unit_id}</span>
                        <p className="text-muted-foreground">{u.cases_timed} timed cases</p>
                        {u.within_noise && <p className="text-muted-foreground">Difference within measured spread</p>}
                      </td>
                      <td>{u.before_median_us.toFixed(2)}</td>
                      <td>{u.after_median_us.toFixed(2)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="text-sm text-muted-foreground">Untimed cases and units are omitted. These results do not establish production speed or a statistically significant change.</p>
          </Panel>
        )}
        {report.limitations.length > 0 && (
          <Panel title="Limitations" note="What this kind of check can’t tell you, for every run.">
            <ul className="flex list-disc flex-col gap-1 pl-5 text-sm">
              {report.limitations.map((l) => <li key={l}>{l}</li>)}
            </ul>
          </Panel>
        )}
        <Downloads report={report} />
        <SecuritySection runId={report.run_id} />
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
  const search = scopedSearch(params);
  const run = useRun(runId);
  // The worker writes the report about 20 s after the run ends. Until then the API answers 404.
  // Timed by the fetch, not the render, so the last 404 both stops the polling and says so.
  // A run with an error (no baseline, a backend restart) went through worker.py's _fail path, which writes no report.
  const ended = run.data && !isLive(run.data) ? Date.parse(run.data.updated_at) : undefined;
  const waiting = (e: Error | null, at: number) =>
    e instanceof ApiError && e.code === 'REPORT_NOT_READY' && !run.data?.error && (ended === undefined || at - ended < CLOSING_WAIT_MS);
  const report = useQuery({
    queryKey: ['report', runId],
    queryFn: () => getReport(runId!),
    enabled: Boolean(runId),
    refetchInterval: (q) => (waiting(q.state.error, q.state.errorUpdatedAt) ? POLL_MS : false),
  });
  const plan = useQuery({ queryKey: ['plan', run.data?.plan_id], queryFn: () => getPlan(run.data!.plan_id), enabled: Boolean(run.data) });
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
        <Report report={report.data} plan={plan.data} run={run.data} repoId={repoId} search={search} />
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
            <p className="max-w-[48ch] break-words text-muted-foreground">
              {run.data?.error ? `The run failed before writing one: ${run.data.error}` : 'The run ended without writing one. Its last events on the Run screen say why.'}
            </p>
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
