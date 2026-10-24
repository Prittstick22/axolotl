# Agent Brief — Dev 5: React Frontend, Demo & Videos

> **Dev 5 is Abhay (`PanotAbhay`).** Who is who: [TEAM_SPLIT](TEAM_SPLIT.md).

> **Changed 24 Oct 2026:** the demo script, the videos and the presentation are separate
> demo work, not MVP and unassigned for now. The demo is chess (Python 2.7 → 3.14), the
> only demo; `legacy_orders` below is only a test fixture. Chess is uploaded as a folder, so the folder upload screen is MVP
> ([#19](https://github.com/Prittstick22/axolotl/issues/19)). See [PROJECT_STATUS.md](../PROJECT_STATUS.md).

You are the implementation agent for **Dev 5** on Axolotl (Encode London 2026). You have three documents:
1. `Axolotl_Product_Requirements_and_Implementation_Handoff.md` — the Spec (§2 screens and UI principles, §9.3 frontend rules, §14 demo)
2. `SHARED_CONTRACTS.md` — API (§6), models (§4), events (§7)
3. This brief

---

## Your mission

Make the truth visible. The graph is the centrepiece: nodes change colour only because the backend says so. Every number, diff and verdict on screen comes from the API. Judges spend 50% of their score on the live demo — you own how it looks and flows.

## You own

- `frontend/**` (Vite + React 19 + TypeScript, `@xyflow/react`, `@monaco-editor/react`, TanStack Query, a dagre/elk layout)
- `frontend/src/types/contracts.ts` (mirror of SHARED_CONTRACTS §4, same snake_case names)
- `docs/demo-script.md`, `demo/expected_demo_flow.md`
- Recording the three videos (Gemini 3 min, MongoDB 3 min, Agents That Ship 2–4 min) Sunday morning
- Frontend tests (Vitest + React Testing Library) for state rendering

## You do NOT own

Any backend logic. If an endpoint is missing a field, ask its owner (see SHARED_CONTRACTS §6) — don't compute it client-side.

---

## Screens (Spec §2.2) and what each must do

1. **New Migration** — choose `legacy_orders` from `GET /demo-fixtures` or upload ZIP (`POST /repositories/import`). Show real validation errors (bad ZIP, too large, traversal rejected). Banner: uploads are static-analysis only; executable migration only for trusted fixtures.
2. **Repository Overview** — counts, warnings, `analysis_status`, system status chips from `GET /system/status` (Mongo, search indexes building/ready, runner, Gemini).
3. **Architecture Graph** — React Flow, auto-layout (dependencies below dependants), arrowheads, `A → B` = A imports B (put this in the legend). Edge styles: solid confirmed, dashed heuristic, dotted grey unresolved. External nodes styled differently, never selectable as targets. Pan/zoom, search/filter box, keyboard focusable nodes. Selecting a node highlights its reverse dependants.
4. **Module Inspector** (side panel) — functions/classes with line numbers, Monaco read-only source scrolled to symbol, imports, dependants, warnings, visible case count, unit status, risk + factors.
5. **Target & Plan** — `GET /migration-targets`; unsupported targets visible but disabled with reason. Create plan → show ordered units, Argon rationale, risks, coverage gaps, cycle groups. **Approve** button → `POST /plans/{id}/approve` → **Start migration** → `POST /plans/{id}/runs`.
6. **Run Dashboard** — graph with live status overlay (`GET /graph?run_id=`), progress as "N of M units verified", current unit, attempt count, timeline of events (`/events?after_sequence=`), tool calls rendered compactly (tool name + excerpt), gate results as pass/fail chips.
7. **Verification / Diff** — per unit: Monaco diff editor (old vs new) from `/runs/{id}/diff?unit_id=`, gate table, counterexamples table showing input, old output, new output (held-out shown as "held-out case — redacted" unless exposed, labelled "exposed during repair"), attempt history.
8. **Final Report** — verdict (never green "completed" unless the API says `completed`), per-unit outcomes, test counts, counterexamples found/resolved, coverage caveats and limitations displayed prominently, Argon narrative labelled as such, download buttons only for artefacts returned by the API.
9. **Search (P1, do it if Dev 3 is ready by Sat 14:00)** — query box with mode toggle keyword/vector/hybrid; results show path, lines, snippet, match method, keyword/vector rank contributions, and a "native Atlas hybrid" vs "app-side fusion" label from `native_hybrid`. Clicking a result selects and highlights the node on the graph.

## State & UI rules (non-negotiable, Spec §9.3)

- No `setInterval`-driven fake progress. Polling every 1.5 s via TanStack Query only while run state is non-terminal; events fetched with `after_sequence`, deduped by sequence.
- Poll failure → banner "Connection lost — last known status at HH:MM:SS", keep last data, never infer completion.
- Status readable without colour: icon + text label per state (queued ○, baselining ◔, migrating ✎, verifying ⧗, repairing ↻, passed ✓, blocked ⛔, failed ✕, skipped ↷). Contrast AA.
- Diff and verdict on one screen must reference the same `workspace_revision`.
- No hard-coded JSON in the shipped app. Until an endpoint lands you may render a local file named `*_stub.json` behind a dev flag; delete all stubs before the Sat 17:00 dry run.
- API base URL from `VITE_API_BASE` (default `http://localhost:8000/api/v1`). No keys in the frontend.

## Tasks with checks

| # | When | Task | Done when |
|---|---|---|---|
| 1 | Fri 17:00–18:30 | Scaffold, routing, `contracts.ts`, API client, status chips | **Gate 18:00** renders real `/system/status` |
| 2 | Fri 18:30–21:00 | Import page + graph + inspector against real Dev 3 endpoints | **Gate 21:00** real graph rendered, click-to-inspect |
| 3 | Fri 21:00–23:30 | Targets & plan, approve/start, run dashboard polling | Real plan and baseline events visible |
| 4 | Sat 08:00–11:00 | Timeline with tool calls, gate chips, node status animation, reverse-dependant highlight | Real failed/repair attempt visible live |
| 5 | Sat 11:00–14:00 | Diff viewer, counterexamples, report page | Two-unit run fully inspectable |
| 6 | Sat 14:00–17:00 | Search page + graph linkage; error states (Mongo down, runner disabled, 429) | Full E2E dry run in browser |
| 7 | Sat 17:00–22:00 | Polish only: layout stability, readable on projector (≥16px), dark/light ok; record a clearly labelled backup recording of a real run | Freeze 22:00 |
| 8 | Sun 07:30–10:45 | Record videos from real runs, write `docs/demo-script.md`, upload & verify links | Submitted by 10:45 |

## Demo script you build toward (Spec §14)

0:00 problem in one sentence + import fixture → 0:25 graph, click `pricing.py`, dependants light up → 0:55 choose Modern Python 3, plan, approve → 1:20 run starts, live tool calls and tests → 2:05 a real mismatch (if one occurs) and repair, nodes amber → green → 2:35 diff, Atlas-backed counterexample/history, report with caveats.

If the live run doesn't produce a regression, say so and show the verifier's held-out checks passing plus the T-11 wrong-patch test as a separate, clearly labelled demonstration. Never present a recording as live; label the backup recording as "Recorded earlier run at <time>".

## Definition of done

A teammate can click through the Spec §16 chain end to end, and every visual state can be traced to a persisted event or API response.
