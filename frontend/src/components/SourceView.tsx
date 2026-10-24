/**
 * Read-only Python source and diffs in Monaco, loaded from the bundle, never a CDN (the demo may be offline).
 * Only the core editor and the Python grammar are imported; lazy-load this file.
 */
import Editor, { DiffEditor, loader, type OnMount } from '@monaco-editor/react';
import * as monaco from 'monaco-editor/editor/editor.api';
import 'monaco-editor/languages/definitions/python/register';
import EditorWorker from 'monaco-editor/editor/editor.worker?worker';
// The diff's +/− gutter and its folded-lines bar are codicon glyphs. The core editor doesn't load
// their font, and the package exports only .js files, hence the path into node_modules.
import '../../node_modules/monaco-editor/esm/vs/base/browser/ui/codicons/codicon/codicon.css';
import { useEffect, useLayoutEffect, useRef, useSyncExternalStore } from 'react';

self.MonacoEnvironment = { getWorker: () => new EditorWorker() };
loader.config({ monaco });
// Monaco measures glyphs once; re-measure when IBM Plex Mono has actually loaded.
void document.fonts.ready.then(() => monaco.editor.remeasureFonts());

const html = document.documentElement;
const subscribe = (onChange: () => void) => {
  const observer = new MutationObserver(onChange);
  observer.observe(html, { attributes: true, attributeFilter: ['class'] });
  return () => observer.disconnect();
};
const isDark = () => html.classList.contains('dark');

/** Monochrome, like the template: keywords bold, comments muted. Hue stays reserved for run state. */
function defineTheme() {
  const token = (name: string) => getComputedStyle(html).getPropertyValue(`--${name}`).trim();
  const [ink, muted, card, tint, passed, passedTint, failed, failedTint] = ['foreground', 'muted-foreground', 'card', 'muted', 'passed', 'passed-tint', 'failed', 'failed-tint'].map(token);
  monaco.editor.defineTheme('axolotl', {
    base: isDark() ? 'vs-dark' : 'vs',
    inherit: false,
    rules: [
      { token: '', foreground: ink.slice(1) },
      { token: 'keyword', fontStyle: 'bold' },
      { token: 'comment', foreground: muted.slice(1) },
    ],
    colors: {
      'editor.background': card,
      'editor.foreground': ink,
      'editorLineNumber.foreground': muted,
      'editorLineNumber.activeForeground': ink,
      'editor.selectionBackground': tint,
      'editor.inactiveSelectionBackground': tint,
      'editorCursor.foreground': ink,
      // Bracket pairs are coloured by default; hue is for run state only.
      ...Object.fromEntries([1, 2, 3, 4, 5, 6].map((n) => [`editorBracketHighlight.foreground${n}`, ink])),
      // The template's diff: removed lines on the failed tint, added on the passed tint.
      'diffEditor.insertedLineBackground': passedTint,
      'diffEditor.removedLineBackground': failedTint,
      'diffEditor.insertedTextBackground': `${passed}33`,
      'diffEditor.removedTextBackground': `${failed}33`,
      'diffEditorGutter.insertedLineBackground': passedTint,
      'diffEditorGutter.removedLineBackground': failedTint,
    },
  });
}

function useTheme() {
  const dark = useSyncExternalStore(subscribe, isDark);
  useEffect(() => {
    defineTheme();
    monaco.editor.setTheme('axolotl');
  }, [dark]);
}

const OPTIONS = {
  readOnly: true,
  domReadOnly: true,
  fontFamily: '"IBM Plex Mono", monospace',
  fontSize: 14,
  lineHeight: 22,
  minimap: { enabled: false },
  scrollBeyondLastLine: false,
  renderLineHighlight: 'none',
  folding: false,
  lineNumbersMinChars: 3,
  overviewRulerLanes: 0,
  contextmenu: false,
  automaticLayout: true,
  padding: { top: 4, bottom: 4 },
  scrollbar: { verticalScrollbarSize: 10, horizontalScrollbarSize: 10 },
} satisfies monaco.editor.IEditorOptions;

const loading = <span className="p-3 text-sm text-muted-foreground">Loading the editor…</span>;

/** Baseline against the accepted revision, inline as in the template, with unchanged stretches folded away. */
export function DiffView({ original, modified, path, sideBySide = false }: { original: string; modified: string; path: string; sideBySide?: boolean }) {
  const editor = useRef<monaco.editor.IStandaloneDiffEditor>(null);
  useTheme();
  // The library disposes the models before the editor, which Monaco reports as an error. So it keeps
  // them, and this cleanup (layout, so it runs before the library's) detaches and disposes them first.
  useLayoutEffect(
    () => () => {
      const models = editor.current?.getModel();
      editor.current?.setModel(null);
      models?.original.dispose();
      models?.modified.dispose();
    },
    [],
  );
  return (
    <DiffEditor
      original={original}
      modified={modified}
      language="python"
      originalModelPath={`a/${path}`}
      modifiedModelPath={`b/${path}`}
      keepCurrentOriginalModel
      keepCurrentModifiedModel
      onMount={(instance) => (editor.current = instance)}
      theme="axolotl"
      beforeMount={defineTheme}
      loading={loading}
      options={{
        ...OPTIONS,
        originalEditable: false,
        renderSideBySide: sideBySide,
        // Monaco would quietly switch to inline below 900 px, which a 1024 px screen with a file list is.
        useInlineViewWhenSpaceIsLimited: false,
        // Room for the +/− column between the line numbers and the code, as in the template.
        lineDecorationsWidth: 20,
        renderOverviewRuler: false,
        hideUnchangedRegions: { enabled: true },
        ariaLabel: `${path}, accepted revision, read-only`,
        originalAriaLabel: `${path}, baseline, read-only`,
      }}
    />
  );
}

/** `start`-`end`: the symbol to scroll to and mark, 1-based and inclusive. */
export default function SourceView({ source, path, start, end }: { source: string; path: string; start?: number; end?: number }) {
  const editor = useRef<monaco.editor.IStandaloneCodeEditor>(null);
  const marks = useRef<monaco.editor.IEditorDecorationsCollection>(null);
  useTheme();

  const reveal = () => {
    if (!editor.current || !start || !end) return marks.current?.clear();
    editor.current.revealLinesInCenter(start, end);
    marks.current?.set([{ range: new monaco.Range(start, 1, end, 1), options: { isWholeLine: true, className: 'source-symbol' } }]);
  };
  useEffect(reveal, [start, end]);

  const onMount: OnMount = (instance) => {
    editor.current = instance;
    marks.current = instance.createDecorationsCollection();
    reveal();
  };

  return (
    <Editor
      value={source}
      language="python"
      path={path}
      theme="axolotl"
      beforeMount={defineTheme}
      onMount={onMount}
      loading={loading}
      options={{ ...OPTIONS, ariaLabel: `Source of ${path}, read-only` }}
    />
  );
}
