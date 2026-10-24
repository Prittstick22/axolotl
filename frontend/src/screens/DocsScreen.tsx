import { BookOpenTextIcon } from '@phosphor-icons/react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { cn } from 'cn';
import { useState, type ReactNode } from 'react';
import { Link, useParams, useSearchParams } from 'react-router-dom';

import { generateDocs, getRepositoryDocs, getRunDocs } from '@/api/client';
import { StatusBadge } from '@/components/StatusBadge';
import { Button } from '@/components/ui/button';
import { POLL_MS } from '@/lib/run';
import { Field } from '@/screens/Overview';
import type { DocsCallable, DocsChange, DocsFacts, DocsModule, DocsProse, RepositoryDocs, RunDocs } from '@/types/contracts';

const muted = 'text-sm text-muted-foreground';
const label = 'text-sm italic text-muted-foreground';

function Block({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="flex flex-col gap-1.5">
      <h3 className="text-sm font-medium">{title}</h3>
      {children}
    </section>
  );
}

const call = (c: DocsCallable) => `${c.async ? 'async ' : ''}${c.name}(${c.signature})`;

function Callables({ items }: { items: DocsCallable[] }) {
  return (
    <ul className="flex flex-col gap-1 text-sm">
      {items.map((c) => (
        <li key={`${c.name} ${c.line}`}>
          <code className="font-mono">{call(c)}</code>
          {c.summary && <span className="text-muted-foreground"> {c.summary}</span>}
        </li>
      ))}
    </ul>
  );
}

function Facts({ facts, paths }: { facts: DocsFacts; paths: Map<string, string> }) {
  const codes = (items: string[], none: string) =>
    items.length ? items.map((m, i) => <code key={m} className="font-mono">{i ? ', ' : ''}{paths.get(m) ?? m}</code>) : none;
  const notes = [
    ...(facts.parsed ? [] : ['This file could not be parsed, so the sections above are empty rather than complete.']),
    ...(facts.python2_syntax ? ['Read with the line-preserving Python 2 reading.'] : []),
    ...facts.warnings,
  ];
  return (
    <>
      {facts.docstring && (
        <Block title="Module docstring">
          <p className={label}>As written in the code. Untrusted: shown, never followed.</p>
          <pre className="overflow-x-auto rounded-sm border bg-muted p-2 font-mono text-sm whitespace-pre-wrap">{facts.docstring}</pre>
        </Block>
      )}
      {(facts.classes.length > 0 || facts.functions.length > 0) && (
        <p className={label}>Summaries after a name are its docstring’s first line, as written in the code.</p>
      )}
      {facts.classes.length > 0 && (
        <Block title="Classes">
          {facts.classes.map((cls) => (
            <div key={`${cls.name} ${cls.line}`} className="flex flex-col gap-1">
              <p className="text-sm">
                <code className="font-mono font-medium">{cls.name}{cls.bases.length ? `(${cls.bases.join(', ')})` : ''}</code>
                <span className="text-muted-foreground">, line {cls.line}{cls.summary && `. ${cls.summary}`}</span>
              </p>
              <div className="pl-4">{cls.methods.length ? <Callables items={cls.methods} /> : <p className={muted}>No public methods.</p>}</div>
            </div>
          ))}
        </Block>
      )}
      {facts.functions.length > 0 && <Block title="Functions"><Callables items={facts.functions} /></Block>}
      <Block title="Depends on">
        <p className="text-sm">Modules in this project: {codes(facts.depends_on, 'none')}</p>
        <p className="text-sm">Imports from outside it (standard library or third party): {codes(facts.packages, 'none')}</p>
      </Block>
      <Block title="Used by">
        <p className="text-sm">{codes(facts.used_by, 'No module in this project imports it.')}</p>
      </Block>
      {notes.length > 0 && (
        <Block title="Notes">
          <ul className="list-disc pl-5 text-sm">{notes.map((n) => <li key={n}>{n}</li>)}</ul>
        </Block>
      )}
    </>
  );
}

