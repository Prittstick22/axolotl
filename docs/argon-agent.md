# Axolotl Gemini agent

Owner: Dev 1. The agent proposes changes; only the independent verifier decides
whether the tested behaviour agrees. The agent never writes MongoDB, executes a
shell command or reads held-out case files.

## Implemented interfaces

`backend/app/agents/__init__.py` exports the async functions consumed automatically
by Dev 2's orchestration ports:

- `propose_plan(ctx)`: asks Gemini for a structured `submit_plan` tool call. Validates
  unit membership, uniqueness and dependency order, retries malformed plans once,
  and acknowledges the tool result before returning the reusable interaction ID.
  Dev 2 also validates and persists the plan.
- `run_unit_session(ctx, controller)`: runs local tools, submits full-file patches,
  requests independent verification and continues with counterexamples after failure.
  Returns `verified`, `budget_exhausted`, `model_gave_up` or `error`. Only
  `controller.is_verified()` can produce a verified outcome.
- `narrate_report(evidence)`: produces model-generated prose from recorded evidence,
  with instructions to cite only supplied numbers and acknowledge untested areas.
  Dev 2 must keep the narrative labelled as model-generated; structured gate results
  remain authoritative.

## API and configuration

`gemini_client.py` isolates the `google-genai` SDK. It uses
`client.aio.interactions.create`, chains `previous_interaction_id`, parses
`function_call` steps and sends matching `function_result` steps. Local tools run in
our client-side loop. Each request repeats the system instructions, tool declarations
and generation configuration because these settings are interaction-scoped.

- Model: `GEMINI_MODEL`, with no silent model fallback.
- Thinking: `GEMINI_THINKING_LEVEL` initially; `high` during repair diagnosis.
- No `temperature`, `top_p`, `top_k`, `thinking_budget` or `cached_content` is sent.
- Each request has a 240-second timeout. Rate limits receive at most four adapter
  attempts with 1, 2 and 4 second backoffs. Other failures surface immediately.
- Missing credentials or failed/incomplete interactions raise `ModelUnavailable`.
  Unit sessions translate failures into an error outcome, never a successful verdict.
- All successful turn IDs are returned for orchestration to persist. Clients close
  at the end of each plan, unit session or narration request.

The request and response shapes were checked against the installed SDK and Google's
[Interactions guide](https://ai.google.dev/gemini-api/docs/interactions-overview) and
[API reference](https://ai.google.dev/api/interactions-api) on 23 October 2026.
A live `gemini-4-argon` test requested a declared tool, accepted its result and
continued via the preceding interaction ID. This verifies API integration, not a
migration verdict or future quota availability.

## Tools and boundaries

| Tool | Source / action |
|---|---|
| `get_repository_overview` | Dev 3 repository metadata, including graph-related counts |
| `get_dependency_graph` | Compact node and dependency edge list |
| `get_module_source` | Original source from Dev 3's module detail service |
| `get_symbol` | Symbol id or qualified-name lookup in the session's repository |
| `search_repository` | Up to eight results from Dev 3; preserves the actual search mode and hybrid metadata |
| `get_behaviour_cases` | Visible cases only, with `include_held_out=False` |
| `propose_patch` | Validated full-file replacements via `controller.submit_patch` |
| `run_verification` | Independent checks through the controller |
| `get_counterexamples` | Controller's model-safe failure evidence |
| `finish_unit` | Stops the session without changing verification status |

Repository and unit identity are bound by code, never accepted from tool arguments.
Arguments reject undeclared fields. Patches reject paths outside the unit, traversal,
Windows paths, hidden-case paths, test files, duplicates and files over 64 KiB.
The controller and sandbox enforce their own boundaries independently.

Repository data is delimited and angle brackets escaped. Tool results are also
wrapped as untrusted data. Embedded instructions have no authority over permissions.
Events use only the three allowed agent event types, with redacted excerpts capped
at 2 KiB. Unknown tools and malformed arguments receive structured errors; malformed
arguments get one retry before the session stops.

Tool calls are counted even within a batch. Both the session's patch limit and the
controller's remaining budget are enforced. The final accepted patch still gets a
verification opportunity after the last patch attempt. An accepted patch must be
verified before another patch or finish; a text-only completion gets one verification
nudge. Empty model turns cannot create an infinite loop.

## Current integration limits

- Search is integrated with Dev 3's response envelope (`mode_used`, `native_hybrid`,
  `hits`). Protected paths are filtered and results are capped at eight. Dependency
  outages return a structured error rather than fabricated search results.
- The visible-case interface does not provide the total held-out count. It is
  reported as unknown (`null`); the agent never loads hidden cases to compute it.
- Chess (Python 2.7 → 3.14) is the only demo since 24 Oct, with no backup, but the chess
  fixture is not integrated yet ([#30](https://github.com/Prittstick22/axolotl/issues/30)). Prompts cover
  Python 2 compatibility risks, but the orders test fixture is the only project the
  sandbox can run so far. A Python 2 interpreter and repeatable terminal-session cases are
  needed before any claim about chess migration can be made.
- A full model-patch-to-Docker-verdict run must be performed where the runner is
  available. Unit tests use scripted collaborators and are not migration evidence.

## Validation

From `backend/`:

```powershell
uv run pytest tests/unit/agents tests/security/test_prompt_injection.py -m "not live" -q
uv run pytest tests/unit/agents/test_live_agent.py -m live -s
uv run pytest -m "not live" -q
uv run ruff check .
```

The live checks make real Gemini calls. The planning test uses explicitly synthetic
input and does not write a migration plan or change any repository. Offline tests
cover repair, budget exhaustion, model errors, plan validation, hidden-case filtering,
path restrictions, prompt injection and report handling.

## Next demonstration

On a machine with Atlas and the runner available, import the trusted fixture, create
and approve a plan, then start a run through the existing API. Confirm that a genuine
model patch reaches `submit_patch` and the verifier, and that only persisted passing
gates advance the unit. Then exercise two dependency-ordered units. A repair is shown
only if a real verification failure occurs; never seed a model mistake for a demo.

## Suggested DeepMind submission wording

Axolotl uses Gemini as its migration reasoning engine through the Interactions API.
The model proposes dependency-aware plans and full-file changes, calls local tools,
and can investigate independent verifier failures within fixed budgets. Recorded
interaction IDs and tool events connect model activity to the migration evidence.
Report the exact model and outcomes of the final live run; API handshake tests alone
do not establish behaviour preservation.
