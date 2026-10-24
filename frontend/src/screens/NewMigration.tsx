import { InfoIcon } from '@phosphor-icons/react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';

import { importRepository, listDemoFixtures } from '@/api/client';
import { Choice } from '@/components/Choice';
import { Button } from '@/components/ui/button';
import { readFolder } from '@/lib/folder';

const FOLDER = 'folder';
const size = (bytes: number) => (bytes < 1024 * 1024 ? `${Math.ceil(bytes / 1024)} KB` : `${(bytes / 1024 / 1024).toFixed(1)} MB`);

/** 01 New migration: import the demo fixture or a folder. Errors are the API's own, on the choice they're about. */
export default function NewMigration() {
  const navigate = useNavigate();
  const fixtures = useQuery({ queryKey: ['demo-fixtures'], queryFn: listDemoFixtures });
  const [picked, setPicked] = useState<string>();
  const [folder, setFolder] = useState<ReturnType<typeof readFolder>>();
  const [name, setName] = useState('');
  const source = picked ?? fixtures.data?.[0]?.demo_fixture_id ?? FOLDER;
  const fixture = fixtures.data?.find((f) => f.demo_fixture_id === source);

  const run = useMutation({
    mutationFn: async () => {
      if (source !== FOLDER) return importRepository({ demo_fixture_id: source }, name.trim() || undefined);
      if (!folder?.files.length) throw new Error('Nothing in this folder can be imported. Caches, build output, binaries and secrets are skipped.');
      // Without a name the API calls every folder "uploaded-folder".
      return importRepository({ files: folder.files }, name.trim() || folder.name);
    },
    onSuccess: (record) => navigate(`/repos/${encodeURIComponent(record.repository_id)}`),
  });
  const pick = (value: string) => {
    setPicked(value);
    run.reset();
  };
  // Picking another source clears the error, so any error is about the source still picked.
  const errorOn = (value: string) => (run.isError && source === value ? run.error.message : undefined);

  return (
    <main className="mx-auto flex w-full max-w-[640px] flex-col gap-6 px-4 pt-8 pb-16 [@media(min-height:800px)]:pt-16">
      <div className="flex flex-col gap-2">
        <h1 className="text-2xl leading-tight font-semibold">New migration</h1>
        <p className="text-muted-foreground">
          Import a Python project. Axolotl maps its modules and imports, then migrates and verifies one unit at a time.
        </p>
      </div>
      <div className="flex items-start gap-2 rounded-md border bg-card px-4 py-3">
        <InfoIcon size={20} aria-hidden className="flex-none text-muted-foreground" />
        {/* backend/app/ingestion/trust.py: an upload can run only when every file exactly matches a trusted tree in the repo. */}
        <span>Uploads are analysed statically and never run, unless the upload is an exact copy of a trusted demo project, such as chess. Only demo fixtures and those exact copies can be migrated.</span>
      </div>

      <form
        className="flex flex-col gap-6"
        onSubmit={(event) => {
          event.preventDefault();
          run.mutate();
        }}
      >
        <fieldset className="flex flex-col gap-2" disabled={run.isPending}>
          <legend className="mb-2 text-sm font-medium text-muted-foreground">Source</legend>
          {fixtures.isError && (
            <p className="text-sm text-muted-foreground">Couldn’t load the demo fixtures: {fixtures.error.message}</p>
          )}
          {fixtures.data?.map((f) => (
            <Choice
              key={f.demo_fixture_id}
              name="source"
              value={f.demo_fixture_id}
              checked={source === f.demo_fixture_id}
              onPick={pick}
              error={errorOn(f.demo_fixture_id)}
              title={<>Demo fixture: <span className="font-mono text-sm">{f.name}</span></>}
            >
              {f.description} Trusted, so it can run in the sandbox.
            </Choice>
          ))}
          <Choice name="source" value={FOLDER} checked={source === FOLDER} onPick={pick} error={errorOn(FOLDER)} title="Upload a folder">
            Pick the project’s top folder. Up to 500 files and 50 MB. Static analysis only, unless it’s an exact copy of a trusted demo project.
          </Choice>
          {source === FOLDER && (
            <div className="flex flex-col gap-2 pt-2">
              <label className="flex flex-col gap-2">
                <span className="text-sm font-medium text-muted-foreground">Folder</span>
                <input
                  type="file"
                  // Chrome's folder picker. React has no prop for it, so it goes through as a plain attribute.
                  {...{ webkitdirectory: '' }}
                  required
                  onChange={(event) => {
                    setFolder(event.target.files?.length ? readFolder(event.target.files) : undefined);
                    run.reset();
                  }}
                  className="text-sm file:mr-3 file:h-8 file:rounded-md file:border file:border-input file:bg-card file:px-3 file:font-medium file:text-foreground hover:file:bg-muted"
                />
              </label>
              <p role="status" className="text-sm text-muted-foreground">
                {folder &&
                  // Chrome's own label counts every file picked, so this accounts for the difference.
                  `Uploading ${folder.files.length} of ${folder.files.length + folder.skipped} files, ${size(folder.bytes)}.${folder.skipped ? ` Skipped ${folder.skipped}: caches, build output, binaries and secrets such as .env.` : ''}`}
              </p>
            </div>
          )}
        </fieldset>

        <label className="flex flex-col gap-2">
          <span className="text-sm font-medium text-muted-foreground">Project name</span>
          <input
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder={source === FOLDER ? folder?.name || 'Name of the folder' : fixture?.name}
            disabled={run.isPending}
            className="h-8 w-full max-w-80 rounded-sm border border-input bg-card px-2 font-mono text-sm placeholder:text-muted-foreground"
          />
        </label>

        <div className="flex flex-wrap items-center gap-3">
          <Button type="submit" disabled={run.isPending}>
            {run.isPending ? 'Importing…' : 'Import repository'}
          </Button>
          {run.isPending && (
            <span role="status" className="text-sm text-muted-foreground">
              Mapping modules and imports.
            </span>
          )}
        </div>
      </form>
    </main>
  );
}
