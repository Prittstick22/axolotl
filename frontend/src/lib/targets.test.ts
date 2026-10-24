import { describe, expect, it } from 'vitest';

import { matchingTarget } from './targets';

const target = (target_id: string, supported: boolean) => ({ target_id, label: target_id, supported, description: '', unsupported_reason: null });
const today = [target('modern_python3', true), target('fastapi', false), target('python2_to_3', false)];

describe('matchingTarget', () => {
  it('picks Python 2 to 3 for a Python 2 project, and nothing while it is unsupported', () => {
    expect(matchingTarget(today, '2.7')).toBeUndefined();
    expect(matchingTarget([...today.slice(0, 2), target('python2_to_3', true)], '2.7')).toBe('python2_to_3');
  });

  it('picks the first supported target for anything else', () => {
    expect(matchingTarget(today, '>=3.10')).toBe('modern_python3');
    expect(matchingTarget(today, null)).toBe('modern_python3');
  });
});
