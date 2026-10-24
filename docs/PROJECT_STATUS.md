# Project status: 24 Oct 2026

A review of `main` at `a27ac92`, made early on Saturday 24 Oct, updated the same day with the
project manager's decisions and feature requests. It says what is finished and working, what is broken and what is
left, and lists the product's features. Every open item is a GitHub issue. The `dev 1` to
`dev 5` labels and the `[Dev N]` in each title name the owning role; who is who is in the table
below and in [TEAM_SPLIT](planning/TEAM_SPLIT.md).

| Label | Meaning |
|---|---|
| `MVP` | Top priority. Needed for the chess demo or the submission. |
| `demo` | Preparing and recording the demo. Not MVP; unassigned for now. |
| `feature` | After the MVP. A new capability or an improvement. |
| `bug` with no `MVP` | A defect that doesn't block the demo. Fix it after the MVP tickets. |
| `stretch` | Only if time allows. |
| `spike` | Investigate, then decide. The output is a decision, not code. |

MVP tickets are assigned on GitHub (24 Oct). Feature tickets aren't assigned yet, except where
someone picked one up themselves. Tickets with no `dev N` label have no obvious owner: anyone can
pick them up.

| Role | Name | GitHub |
|---|---|---|
| Dev 1 (also project manager) | Dev | `Prittstick22` |
| Dev 2 | Mo | `mh-husssain` |
| Dev 3 | Caroline | `Caroline3110` |
| Dev 4 | William (Will) | `WilliamParis1` |
| Dev 5 | Abhay | `PanotAbhay` |

The repository is public (24 Oct), by decision. Never commit `.env` or any key.

Time left: feature freeze **Sat 22:00**, submission **Sun 10:45** (cutoff 12:00).

## Decisions, 24 Oct

