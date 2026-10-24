import { describe, expect, it } from 'vitest';

import type { RunEvent, UnitRunState } from '@/types/contracts';

import { eventRows, field, terminalLines } from './events';
import { attemptOf } from './run';

// Payload shapes from run_bb0da2e79534.
let seq = 0;
const ev = (event_type: string, payload: Record<string, unknown>, unit_id: string | null = 'unit_storage', attempt: number | null = 1): RunEvent =>
  ({ run_id: 'run_x', sequence: ++seq, timestamp: '2026-10-24T10:00:00Z', event_type, unit_id, attempt, payload }) as RunEvent;
const gate = (gate: string, status: string, extra: Record<string, unknown> = {}, unit: string | null = 'unit_storage') =>
  ev('gate_result', { gate, status, required: true, summary: '', passed: 0, failed: 0, inconclusive: 0, ...extra }, unit, unit ? 1 : null);

const log = [
  ev('agent_turn', { text_excerpt: '""' }),
  ev('agent_tool_call', { tool: 'get_module_source', args_excerpt: '{"module_id": "module_orders"}' }),
  ev('agent_tool_result', { tool: 'get_module_source', ok: true }),
  ev('agent_tool_call', { tool: 'propose_patch', args_excerpt: '{"rationale": "Add type hints.", "files": [{"new_content": "# storage.py\\n' }),
  ev('patch_proposed', { files: ['storage.py'], summary: 'Modernise storage.py with type hints', bytes: 1404 }),
  ev('patch_applied', { workspace_revision: 'rev_9609090a4a3c' }),
  ev('agent_tool_result', { tool: 'propose_patch', ok: true, result_excerpt: '{"accepted": true, "workspace_revision": "rev_9609090a4a3c", "errors": []}' }),
  ev('verification_started', { workspace_revision: 'rev_9609090a4a3c', scope: 'unit' }),
  gate('compile', 'passed'),
  gate('differential', 'skipped'),
  gate('existing_tests', 'passed', { summary: '12 passed in 0.23s' }),
  ev('verification_failed', { verification_id: 'ver_88205f1702ff', failed_case_ids: [], counterexample_ids: [] }),
  ev('agent_tool_call', { tool: 'finish_unit', args_excerpt: '{"reason": "Verified; unit_storage has no differential cases."}' }),
  ev('agent_tool_result', { tool: 'finish_unit', ok: true, result_excerpt: '{"finished": true, "verified": false}' }),
  ev('unit_blocked', { reason: 'dependency unit_storage is blocked', verification_id: null }, 'unit_orders', null),
  gate('final_integration', 'passed', { passed: 26 }, null),
];
const name = (id: string) => ({ unit_storage: 'storage.py', unit_orders: 'orders.py', module_orders: 'orders.py' })[id] ?? id;

describe('eventRows', () => {
  const { rows, gates } = eventRows(log, name);

  it('hides results, patches, gates and empty Argon turns, and shows paths instead of IDs', () => {
    expect(rows.map((r) => [r.name, r.text])).toEqual([
      ['get_module_source', 'orders.py'],
      ['propose_patch', 'Modernise storage.py with type hints'],
      ['verification_failed', 'storage.py, attempt 1'],
      ['finish_unit', 'Verified; storage.py has no differential cases.'],
      ['unit_blocked', 'orders.py: dependency storage.py is blocked'],
    ]);
    expect(JSON.stringify(rows)).not.toMatch(/rev_|ver_/);
  });

  it('attaches the gates to the verdict, a skipped required gate included', () => {
    const verdict = rows[2];
    expect(verdict.kind).toBe('fail');
    expect(verdict.gates?.map((g) => `${g.label} ${g.value}`)).toEqual(['Compile passed', 'Differential skipped, required', 'Existing tests 12 passed']);
  });

  it('puts the verifier next to Argon’s reason', () => {
    expect(rows[3].detail?.map((d) => d.label)).toEqual(['Reason', 'Verifier']);
    expect(rows[1].detail?.at(-1)?.text).toBe('Accepted and applied to the workspace.');
  });

  it('opens a group for final gates that arrive without verification_started', () => {
    expect(gates?.title).toBe('Final verification');
    expect(gates?.gates.map((g) => g.value)).toEqual(['26 agree']);
  });
});

describe('attemptOf', () => {
  const unit = (status: UnitRunState['status'], attempts: number) => ({ unit_id: 'unit_storage', status, attempts }) as UnitRunState;
  const { attempts } = eventRows(log, name);

  it('reads a live unit’s attempt from its events, since the summary says 0 until the unit ends', () => {
    expect(attempts.get('unit_storage')).toBe(1);
    expect(attemptOf(unit('migrating', 0))).toBe(1);
    expect(attemptOf(unit('repairing', 0), attempts.get('unit_storage'))).toBe(2);
    expect(attemptOf(unit('blocked', 1), 3)).toBe(1);
  });
});

describe('field', () => {
  it('reads a key from an excerpt cut mid-string', () => {
    expect(field('{"summary": "Add hints", "rationale": "Keep the Decimal ro', 'rationale')).toBe('Keep the Decimal ro');
    expect(field('{"accepted": false, "errors": ["x', 'accepted')).toBe(false);
    expect(field('{"files": [', 'summary')).toBeUndefined();
  });
});

describe('terminalLines', () => {
  it('has a line for every event but the empty Argon turn, folded ones included', () => {
    expect(terminalLines(log, name).map((l) => [l.type, l.text, l.fail])).toEqual([
      ['agent_tool_call', 'get_module_source orders.py', false],
      ['agent_tool_result', 'get_module_source ok', false],
      ['agent_tool_call', 'propose_patch Modernise storage.py with type hints', false],
      ['patch_proposed', 'storage.py: Modernise storage.py with type hints', false],
      ['patch_applied', 'storage.py as rev_9609090a4a3c', false],
      ['agent_tool_result', 'propose_patch ok {"accepted": true, "workspace_revision": "rev_9609090a4a3c", "errors": []}', false],
      ['verification_started', 'storage.py, attempt 1', false],
      ['gate_result', 'storage.py, Compile passed', false],
      ['gate_result', 'storage.py, Differential skipped, required', false],
      ['gate_result', 'storage.py, Existing tests 12 passed in 0.23s', false],
      ['verification_failed', 'storage.py, attempt 1', true],
      ['agent_tool_call', 'finish_unit Verified; storage.py has no differential cases.', false],
      ['agent_tool_result', 'finish_unit ok {"finished": true, "verified": false}', false],
      ['unit_blocked', 'orders.py: dependency storage.py is blocked', true],
      ['gate_result', 'final, Final integration 26 agree', false],
    ]);
  });
});
