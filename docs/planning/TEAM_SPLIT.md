# Axolotl — Five-Person Team Split

How to use: give **each developer's agent three files** — the original Spec, `SHARED_CONTRACTS.md`, and that developer's `AGENT_DEVn_*.md`.

## Who is who

| Role | Name | GitHub |
|---|---|---|
| Dev 1 (also project manager) | Dev | `Prittstick22` |
| Dev 2 | Mo | `mh-husssain` |
| Dev 3 | Caroline | `Caroline3110` |
| Dev 4 | William (Will) | `WilliamParis1` |
| Dev 5 | Abhay | `PanotAbhay` |

`Dev N` in any doc, ticket title or `dev N` label means the person above. The repository
is public (24 Oct), by decision.

| Dev | Owns | Agent brief | Hand-off they produce |
|---|---|---|---|
| **Dev 1 — Argon Agent** | Gemini adapter, tool schemas, prompts, plan + migrate/repair loop | `AGENT_DEV1_ARGON_AGENT.md` | `docs/argon-agent.md`, DeepMind write-up |
| **Dev 2 — Orchestrator & Integration Lead** | Repo skeleton, contracts, SCC scheduler, state machine, events, plans/runs API, reports, README | `AGENT_DEV2_ORCHESTRATOR_INTEGRATION.md` | README, architecture docs, submission |
| **Dev 3 — Graph & Atlas** | Safe import, AST, dependency graph, all MongoDB persistence, Atlas Search / Vector / Hybrid | `AGENT_DEV3_INGESTION_GRAPH_ATLAS.md` | `docs/mongodb-atlas.md`, MongoDB write-up |
| **Dev 4 — Verifier** | Legacy demo fixture, held-out cases, Docker sandbox, workspaces/patches, differential verifier, demo script, videos, presentation | `AGENT_DEV4_VERIFIER_SANDBOX_FIXTURE.md` | `docs/safety-and-limitations.md`, demo script, 3 videos, presentation |
| **Dev 5 — Frontend** | React app, graph UI, run dashboard, diff/report, search UI | `AGENT_DEV5_FRONTEND.md` | The UI the demo runs on |

> **Changed 24 Oct 2026:** the demo script, the three videos and the presentation are
> separate demo work, not MVP and unassigned for now. Chess (Python 2.7 → 3.14) is the
> only demo, with no backup. See [PROJECT_STATUS.md](../PROJECT_STATUS.md).

## How the pieces connect

```
Dev 5 UI ──REST──> Dev 3 repo/graph/search API ──> Atlas (repositories, symbols, edges, code_chunks)
       └──REST──> Dev 2 plans/runs API
                     │ scheduler (Dev 3 graph) ─> Dev 1 propose_plan
                     │ per unit: Dev 1 run_unit_session ⇄ Dev 2 UnitController
                     │                                  └─> Dev 4 apply_patch / verify_unit (Docker)
                     └─> Atlas (plans, runs, run_events)      Dev 4 ─> Atlas (cases, verifications, counterexamples)
```

The pass/fail authority chain: **Dev 4 verifier → Dev 2 records it → Dev 5 displays it.** Dev 1's model never sets status.

## Team gates (all on `main`)

| Time | Gate |
|---|---|
| Fri 18:00 | Skeleton boots, real Mongo status in UI |
| Fri 21:00 | Fixture → real graph in Atlas → UI; one real sandbox baseline case |
| Sat 11:00 | Real Argon patch → real verification → repair attempt |
| Sat 14:00 | Two units verified in order; search live |
| Sat 17:00 | Cold-start E2E, all stubs deleted |
| Sat 22:00 | Feature freeze |
| Sun 10:45 | Submitted (cutoff 12:00) |

## Things to settle at kickoff (Dev 2 records answers)

- The Spec planned 3 people on Axolotl and 2 on AccessProof; this split puts all 5 on Axolotl, so AccessProof would not be built — confirm that's intended.
- Can one Axolotl entry go to DeepMind, MongoDB and Agents That Ship?
- Gemini 4 Argon quota, exact Interactions API tool-call shape, available embedding model.
- Atlas cluster version (native `$rankFusion` needs 8.0+), Search/Vector index permissions.
- A real engineer who can validate the problem (the "Alex" persona is illustrative).