- **Chess is the only demo.** Will's terminal chess game, migrated from Python 2.7 to Python
  3.14. It can't be migrated yet; that is tracked as a bug,
  [#30](https://github.com/Prittstick22/axolotl/issues/30), with one sub-ticket per piece. The
  Spec listed Python 2 → 3 as a hackathon non-goal; this decision overrides it, and the sandbox
  and safety rules still apply.
- **There is no backup demo.** `legacy_orders` stays in the repo only as the test fixture the
  backend tests and the sandbox run on today. It is deleted once chess runs end to end
  ([#54](https://github.com/Prittstick22/axolotl/issues/54)).
- **Demo work is not MVP.** The demo script, the presentation and the videos
  ([#14](https://github.com/Prittstick22/axolotl/issues/14), [#70](https://github.com/Prittstick22/axolotl/issues/70), [#15](https://github.com/Prittstick22/axolotl/issues/15)) are labelled `demo`. The presentation
  ([#70](https://github.com/Prittstick22/axolotl/issues/70), 1 to 1:30 minutes) is Will's (Dev 4) and starts now; the script and videos come last.

## Summary

- **Working:** the backend is essentially complete. CI on `main` is green: 562 offline backend
  tests, 9 tests in a real sandbox container, and the frontend build and lint. Screens 01–06 and 08 are
  built. Argon's patches have passed the independent verifier on a real `legacy_orders` run.
- **Broken:** chess can't be planned or run
  ([#30](https://github.com/Prittstick22/axolotl/issues/30)). A failed Argon call (a 429 or a
  dropped connection) silently blocks a unit, which cut two live runs short
  ([#47](https://github.com/Prittstick22/axolotl/issues/47)). Imports take minutes because they wait
  for search indexing ([#28](https://github.com/Prittstick22/axolotl/issues/28)).
- **Fixed on 24 Oct, Dev 4 to review:** units with no cases can now pass
  ([#2](https://github.com/Prittstick22/axolotl/issues/2)), and the diff reads the real baseline
  ([#44](https://github.com/Prittstick22/axolotl/issues/44)), in `45fe5a5`.
- **Left for the MVP:** 19 open tickets (#6, #19 and #45 are closed). The chess pieces, a live
  run with a real repair, the Verification/Diff screen, a cold-start dry run and the write-ups.

## Finished and working

| Area | State | Evidence |
|---|---|---|
| App boots; `/health` and `/system/status` with real checks | ✅ | CI; the status chips |
| Contracts, config, error envelope | ✅ | CI |
| Import (fixture, ZIP, folder API), safety checks, AST, dependency graph | ✅ | CI; folder upload API in PR #1 |
| Atlas persistence and indexes, created from code | ✅ | Live suite against the cluster |
| Keyword, vector and native `$rankFusion` hybrid search | ✅ | Live suite; `docs/mongodb-bounty.md` |
| Scheduler, state machine, event journal, plans, runs, reports | ✅ | CI |
| Docker sandbox, workspaces, the five verification gates | ✅ | 9 container tests in CI |
| Gemini agent: planning, migration and repair loop, prompt-injection tests | ✅ | Offline tests; 2 live Gemini checks |
| Argon patch → independent verifier | ✅ partly | `legacy_orders` runs: `run_bb0da2e79534` verified 2 of 6 units before #2; after the fix, `run_e5380c4d180c` reached 4 of 6 and lost a unit to #47 |
| Chess: upload, graph, source and declared Python 2.7 | ✅ | Folder upload of `demo/chess` on 24 Oct (15 modules, 122 chunks) |
| Screens 01–06: New migration, Overview, Graph, Module inspector, Plan, Run | ✅ | Frontend build; vitest |
| Folder upload screen on 01 | ✅ | `64ba380`; [#19](https://github.com/Prittstick22/axolotl/issues/19) closed |
| Run timeline: Argon's tool calls, gate chips, live attempts | ✅ | `a27ac92` |
| Runtime endpoint 500 on other machines | ✅ fixed | `edd19ea`, `237ed92` |

**Not checked in this review:** a live end-to-end run. The review machine has no `.env` and
Docker wasn't running, so nothing here re-proves the live stack. That is what
[#4](https://github.com/Prittstick22/axolotl/issues/4) and
[#7](https://github.com/Prittstick22/axolotl/issues/7) are for.

## Broken

| Issue | Owner | Blocks the demo? |
|---|---|---|
| [#30](https://github.com/Prittstick22/axolotl/issues/30) Chess can't be migrated from Python 2.7 to 3.14 yet | Dev 2, 3, 4 | **Yes**: the chess demo |
| [#47](https://github.com/Prittstick22/axolotl/issues/47) A failed Argon call blocks a unit, and the reason says verification failed | Dev 1, Dev 2 | **Yes**: cut two live runs short |
| [#2](https://github.com/Prittstick22/axolotl/issues/2) Units with no visible cases can never pass verification | Dev 4 | Fixed in `45fe5a5`; Dev 4 to review and close |
| [#29](https://github.com/Prittstick22/axolotl/issues/29) Every import gets the `legacy_orders` held-out cases | Dev 1 | **Yes**: chess would be checked against the wrong cases |
| [#28](https://github.com/Prittstick22/axolotl/issues/28) Import waits minutes for search indexing | Dev 3 | Stalls the demo unless chess is imported beforehand |
| [#44](https://github.com/Prittstick22/axolotl/issues/44) The diff has no baseline, so every file reads as new | Dev 4 | Fixed in `45fe5a5`; Dev 4 to review and close |
| [#45](https://github.com/Prittstick22/axolotl/issues/45) The unit endpoint returns no verifications | Dev 2 | Fixed in `e4fad35`, closed |
| [#3](https://github.com/Prittstick22/axolotl/issues/3) The plan shows the wrong risk for every unit | Dev 2 | Wrong numbers on the Plan screen |
| [#16](https://github.com/Prittstick22/axolotl/issues/16) A run turns terminal before its last event | Dev 2 | No, the frontend works around it |
| [#17](https://github.com/Prittstick22/axolotl/issues/17) Interrupted runs keep their running unit states | Dev 2 | Only if the backend restarts mid-run |
| [#18](https://github.com/Prittstick22/axolotl/issues/18) One plan can start several active runs | Dev 2 | No, but a double click spends quota twice |
| [#32](https://github.com/Prittstick22/axolotl/issues/32) A unit's attempts reads 0 until the unit ends | Dev 2 | No, the frontend works around it |

Docs that are out of date are covered by [#8](https://github.com/Prittstick22/axolotl/issues/8).

## Left for the MVP

### Chess path

Chess needs every piece below before a live run is possible. Close them, then close
[#30](https://github.com/Prittstick22/axolotl/issues/30).

| Issue | Owner | Depends on |
|---|---|---|
| [#28](https://github.com/Prittstick22/axolotl/issues/28) Import waits minutes for search indexing | Dev 3 | — |
| [#29](https://github.com/Prittstick22/axolotl/issues/29) Every import gets the `legacy_orders` held-out cases | Dev 1 | — |
| [#22](https://github.com/Prittstick22/axolotl/issues/22) Chess: dual-runtime sandbox (2.7 baseline, 3.14 candidate) | Dev 1 | — |
| [#23](https://github.com/Prittstick22/axolotl/issues/23) Chess: behaviour cases and per-fixture case routing | Dev 4 | #29 |
| [#21](https://github.com/Prittstick22/axolotl/issues/21) Chess: add a working `python2_to_3` target | Dev 2 | #22, #23 |

### Both demos

| Issue | Owner | Depends on |
|---|---|---|
| [#47](https://github.com/Prittstick22/axolotl/issues/47) A failed Argon call blocks a unit (Dev 2 half done in `ba9d765`; Dev 1's retry left) | Dev 1, Dev 2 | — |
| [#2](https://github.com/Prittstick22/axolotl/issues/2) Units with no visible cases can pass (fixed; review and close) | Dev 4 | — |
| [#44](https://github.com/Prittstick22/axolotl/issues/44) The diff reads the real baseline (fixed; review and close) | Dev 4 | — |
| [#3](https://github.com/Prittstick22/axolotl/issues/3) The plan shows the wrong risk for every unit | Dev 2 | — |
| [#4](https://github.com/Prittstick22/axolotl/issues/4) Run chess end to end on the live stack and capture a real repair | Dev 1 | #30, #47 |
| [#5](https://github.com/Prittstick22/axolotl/issues/5) Build screen 07: Verification / Diff | Dev 5 | #44 |
| [#7](https://github.com/Prittstick22/axolotl/issues/7) Cold-start end-to-end dry run from the README | Dev 2 | #4, #5, #6 |
| [#11](https://github.com/Prittstick22/axolotl/issues/11) Protect the Gemini embedding quota and prime the replay cache | Dev 3 | — |

### Submission

| Issue | Owner | Depends on |
|---|---|---|
| [#9](https://github.com/Prittstick22/axolotl/issues/9) Write `docs/safety-and-limitations.md` | Dev 4 | — |
| [#8](https://github.com/Prittstick22/axolotl/issues/8) Bring the README and docs up to date for submission | Dev 2 | #9 |
| [#12](https://github.com/Prittstick22/axolotl/issues/12) Finish the MongoDB bounty write-up and rehearse the Atlas demo | Dev 3 | — |
| [#13](https://github.com/Prittstick22/axolotl/issues/13) DeepMind submission write-up from the final live run | Dev 1 | #4 |

## Demo, not MVP

Unassigned for now. Do these last, once the chess run works end to end, so the script
and videos show the real product.

| Issue | Owner | Depends on |
|---|---|---|
| [#14](https://github.com/Prittstick22/axolotl/issues/14) Write the demo script and rehearse the 3-minute demo | Unassigned | #4 |
| [#70](https://github.com/Prittstick22/axolotl/issues/70) Make the 1–1:30 minute presentation (replaces #31) | Dev 4 · `WilliamParis1` | [#70](https://github.com/Prittstick22/axolotl/issues/70) (`demo`), — |
| [#15](https://github.com/Prittstick22/axolotl/issues/15) Record and upload the three submission videos | Unassigned | #4, #14 |

**Rebalanced 24 Oct.** Dev 4's backend tickets #22 and #29 moved to Dev 1, so Dev 4 has quicker
tickets: reviewing the two fixes (#2, #44), the safety doc (#9), the
fixture sign-off (#10) and the chess cases (#23), which suit the person who wrote the game.

## Features, after the MVP

| Issue | Owner |
|---|---|
| [#20](https://github.com/Prittstick22/axolotl/issues/20) Build the Search screen (Spec P1; pull forward if the MongoDB demo needs it) | Dev 5 |
| [#24](https://github.com/Prittstick22/axolotl/issues/24) Parse Python 2-only syntax (chess parses today, so the demo doesn't need it) | Dev 3 |
| [#25](https://github.com/Prittstick22/axolotl/issues/25) Graph accuracy: heuristic edges, more dynamic imports, grouped stdlib nodes | Dev 3 |
| [#26](https://github.com/Prittstick22/axolotl/issues/26) Make snapshots available on every machine | Dev 3 |
| [#27](https://github.com/Prittstick22/axolotl/issues/27) Run API additions for the Run screen | Dev 2 |
| [#46](https://github.com/Prittstick22/axolotl/issues/46) Runtime timings in the report (decide first) | Dev 4, Dev 2 |
| [#54](https://github.com/Prittstick22/axolotl/issues/54) Delete `legacy_orders` once chess runs end to end and the tests use chess | Anyone |

### Requested by the project manager, 24 Oct

All of these are possible. None is MVP, and the feature freeze is Sat 22:00, so most will land
after submission. The ones marked **cheap** reuse what the backend already does and could be
pulled forward if the MVP is on track.

| Feature | Issue | Owner | Possible? |
|---|---|---|---|
| Store the migrated code's methods and dependencies in Atlas, next to the legacy ones | [#33](https://github.com/Prittstick22/axolotl/issues/33) | Dev 3 | Yes. Today only the legacy code is analysed (at import). The catch: the backend's Python 3.12 can't parse 3.14-only syntax, so the migrated code must be parsed with a 3.14 parser. #34 and #39 build on this. |
| Dependency changes: what was legacy and what it became | [#34](https://github.com/Prittstick22/axolotl/issues/34) | Dev 3, Dev 5 | Yes, after #33. Decided: both the project's own module imports and third-party packages, plus standard-library renames and the declared Python version. |
| Migration summary: an overview of what changed | [#35](https://github.com/Prittstick22/axolotl/issues/35) | Dev 2, Dev 5 | Yes. Most of the data is in the report and the run's events already. |
| Git-style diff of the whole project | [#36](https://github.com/Prittstick22/axolotl/issues/36) | Dev 5 | Yes, **cheap**: `GET /runs/{id}/diff` already returns every changed file with a unified diff. |
| Dependencies between classes | [#37](https://github.com/Prittstick22/axolotl/issues/37) | Dev 3, Dev 5 | Yes. Partly planned: #25 adds the inheritance and call edges; #37 adds the class-level graph. |
| Agent activity terminal | [#38](https://github.com/Prittstick22/axolotl/issues/38) | Dev 5 | Yes, **cheap**: the Run screen's timeline (`a27ac92`) already shows Argon's tool calls. Real events only, no simulated progress. |
| Documentation for the legacy and the migrated code | [#39](https://github.com/Prittstick22/axolotl/issues/39) | Dev 1 | Yes. Argon already describes every legacy function for search; the migrated side needs #33. Costs model quota (#11). |
| Export the migrated project | [#40](https://github.com/Prittstick22/axolotl/issues/40) | Dev 2, Dev 5 | Yes, **cheap**: the backend already writes `final_tree.zip`, a patch and `report.json`. It needs a button and a bundle. |
| Upgrade chosen modules without the whole codebase | [#41](https://github.com/Prittstick22/axolotl/issues/41) | Spike | Probably. A good idea for Modern Python 3, where old and new code share an interpreter. Risky for Python 2.7 → 3.14, because a half-migrated project must still run on one interpreter. Decide in the spike. |
| Security report | [#42](https://github.com/Prittstick22/axolotl/issues/42) | Stretch | Yes. Decided: the migrated project's packages are checked against a vulnerability database such as OSV, from the backend, since the sandbox has no network; only package names and versions are sent. Plus a static scan of both trees and the Python 2.7 end-of-life finding. |
| Saint.AI notifications over WhatsApp | [#43](https://github.com/Prittstick22/axolotl/issues/43) | Spike, stretch | Unknown until we have Saint.AI's documentation. Messages must never contain code, held-out cases or secrets. |

## By developer

| Dev | MVP (assigned) | Bugs | Features |
|---|---|---|---|
| Dev 1 · `Prittstick22` | [#47](https://github.com/Prittstick22/axolotl/issues/47) (with Dev 2), [#4](https://github.com/Prittstick22/axolotl/issues/4), [#13](https://github.com/Prittstick22/axolotl/issues/13), [#22](https://github.com/Prittstick22/axolotl/issues/22), [#29](https://github.com/Prittstick22/axolotl/issues/29) | — | [#39](https://github.com/Prittstick22/axolotl/issues/39) |
| Dev 2 · `mh-husssain` | [#47](https://github.com/Prittstick22/axolotl/issues/47) (Dev 2 half done), [#3](https://github.com/Prittstick22/axolotl/issues/3), [#21](https://github.com/Prittstick22/axolotl/issues/21), [#8](https://github.com/Prittstick22/axolotl/issues/8), [#7](https://github.com/Prittstick22/axolotl/issues/7), [#30](https://github.com/Prittstick22/axolotl/issues/30) | [#16](https://github.com/Prittstick22/axolotl/issues/16), [#17](https://github.com/Prittstick22/axolotl/issues/17), [#18](https://github.com/Prittstick22/axolotl/issues/18), [#32](https://github.com/Prittstick22/axolotl/issues/32) | [#27](https://github.com/Prittstick22/axolotl/issues/27), [#35](https://github.com/Prittstick22/axolotl/issues/35), [#40](https://github.com/Prittstick22/axolotl/issues/40), [#46](https://github.com/Prittstick22/axolotl/issues/46) |
| Dev 3 · `Caroline3110` | [#11](https://github.com/Prittstick22/axolotl/issues/11), [#12](https://github.com/Prittstick22/axolotl/issues/12), [#28](https://github.com/Prittstick22/axolotl/issues/28), [#30](https://github.com/Prittstick22/axolotl/issues/30) (with Dev 2) | — | [#24](https://github.com/Prittstick22/axolotl/issues/24), [#25](https://github.com/Prittstick22/axolotl/issues/25), [#26](https://github.com/Prittstick22/axolotl/issues/26), [#33](https://github.com/Prittstick22/axolotl/issues/33), [#34](https://github.com/Prittstick22/axolotl/issues/34), [#37](https://github.com/Prittstick22/axolotl/issues/37) |
| Dev 4 · `WilliamParis1` | [#2](https://github.com/Prittstick22/axolotl/issues/2) (review), [#44](https://github.com/Prittstick22/axolotl/issues/44) (review), [#9](https://github.com/Prittstick22/axolotl/issues/9), [#10](https://github.com/Prittstick22/axolotl/issues/10), [#23](https://github.com/Prittstick22/axolotl/issues/23) | — | [#46](https://github.com/Prittstick22/axolotl/issues/46) |
| Dev 5 · `PanotAbhay` | [#5](https://github.com/Prittstick22/axolotl/issues/5) | — | [#20](https://github.com/Prittstick22/axolotl/issues/20), [#34](https://github.com/Prittstick22/axolotl/issues/34), [#35](https://github.com/Prittstick22/axolotl/issues/35), [#36](https://github.com/Prittstick22/axolotl/issues/36), [#37](https://github.com/Prittstick22/axolotl/issues/37), [#38](https://github.com/Prittstick22/axolotl/issues/38), [#40](https://github.com/Prittstick22/axolotl/issues/40) |
| Demo, unassigned | — | — | [#14](https://github.com/Prittstick22/axolotl/issues/14), [#15](https://github.com/Prittstick22/axolotl/issues/15) (`demo`, not MVP) |
| Anyone | — | — | [#54](https://github.com/Prittstick22/axolotl/issues/54) (cleanup), [#41](https://github.com/Prittstick22/axolotl/issues/41) (spike), [#42](https://github.com/Prittstick22/axolotl/issues/42) (stretch), [#43](https://github.com/Prittstick22/axolotl/issues/43) (spike, stretch) |

## Product features

What Axolotl does, built or planned. ✅ is on `main`; a ticket number means it isn't done yet.

### Import and safety

| Feature | State |
|---|---|
| Import the trusted `legacy_orders` test fixture (not a demo; deleted later, [#54](https://github.com/Prittstick22/axolotl/issues/54)) | ✅ |
| Upload a ZIP (static analysis only) | ✅ |
| Upload a project folder | ✅ (screen landed in `64ba380`) |
| Upload safety: traversal, symlinks, decompression bombs, size, file and depth caps; `.env`, virtualenvs and caches dropped | ✅ |
| Declared Python version read from `setup.py` / `pyproject.toml` without running them | ✅ |
| Fast import, with search indexing in the background | [#28](https://github.com/Prittstick22/axolotl/issues/28) |

### Code analysis and graph

| Feature | State |
|---|---|
| Static AST extraction: modules, classes, functions, methods, signatures, line spans | ✅ |
| Module dependency graph; confirmed vs unresolved imports; dynamic imports flagged | ✅ |
| Import cycles collapsed into one migration unit | ✅ |
| Graph screen: pan and zoom, dependants highlighted, `/` filter, legend | ✅ |
| Module inspector: symbols, imports, dependants, read-only source | ✅ |
| Python 2-only syntax | [#24](https://github.com/Prittstick22/axolotl/issues/24) |
| Heuristic call and inheritance edges, grouped standard-library nodes | [#25](https://github.com/Prittstick22/axolotl/issues/25) |
| Dependencies between classes (class-level graph) | [#37](https://github.com/Prittstick22/axolotl/issues/37) |
| The migrated code's classes, methods and dependencies stored next to the legacy ones | [#33](https://github.com/Prittstick22/axolotl/issues/33) |
| Dependency changes: what was legacy and what it became | [#34](https://github.com/Prittstick22/axolotl/issues/34) |

### MongoDB Atlas

| Feature | State |
|---|---|
| Atlas as the only store: repositories, symbols, edges, code chunks, plans, runs, event journal, cases, verifications, counterexamples | ✅ |
| Indexes created from code at startup, safe to re-run | ✅ |
| Keyword search (Atlas Search), vector search (Gemini embeddings), native hybrid search (`$rankFusion`) | API ✅; screen [#20](https://github.com/Prittstick22/axolotl/issues/20) |
| Snapshots available on every machine | [#26](https://github.com/Prittstick22/axolotl/issues/26) |

### Planning

| Feature | State |
|---|---|
| Migration targets, with unsupported ones shown disabled and why | ✅ |
| Modern Python 3 target (used only by the test fixture) | ✅ |
| Python 2.7 → 3.14 target (MVP demo) | [#21](https://github.com/Prittstick22/axolotl/issues/21) |
| Argon proposes a dependency-ordered plan, validated in code | ✅ |
| Per-unit risk and visible case counts | ✅, risk wrong: [#3](https://github.com/Prittstick22/axolotl/issues/3) |
| The user approves the plan before a run starts | ✅ |

### Migration run

| Feature | State |
|---|---|
| Scheduler: a unit runs only after its dependencies pass; dependants of a blocked unit are skipped | ✅ |
| Argon migrates each unit with real tools: overview, graph, source, symbol lookup, search, visible cases, propose patch | ✅ |
| Bounded repair loop driven by the verifier's counterexamples | ✅ offline; live: [#4](https://github.com/Prittstick22/axolotl/issues/4) |
| Patch budget and per-unit file allowlist enforced outside the model; prompt-injection defences | ✅ |
| Run screen: live states, graph coloured by unit state, timeline of Argon's tool calls, gate chips, cancel | ✅ |
| Agent activity terminal | [#38](https://github.com/Prittstick22/axolotl/issues/38) |
| "Attempt 2 of 3", grey skipped units, one active run per plan | [#27](https://github.com/Prittstick22/axolotl/issues/27), [#18](https://github.com/Prittstick22/axolotl/issues/18), [#32](https://github.com/Prittstick22/axolotl/issues/32) |
| Upgrade chosen modules only | Spike [#41](https://github.com/Prittstick22/axolotl/issues/41) |

### Verification and sandbox

| Feature | State |
|---|---|
| Docker sandbox: no network, read-only code, non-root user | ✅ |
| Immutable baseline captured from the original code | ✅ |
| Unit gates (compile, differential, impacted tests, existing tests, sandbox policy) and final integration | ✅; [#2](https://github.com/Prittstick22/axolotl/issues/2) for units with no cases |
| Visible and held-out cases; held-out cases never shown to the model | ✅; per-fixture routing [#29](https://github.com/Prittstick22/axolotl/issues/29) |
| Python 2.7 baseline and Python 3.14 candidate | [#22](https://github.com/Prittstick22/axolotl/issues/22) |
| Chess behaviour cases | [#23](https://github.com/Prittstick22/axolotl/issues/23) |

### Results

| Feature | State |
|---|---|
| Run report built from recorded evidence, with caveats; artefact downloads | ✅ (screen 08, [#6](https://github.com/Prittstick22/axolotl/issues/6) closed) |
| Old vs new diff, gate results, counterexamples, attempt history | API ✅ (verifications [#45](https://github.com/Prittstick22/axolotl/issues/45) closed; baseline [#44](https://github.com/Prittstick22/axolotl/issues/44) fixed, to review); screen [#5](https://github.com/Prittstick22/axolotl/issues/5) |
| Runtime before and after, per unit | [#46](https://github.com/Prittstick22/axolotl/issues/46) (decide first) |
| Argon's narrative of the run, labelled as model-generated | API ✅ |
| Migration summary: an overview of what changed | [#35](https://github.com/Prittstick22/axolotl/issues/35) |
| Git-style diff of the whole project | API ✅; screen [#36](https://github.com/Prittstick22/axolotl/issues/36) |
| Documentation for the legacy and the migrated code | [#39](https://github.com/Prittstick22/axolotl/issues/39) |
| Export the migrated project (ZIP, patch, report) | API ✅; button and bundle [#40](https://github.com/Prittstick22/axolotl/issues/40) |
| Security report | Stretch [#42](https://github.com/Prittstick22/axolotl/issues/42) |
| Notifications over WhatsApp through Saint.AI | Spike, stretch [#43](https://github.com/Prittstick22/axolotl/issues/43) |

### System

| Feature | State |
|---|---|
| Live status chips: MongoDB, search indexes, sandbox runner, Gemini | ✅ |
| Honest failures: `503 DATABASE_UNAVAILABLE`, `SEARCH_INDEX_NOT_READY`, never a fake answer | ✅ |

### In the Spec but not planned

Nobody is working on these. Say if any should become tickets.

- Import from a GitHub URL (Spec P1).
- Open a GitHub pull request with the migrated code (Spec P2).
- Other targets: FastAPI, sync → async, Python → TypeScript. They are shown in the Plan screen as
  disabled (Spec P2).

## Sources

[Spec](planning/Axolotl_Product_Requirements_and_Implementation_Handoff.md) §0, §2.2, §3.1, §14
and §16 · [IMPLEMENTATION.md](IMPLEMENTATION.md) · [GitHub issues](https://github.com/Prittstick22/axolotl/issues) ·
[chess-demo.md](chess-demo.md) · [mongodb-atlas.md](mongodb-atlas.md) TODO list ·
[BUILD_LOG.md](../BUILD_LOG.md) · the five dev briefs in [planning/](planning/) · CI run
`40043393667` on `main`.