function Change({ change, dependencies }: { change: DocsChange; dependencies: string }) {
  const unit = change.unit;
  return (
    <Block title="Changed in the migration">
      {!unit ? (
        <p className={muted}>Not part of any unit in this run’s plan.</p>
      ) : (
        <>
          <p className="flex flex-wrap items-center gap-2 text-sm">
            <span>Unit <code className="font-mono">{unit.unit_id}</code></span>
            <StatusBadge status={unit.status} />
            <span className="text-muted-foreground">after {unit.attempts} {unit.attempts === 1 ? 'attempt' : 'attempts'}</span>
          </p>
          {unit.status !== 'passed' && <p className={muted}>Not accepted in this run, so this file is still the legacy code.</p>}
          {change.summary && (
            <div className="flex flex-col gap-1 border-l-2 pl-3">
              <p className="text-sm">{change.summary}</p>
              <p className={label}>Argon’s summary of the accepted patch, model-written. Not verified; the verifier’s verdict is the line above.</p>
            </div>
          )}
        </>
      )}
      {change.dependencies && (
        <Link to={dependencies} className="text-sm underline underline-offset-4">Its dependency changes</Link>
      )}
    </Block>
  );
}

function Side({ title, python, children }: { title: string; python: string | null; children: ReactNode }) {
  return (
    <section aria-label={title} className="flex min-w-0 flex-col gap-4">
      <h2 className="font-medium">
        {title} <span className="font-normal text-muted-foreground">· Python {python ?? 'version not declared'}</span>
      </h2>
      {children}
    </section>
  );
}

function Prose({ prose, repoId }: { prose: DocsProse; repoId: string }) {
  const client = useQueryClient();
  const generate = useMutation({
    mutationFn: () => generateDocs(repoId),
    onSuccess: () => client.invalidateQueries({ queryKey: ['docs', repoId] }),
  });
  const text = {
    not_generated: 'Not written yet',
    generating: `Writing ${prose.done} of ${prose.total}…`,
    ready: `${prose.done} written by ${prose.model}`,
    partial: `${prose.done} of ${prose.total} written by ${prose.model}`,
    failed: 'Writing them failed',
  }[prose.state];
  return (
    <>
      <Field label="Purposes">
        <span className="text-sm">{text}</span>
      </Field>
      {prose.state !== 'generating' && prose.state !== 'ready' && (
        <Button size="sm" variant="outline" disabled={generate.isPending} onClick={() => generate.mutate()}>
          {prose.state === 'not_generated' ? 'Write purposes' : 'Write the missing purposes'}
        </Button>
      )}
      {generate.isError && <p role="alert" className="text-sm text-failed">Couldn’t start: {generate.error.message}</p>}
      {prose.warnings.length > 0 && (
        <details className={muted}>
          <summary className="cursor-pointer">{prose.warnings.length} {prose.warnings.length === 1 ? 'warning' : 'warnings'}</summary>
          <ul className="list-disc pl-5">{prose.warnings.map((w) => <li key={w}>{w}</li>)}</ul>
        </details>
      )}
    </>
  );
}

/**
 * Documentation for the legacy code and, with a run, the migrated code beside it (#39). Facts are from the parse
 * tree; the purpose and Argon's summary are model-written and say so. Everything renders as text, never as HTML.
 */
