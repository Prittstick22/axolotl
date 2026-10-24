# legacy_orders — test fixture

> **Not a demo (24 Oct 2026).** Chess (Python 2.7 → 3.14) is the only demo, with no backup.
> This fixture stays only because the backend tests and the sandbox run on it. It will be
> deleted once chess runs end to end and the tests move to chess ([#54](https://github.com/Prittstick22/axolotl/issues/54)). See
> [PROJECT_STATUS.md](../../docs/PROJECT_STATUS.md).

**Provenance: written by the Axolotl team for this project.** It is not derived from
any real or third-party codebase, contains no customer data, and the customer names in
`customers.py` are invented.

Owner: **Dev 4**. Built by Dev 2 ahead of Dev 4 picking it up; the decisions marked
**[confirm]** below were not specified in the planning docs and need Dev 4's sign-off.

## What it is

A small, runnable, deterministic legacy-style **Python 3** order service. Six top-level
modules, no package, no external dependencies.

```
main.py        thin CLI: place / refund from a JSON argument
orders.py      place_order(customer_id, items) -> dict
refunds.py     partial_refund(order_id, lines) -> dict
pricing.py     discounts, tax, rounding — the subtle rule lives here
customers.py   category / eligibility lookup
storage.py     in-memory store with reset() for deterministic tests
tests/test_baseline.py   12 tests, public behaviour only
tests/cases.json         16 visible behaviour cases
```

Expected module edges (hand this to Dev 3 for **T-03** — exactly these, no more):

```
module_main    -> module_orders
module_main    -> module_refunds
module_orders  -> module_pricing
module_orders  -> module_customers
module_orders  -> module_storage
module_refunds -> module_pricing
module_refunds -> module_storage
```

No cycle — the cyclic fixture for T-05 is a separate artefact (Dev 3).
`pricing`, `customers` and `storage` are leaves.

## The seeded rule

`pricing.finalise_amount(lines, category)` runs these stages **in this order**:

1. gross amount per line
2. discount taken off **each line**, before any tax
3. **each line** rounded to the penny, `ROUND_HALF_UP`
4. tax applied to the **sum of the rounded lines**
5. the taxed total rounded to the penny **again**

The function is named neutrally on purpose, so Dev 3's semantic search demo (**T-07**,
"where does the final customer bill get rounded?") has to actually work rather than
keyword-match on `round_bill`.

A tidier rewrite diverges by £0.01. Measured, not assumed — across 300 single-line
inputs (15 prices × 5 quantities × 4 categories), **77 diverge** under at least one of:

| Naive rewrite | Example it gets wrong |
|---|---|
| sum then discount once | partner, 3×2.99 + 1×9.99 → 19.34 vs **19.33** |
| banker's rounding (`ROUND_HALF_EVEN`) | member, 1×2.45 → 2.64 vs **2.65** |
| quantise only at the end | partner, 1×1.25 → 1.28 vs **1.27** |

**The fixture's own 12 tests pass on all three rewrites.** Only the held-out
differential catches them. That is the point of the demo, and it is why the visible
tests must not be strengthened to pin the rounding stages.

### Parameters **[confirm]** — not specified in any planning doc

| Thing | Chosen | Note |
|---|---|---|
| Tax rate | 20% (UK VAT) | `pricing.TAX_RATE` |
| Discount bands | standard 0%, member 10%, partner 15%, staff 25% | `pricing.CATEGORY_DISCOUNTS` |
| Rounding function name | `finalise_amount` | docs suggested it; contracts §4 uses `pricing.final_total` as an unrelated example |
| Refund rule | reprice the returned quantities through `finalise_amount` at the original unit price and category | docs say only "a refund rule that reuses pricing" |
| Unknown / closed account | prices as `standard`, cannot order | docs say "do not fix this" |
| Rejections | returned as `{"status": "rejected", "reason": ...}` dicts, not exceptions | keeps the public surface dict-shaped |
| Case counts | 16 visible, 10 held-out | docs give no numbers |

## Behaviour cases

Cases carry **inputs only**. The expected values come from running this baseline —
the verifier is the oracle, never a checked-in expectation.

- Visible: `tests/cases.json`, ids `case_###`. Ordinary inputs where every rounding
  convention agrees, plus rejection and exception paths.
- Held-out: `../challenge_cases/held_out.json`, ids `hcase_###`. The `.xx5` edges.
  **Outside this tree on purpose** so it never reaches the snapshot, the workspace,
  the search index or the model.

`repository_id` and `case_hash` are filled in at import time, so they are absent from
the files on disk.

**[confirm] Contract gap:** cases 014, 016, hcase_009 and hcase_010 use a `steps` array
for stateful sequences (place an order, then refund it). `BehaviourCase` in
SHARED_CONTRACTS §4 has no `steps` field. The harness supports it; the contract needs an
additive field under §11, or these cases need another shape. Schema used:

```json
"steps": [{"function": "orders.place_order", "args": [...], "kwargs": {}}, ...]
```

The last step's return value is the captured output. `storage.reset()` runs once before
the sequence.

## Legacy markers

No type hints, dicts as the universal data structure, `%`-formatting, long functions,
magic strings, stale comments. **No Python 2-isms** — the docs specify legacy-style
*Python 3* and explicitly exclude Python 2 support, so `%`-formatting is the only
deprecated idiom used.

`customers.category_for` carries a prompt-injection string in its docstring as
untrusted-input test data for **T-18**. It is inert data. Nothing in this repository
grants instructions found in source text.

## Running it

```bash
cd demo/legacy_orders && PYTHONPATH=. python -m pytest tests -q
```

```bash
python main.py place '{"customer_id": "C-1001", "items": [{"sku": "A", "unit_price": "2.45", "quantity": 1}]}'
```
