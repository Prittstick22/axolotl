import { describe, expect, it } from 'vitest';

import { narrativeBlocks } from './narrative';

describe('narrativeBlocks', () => {
  it('keeps headings, bullet lists and paragraphs, and drops bold', () => {
    const md = [
      '### Run report: `run_1`',
      '',
      '**Overall State:** `completed_with_warnings`',
      '',
      '#### Unit Summary',
      'Out of 6 units, 4 passed',
      'and 1 was blocked:',
      '',
      '* **`unit_customers`**: `passed` in 1 attempt.',
      '- **`unit_refunds`**: `blocked`.',
      '#### Final Verification',
      'Passed.',
    ].join('\n');
    expect(narrativeBlocks(md)).toEqual([
      { kind: 'heading', lines: ['Run report: `run_1`'] },
      { kind: 'para', lines: ['Overall State: `completed_with_warnings`'] },
      { kind: 'heading', lines: ['Unit Summary'] },
      { kind: 'para', lines: ['Out of 6 units, 4 passed', 'and 1 was blocked:'] },
      { kind: 'list', lines: ['`unit_customers`: `passed` in 1 attempt.', '`unit_refunds`: `blocked`.'] },
      { kind: 'heading', lines: ['Final Verification'] },
      { kind: 'para', lines: ['Passed.'] },
    ]);
  });
});