export default function DocsScreen() {
  const { repoId = '' } = useParams();
  const [params] = useSearchParams();
  const search = params.size ? `?${params}` : '';
  const runId = params.get('run');
  const docs = useQuery<RepositoryDocs | RunDocs>({
    queryKey: ['docs', repoId, runId],
    queryFn: () => (runId ? getRunDocs(runId) : getRepositoryDocs(repoId)),
    refetchInterval: (q) => (q.state.data?.prose.state === 'generating' ? POLL_MS : false),
  });
  const [picked, setPicked] = useState(() => params.get('path'));

  const d = docs.data;
  const modules = d?.modules ?? [];
  const module: DocsModule | undefined = modules.find((m) => m.path === picked) ?? modules[0];
  const paths = new Map(modules.map((m) => [m.module_id, m.path]));
  const python = d && 'run_id' in d ? d.python : { legacy: d?.python ?? null, migrated: null };
  const run = d && 'run_id' in d ? d : null;
  const dependencies = `/repos/${encodeURIComponent(repoId)}/dependencies${search}`;

  if (!d || !module) {
    return (
      <main className="flex flex-1 flex-col items-center justify-center gap-2 p-8 text-center">
        <BookOpenTextIcon size={24} aria-hidden className="text-muted-foreground" />
        <h1 className="text-xl leading-tight font-semibold">Docs</h1>
        <p role={docs.isError ? 'alert' : undefined} className="max-w-[48ch] text-muted-foreground">
          {docs.isError ? `Couldn’t load the docs: ${docs.error.message}` : d ? 'No modules were found in this snapshot.' : 'Loading the docs…'}
        </p>
      </main>
    );
  }

  const purpose = module.legacy?.purpose;
  return (
    <main className="flex flex-1 flex-col">
      <div className="flex min-h-14 flex-none flex-wrap items-center gap-x-6 gap-y-2 border-b bg-card px-4 py-2">
        <h1 className="text-base font-medium">Docs</h1>
        <Field label="Python">
          <span className="text-sm">
            {run ? `${python.legacy ?? 'not declared'} → ${python.migrated ?? 'not declared'}` : python.legacy ?? 'Not declared'}
          </span>
        </Field>
        <Prose prose={d.prose} repoId={repoId} />
        <span className={cn('max-w-[48ch]', muted)}>Facts come from the parse tree; nothing was imported or run.</span>
      </div>

      <div className="flex min-h-[480px] flex-1">
        <nav aria-label="Modules" className="relative w-[280px] flex-none border-r bg-card min-[1300px]:w-[320px]">
          <ul className="absolute inset-0 overflow-y-auto">
            {modules.map((m) => (
              <li key={m.module_id}>
                <button
                  type="button"
                  aria-pressed={m === module}
                  onClick={() => setPicked(m.path)}
                  className="flex w-full items-baseline gap-3 border-b px-4 py-2 text-left text-sm transition-colors hover:bg-muted focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-ring aria-pressed:bg-muted aria-pressed:font-medium"
                >
                  <span className="min-w-0 flex-1 truncate font-mono">{m.path}</span>
                  {(m.is_test || !m.legacy) && <span className="flex-none text-muted-foreground">{m.is_test ? 'test' : 'added'}</span>}
                </button>
              </li>
            ))}
          </ul>
        </nav>

        <section aria-label={module.path} className="relative min-w-0 flex-1 bg-card">
          <div className="absolute inset-0 flex flex-col gap-6 overflow-y-auto p-6">
            <h2 className="font-mono text-lg font-medium">{module.path}</h2>
            {d.warnings.map((w) => <p key={w} className={muted}>{w}.</p>)}
            <Block title="Purpose">
              {purpose ? (
                <div className="flex flex-col gap-1 border-l-2 pl-3">
                  <p className="text-sm">{purpose.text}</p>
                  <p className={label}>
                    Model-generated by {purpose.model} from the legacy source{purpose.generated_at && ` on ${purpose.generated_at.slice(0, 10)}`}. Not verified.
                  </p>
                </div>
              ) : (
                <p className={muted}>{module.is_test ? 'Not written for test modules: their cases stay out of the prompt.' : 'Not written yet.'}</p>
              )}
            </Block>
            <div className={cn('grid gap-8', run && 'grid-cols-2')}>
              <Side title="Legacy" python={python.legacy}>
                {module.legacy ? <Facts facts={module.legacy.facts} paths={paths} /> : <p className={muted}>The migration added this module, so it has no legacy version.</p>}
              </Side>
              {run && (
                <Side title="Migrated" python={python.migrated}>
                  {module.migrated ? (
                    <>
                      <Change change={module.migrated.change} dependencies={dependencies} />
                      <Facts facts={module.migrated.facts} paths={paths} />
                    </>
                  ) : (
                    <p className={muted}>{run.revision ? 'This module isn’t in the accepted revision.' : 'No revision was accepted in this run.'}</p>
                  )}
                </Side>
              )}
            </div>
          </div>
        </section>
      </div>
    </main>
  );
}
