# Agent Brief — Dev 4: Demo Fixture, Sandbox Runner & Differential Verifier

> **Changed 24 Oct 2026:** the demo is chess (Python 2.7 → 3.14), the only demo, so the chess
> cases are MVP ([#23](https://github.com/Prittstick22/axolotl/issues/23)). `legacy_orders` below is no longer a demo: it is only the test
> fixture until chess replaces it ([#54](https://github.com/Prittstick22/axolotl/issues/54)). See [PROJECT_STATUS.md](../PROJECT_STATUS.md).

You are the implementation agent for **Dev 4** on Axolotl (Encode London 2026). You have three documents:
1. `Axolotl_Product_Requirements_and_Implementation_Handoff.md` — the Spec
2. `SHARED_CONTRACTS.md` — names, shapes, ownership
3. This brief

---

## Your mission

You are the independent judge. You build the legacy application Axolotl migrates, the protected test oracle, the restricted sandbox that executes old and new code, and the differential verifier whose verdict is the only source of pass/fail. Nobody — not Argon, not the orchestrator — can make something pass except real matching outputs from your runner.

## You own

- `demo/legacy_orders/**`, `demo/challenge_cases/**`
- `runner/**` (Dockerfile, `harness/run_cases.py`, `policy.md`)
- `backend/app/sandbox/**` (runner client, workspaces, patch application)
- `backend/app/verification/**` (cases, baseline, comparison, gates, counterexamples)
- Collections `behaviour_cases`, `verification_results`, `counterexamples`
- Tests T-09, T-10, T-11, sandbox security tests; `docs/safety-and-limitations.md`

## You do NOT own

Deciding *what* runs next (Dev 2), model calls (Dev 1), graph/AST (Dev 3), UI (Dev 5).

---

## 1. The legacy fixture — `demo/legacy_orders/` (build Fri 17:00–19:00, first job)

A small, **runnable**, deterministic legacy-style Python 3 order service, 5–6 local modules, matching Spec §3.3:

```
main.py        # thin CLI: place order / refund from JSON
orders.py      # place_order(customer_id, items) -> dict ; imports pricing, customers, storage
refunds.py     # partial_refund(order_id, lines) -> dict ; imports pricing, storage
pricing.py     # line totals, discounts, tax, final amount — the subtle rule lives here
customers.py   # customer category / eligibility lookup
storage.py     # in-memory dict store with reset() for deterministic tests
tests/test_baseline.py   # existing pytest suite (public behaviour only)
tests/cases.json         # visible behaviour cases
```

"Legacy style": no type hints, dict-everywhere, `%`-formatting, long functions, magic strings, comments that look stale — but **no actual bugs that a modernisation would legitimately fix** (it must be preserve-able).

**The seeded rule (genuine, not scripted):** money with `Decimal`; discount applied *per line* before tax, each line quantised to 0.01 with `ROUND_HALF_UP`, *then* tax on the rounded subtotal quantised again. A natural "clean" rewrite (sum then discount, or `round()`/banker's rounding, or quantise once at the end) produces £0.01 differences on specific inputs. Name the function neutrally (e.g. `finalise_amount`) so the semantic search demo is meaningful (tell Dev 3). Also a refund rule that reuses pricing (so `refunds` is an impacted dependant).

Make visible cases cover ordinary inputs where naive and correct rounding agree, and put most `.xx5` edge inputs in **held-out** cases. That way a regression is *plausible* without anyone instructing the model to make one. If Argon gets it right first time, that is the honest outcome — report it. Separately, T-11 proves the verifier catches a deliberately wrong patch (a test, not shown as an Argon repair).

Hand Dev 3 the exact expected import edge list for T-03. Document fixture provenance (authored by the team) in `demo/legacy_orders/README.md`.

## 2. Behaviour cases
- `tests/cases.json` (visible, `case_###`) and `demo/challenge_cases/held_out.json` (`hcase_###`), format = `BehaviourCase` fields, values encoded per SHARED_CONTRACTS §5. Include case-level `description`.
- `load_cases(..., include_held_out)`: map `function` → module → `unit_id`. On import of the fixture, persist cases with `case_hash`. Held-out cases are stored but **every** read path for the model goes through `include_held_out=False` or redaction.
- `challenge_cases/` lives outside the fixture tree so it's never in the snapshot, the workspace, search chunks or model context.
- Stateful functions: harness calls `storage.reset()` before each case, then a case may be a short sequence (`steps: [...]`) whose final state is captured as part of the output. Keep it simple.

## 3. Runner — `runner/` + `app/sandbox/runner.py`
`RUNNER_MODE=trusted_docker`; if Docker unavailable → `disabled`, `runner_status().available=False`, and verification returns gates `inconclusive` — never pass. **Never** fall back to host `subprocess` of repository code.

Per execution:
```
docker run --rm --network none --user 65534:65534 --read-only \
  --memory 256m --cpus 1 --pids-limit 64 --security-opt no-new-privileges --cap-drop ALL \
  --tmpfs /tmp:size=16m \
  -v <code_tree>:/code:ro  -v <harness_dir>:/harness:ro  -v <cases_file>:/cases.json:ro \
  axolotl-runner python /harness/run_cases.py --code /code --cases /cases.json
```
- No env vars passed (no keys). No Docker socket mount. Wall-clock timeout (e.g. 30 s) enforced by the backend with `docker kill`. Stdout capped (1 MB); logs truncated to 4 KB in results.
- `run_cases.py`: inserts `/code` on `sys.path`, imports module by dotted name, decodes args, calls, canonicalises output (§5), catches exceptions → `raised`, per-case timeout, prints one JSON line per case. It must not import anything from the backend.
- Compile gate: `python -m compileall -q /code` in the same container profile (with `PYTHONDONTWRITEBYTECODE` / write to tmpfs).
- Existing-tests gate: `pytest -q -p no:cacheprovider` on `/code/tests` with the same limits; pytest preinstalled in the image (no runtime installs).
- Building the runner image and the backend calling `docker` are done from the host; the backend itself does not run inside a container that mounts the socket.

## 4. Workspaces & patches — `app/sandbox/workspaces.py`
- `create_workspace(run_id, repo_id, base_revision)`: fresh copy of baseline (`snapshot_path`) or of the last accepted revision into `WORKSPACE_ROOT/runs/<run_id>/<rev>/`.
- `apply_patch`: reject if any path not in `allowed_paths`, outside root after `resolve()`, under `tests/`, > 64 KB, not valid UTF-8, or `ast.parse` fails (return the syntax error text in `PatchAck.errors` for the model). Writes only inside the workspace.
- `accept_workspace` freezes the revision (read-only) and returns its id. `diff` uses `difflib.unified_diff`.
- T-09: baseline snapshot hash identical before/after a full run.

## 5. Verifier — `app/verification/`
- `capture_baseline`: run all cases (visible + held-out) against the immutable baseline once per run; store outcomes keyed by `(run_id, case_id)`; record env (python version, image digest). Baseline cases that error are flagged `cases_failed_to_capture`, never silently dropped.
- `verify_unit(unit, ws, impacted, attempt)` gates (Spec §7.3):
  1. `compile` on candidate tree
  2. `differential`: all cases of `unit` (visible + held-out) on candidate vs stored baseline
  3. `impacted_tests`: cases of every impacted unit (reverse dependants) on candidate tree
  4. `existing_tests`: fixture pytest suite on candidate tree
  5. `sandbox_policy`: no timeouts/kills/oversized output
  Any timeout → `inconclusive`, which counts as **not passed**.
- `verify_final`: all cases + full pytest on the final accepted tree → `final_integration` gate.
- Mismatch → create `Counterexample` (`visibility`, `exposed_to_model`). `get_counterexamples(for_model=True)` redacts held-out args/outputs unless exposed; `expose_case` flips it and logs.
- Persist every `VerificationResult`; `all_required_gates_passed` computed here and nowhere else.

## Tasks with checks

| # | When | Task | Done when |
|---|---|---|---|
| 1 | Fri 17:00–19:00 | Fixture + visible/held-out cases + pytest suite; edge list to Dev 3 | `pytest` passes on fixture locally; cases JSON valid |
| 2 | Fri 19:00–21:00 | Runner image + `run_cases.py` + baseline capture of one case through Docker | **Gate 21:00**: one real baseline case executed in sandbox, stored in Atlas |
| 3 | Fri 21:00–23:30 | Workspaces, `apply_patch`, differential compare, counterexamples | Hand-edited wrong `pricing.py` in a workspace fails differential with a real £0.01 counterexample (T-11) |
| 4 | Sat 08:00–11:00 | Full `verify_unit` gates + controller integration with Dev 2 | **Gate 11:00** with real Argon patch |
| 5 | Sat 11:00–14:00 | Impacted-unit tests, held-out redaction/exposure, `verify_final` | T-10, T-13 support, T-14 |
| 6 | Sat 14:00–17:00 | Security tests: patch path traversal, writes to `tests/`, network attempt in candidate code fails, infinite loop → inconclusive | All pass |
| 7 | Sat 17:00–22:00 | Fixes only | |
| 8 | Sun 07:30–10:45 | `docs/safety-and-limitations.md` (exact sandbox protections and their limits; trusted-fixture-only execution; finite-test caveat) | Handed to Dev 2 |

## Definition of done

A wrong candidate cannot pass; a correct one can; every verdict is reproducible from stored inputs, hashes and the runner image digest; the baseline never changes.
