import type { GateResult, GateStatus, RunEvent } from '@/types/contracts';

export type GateChip = { gate: string; label: string; status: GateStatus; value: string; summary: string };
export type GateGroup = { title: string; gates: GateChip[] };
export type EventRow = {
  sequence: number;
  timestamp: string;
  name: string;
  text: string;
  kind: 'event' | 'ok' | 'wait' | 'repair' | 'fail';
  detail?: { label: string; text: string }[];
  gates?: GateChip[];
};

type Payload = Record<string, unknown>;
const str = (v: unknown) => (typeof v === 'string' ? v : '');
const words = (v: unknown) => str(v).replaceAll('_', ' ');
const list = (v: unknown) => (Array.isArray(v) ? v.map(String) : []);

/** Excerpts are JSON cut at a length limit, so when a parse fails the key is read with a regex. */
export function field(excerpt: unknown, key: string): unknown {
  try {
    return (JSON.parse(str(excerpt)) as Payload)[key];
  } catch {
    const m = new RegExp(`"${key}":\\s*(?:(true|false)|"((?:[^"\\\\]|\\\\.)*))`).exec(str(excerpt));
    if (!m) return undefined;
    if (m[1]) return m[1] === 'true';
    try {
      return JSON.parse(`"${m[2]}"`) as string;
    } catch {
      return m[2];
    }
  }
}

const GATE_LABEL: Record<string, string> = {
  compile: 'Compile',
  differential: 'Differential',
  impacted_tests: 'Impacted tests',
  existing_tests: 'Existing tests',
  sandbox_policy: 'Sandbox policy',
  final_integration: 'Final integration',
};

export function gateChip(p: Payload | GateResult): GateChip {
  const status = str(p.status) as GateStatus;
  const summary = str(p.summary);
  const tests = /^(\d+) passed/.exec(summary)?.[1];
  const value = Number(p.failed)
    ? `${p.failed} differ`
    : Number(p.inconclusive)
      ? `${p.inconclusive} inconclusive`
      : status === 'skipped'
        ? p.required ? 'skipped, required' : 'skipped'
        : tests
          ? `${tests} passed`
          : Number(p.passed) ? `${p.passed} agree` : status;
  return { gate: str(p.gate), label: GATE_LABEL[str(p.gate)] ?? words(p.gate), status, value, summary };
}

// Covered by other rows: each tool call shows its result, propose_patch shows the patch,
// run_verification starts each verification, and gates show as chips on the verdict.
const HIDDEN = new Set(['agent_tool_result', 'patch_proposed', 'patch_applied', 'verification_started', 'gate_result']);

/**
 * The run's events as the log shows them (review item 3): state changes, Argon's tool calls,
 * verdicts with their gates and patch outcomes, with paths instead of internal IDs. `name` turns a
 * unit or module ID into its file path. `gates` is the latest verification, finished or not, and
 * `attempts` the highest patch attempt seen per unit.
 */
