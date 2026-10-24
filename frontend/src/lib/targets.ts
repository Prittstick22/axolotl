import type { MigrationTarget } from '@/types/contracts';

/**
 * The target that matches the declared Python, or none while that target is unsupported.
 * runtime.py says "2.7" only when the metadata rules Python 3 out; otherwise it's the raw requirement or null.
 */
export function matchingTarget(targets: MigrationTarget[], declared: string | null): string | undefined {
  const match = targets.find((t) => (declared?.startsWith('2.') ? t.target_id === 'python2_to_3' : t.supported));
  return match?.supported ? match.target_id : undefined;
}
