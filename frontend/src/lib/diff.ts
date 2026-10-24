/** Counted from the unified diff, minus its two header lines. */
export function changes(unified: string) {
  const lines = unified.split('\n');
  return {
    added: lines.filter((l) => l.startsWith('+') && !l.startsWith('+++ b/')).length,
    removed: lines.filter((l) => l.startsWith('-') && !l.startsWith('--- a/')).length,
  };
}