export function eventRows(log: RunEvent[], name: (id: string) => string) {
  const results = new Map<number, Payload>();
  const calls: RunEvent[] = [];
  for (const e of log) {
    if (e.event_type === 'agent_tool_call') calls.push(e);
    if (e.event_type === 'agent_tool_result') {
      const i = calls.findIndex((c) => c.payload.tool === e.payload.tool);
      if (i >= 0) results.set(calls.splice(i, 1)[0].sequence, e.payload);
    }
  }

  const ids = (s: string) => s.replace(/\b(unit|module)_\w+/g, name);
  const rows: EventRow[] = [];
  let gates: GateGroup | undefined;
  let open = false;
  let patch: EventRow | undefined;
  const attempts = new Map<string, number>();
  for (const e of log) {
    const p = e.payload;
    const unit = e.unit_id ? name(e.unit_id) : '';
    if (e.unit_id && e.attempt) attempts.set(e.unit_id, Math.max(attempts.get(e.unit_id) ?? 0, e.attempt));
    // The final verification has no verification_started: its first gate opens the group.
    if (e.event_type === 'verification_started' || (e.event_type === 'gate_result' && !open)) {
      gates = { title: unit ? `${unit}, attempt ${e.attempt ?? 1}` : 'Final verification', gates: [] };
      open = true;
    }
    if (e.event_type === 'gate_result') gates!.gates.push(gateChip(p));
    // The call's args excerpt is cut inside the file contents, often before the summary.
    if (e.event_type === 'patch_proposed' && patch) patch.text = str(p.summary) || patch.text;
    if (HIDDEN.has(e.event_type)) continue;

    const row: EventRow = { sequence: e.sequence, timestamp: e.timestamp, name: e.event_type, text: unit, kind: 'event' };
    switch (e.event_type) {
      case 'run_created':
        row.text = words(p.target_id);
        break;
      case 'run_state_changed':
      case 'unit_state_changed':
        row.text = [unit, `${words(p.from)} to ${words(p.to)}`].filter(Boolean).join(' ');
        break;
      case 'baseline_captured':
        row.text = `${p.cases_captured} cases recorded${Number(p.cases_failed_to_capture) ? `, ${p.cases_failed_to_capture} couldn’t be recorded` : ''}`;
        break;
      case 'agent_turn': {
        // text_excerpt is a JSON string, "\"\"" when Argon only called tools.
        let text = str(p.text_excerpt);
        try {
          text = str(JSON.parse(text));
        } catch {
          // Cut short, so not valid JSON: show it as it is.
        }
        if (!text.trim()) continue;
        row.name = 'Argon';
        row.text = ids(text);
        row.detail = [{ label: 'Argon wrote', text }];
        break;
      }
      case 'agent_tool_call': {
        const tool = str(p.tool);
        const result = results.get(e.sequence);
        row.name = tool;
        if (tool === 'get_module_source') row.text = name(str(field(p.args_excerpt, 'module_id'))) || unit;
        if (tool === 'run_verification') row.kind = 'wait';
        if (tool === 'propose_patch') {
          const accepted = field(result?.result_excerpt, 'accepted');
          const errors = list(field(result?.result_excerpt, 'errors'));
          row.text = str(field(p.args_excerpt, 'summary')) || unit;
          row.detail = [
            { label: 'Rationale', text: str(field(p.args_excerpt, 'rationale')) },
            {
              label: 'Result',
              text: !result ? 'Waiting for the result.' : accepted === false ? `Rejected. ${errors.join(' ')}` : 'Accepted and applied to the workspace.',
            },
          ].filter((d) => d.text);
          if (accepted === false) row.kind = 'fail';
          patch = row;
        }
        if (tool === 'finish_unit') {
          // Argon's reason can claim success; the verifier's answer sits next to it.
          const verified = field(result?.result_excerpt, 'verified');
          row.text = ids(str(field(p.args_excerpt, 'reason'))) || unit;
          row.detail = [{ label: 'Reason', text: row.text }];
          if (verified === false) row.detail.push({ label: 'Verifier', text: `Not verified: no verification of ${unit} passed.` });
        }
        if (result?.ok === false) {
          row.kind = 'fail';
          row.detail = [...(row.detail ?? []), { label: 'Error', text: str(result.result_excerpt) }];
        }
        break;
      }
      case 'verification_passed':
      case 'verification_failed': {
        // A verdict with no gates of its own must not borrow the previous unit's.
        const own = open ? gates : undefined;
        row.kind = e.event_type === 'verification_passed' ? 'ok' : 'fail';
        row.text = own?.title ?? (unit || 'Final verification');
        row.gates = own?.gates;
        open = false;
        const failed = list(p.failed_case_ids);
        if (failed.length) row.detail = [{ label: 'Cases that differ', text: failed.join(', ') }];
        break;
      }
      case 'snapshot_accepted':
        row.kind = 'ok';
        break;
      case 'repair_started':
        row.kind = 'repair';
        row.text = `${unit}, attempt ${p.attempt} of ${Number(p.attempt) + Number(p.budget_remaining)}`;
        break;
      case 'counterexample_recorded':
        row.text = [unit, str(p.case_id), p.visibility === 'held_out' ? 'held out' : ''].filter(Boolean).join(', ');
        break;
      case 'patch_rejected':
        row.kind = 'fail';
        row.text = [unit, list(p.errors).join(' ')].filter(Boolean).join(': ');
        break;
      case 'unit_blocked':
        row.kind = 'fail';
        row.text = `${unit}: ${ids(str(p.reason))}`;
        break;
      case 'report_generated':
        row.text = `${list(p.artefact_ids).length} artefacts`;
        break;
      case 'error':
        row.kind = 'fail';
        row.text = str(p.message) || words(p.code);
        break;
      default:
        // A type this screen doesn't know yet: its plain values, minus internal IDs.
        row.text = [unit, ...Object.values(p).filter((v) => typeof v === 'string' || typeof v === 'number').map(String)]
          .filter((v) => v && !/^(v1_|ver_|rev_|art_|sha256:)/.test(v))
          .join(', ');
    }
    rows.push(row);
  }
  return { rows, gates, attempts };
}

export type TerminalLine = { sequence: number; timestamp: string; type: string; text: string; fail: boolean };
const RESULT_CHARS = 120;

/**
 * Every recorded event as one line, the ones the log folds away included (#38). The text is read from
 * the event and nothing else, so there is no made-up progress. Turns where Argon only called tools have
 * no text and no line.
 */
export function terminalLines(log: RunEvent[], name: (id: string) => string): TerminalLine[] {
  const rows = new Map(eventRows(log, name).rows.map((r) => [r.sequence, r]));
  return log.flatMap((e) => {
    const p = e.payload;
    const row = rows.get(e.sequence);
    const unit = e.unit_id ? name(e.unit_id) : '';
    let text = row?.text ?? '';
    let fail = row?.kind === 'fail';
    switch (e.event_type) {
      case 'agent_turn':
        if (!row) return [];
        break;
      case 'agent_tool_call':
        text = [str(p.tool), text].filter(Boolean).join(' ');
        break;
      case 'agent_tool_result': {
        // get_module_source returns the whole module, which would fill the panel. Errors stay whole.
        const result = str(p.result_excerpt);
        fail = p.ok === false;
        const cut = !fail && result.length > RESULT_CHARS;
        text = [str(p.tool), fail ? 'error' : 'ok', cut ? `${result.slice(0, RESULT_CHARS)}…` : result].filter(Boolean).join(' ');
        break;
      }
      case 'patch_proposed':
        text = `${list(p.files).join(', ')}: ${str(p.summary)}`;
        break;
      case 'patch_applied':
        text = `${unit} as ${str(p.workspace_revision)}`;
        break;
      case 'verification_started':
        text = `${unit}, attempt ${e.attempt ?? 1}`;
        break;
      case 'gate_result': {
        const g = gateChip(p);
        fail = g.status === 'failed';
        text = `${unit || 'final'}, ${g.label} ${g.summary || g.value}`;
      }
    }
    return [{ sequence: e.sequence, timestamp: e.timestamp, type: e.event_type, text, fail }];
  });
}
