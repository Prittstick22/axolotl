# Three-minute chess demo (#14)

**Owner/operator: Will (Dev 4).** A second person may speak while Will clicks;
otherwise Will does both. This follows the 24 October chess-only decision and #70,
which supersede the older `legacy_orders` fallback in #14/#15. The fallback is a
recording of chess, never a different project. The 1:00–1:30 introduction is in
[presentation.md](presentation.md) and precedes these three minutes.

**Status: script ready; real-stack recording and two rehearsals pending.** This
document does not establish that a model-generated chess migration has completed.
At the base used to prepare this kit, #22's interpreters are implemented, #23's
24-case suite is in PR #69, and `python2_to_3` remains disabled in
`backend/app/api/targets.py`. Do not start the live sequence until that target is
enabled through the reviewed integration and a real end-to-end run succeeds.

## Before the clock starts

- Use the same commit, demo machine, Atlas database and model as the recording.
  Record their identifiers in [rehearsal-log.md](rehearsal-log.md).
- Docker must be running. Build/check all production images using
  [runner/README.md](../runner/README.md). Check the Python 2.7 **and** 3.14
  images: `/api/v1/system/status` checks the default runner only, so its green
  chip alone does not prove the two chess interpreters are present.
- Run `uv run pytest tests/integration/test_sandbox_runtimes_live.py -v -rs`
  from `backend/`. Require the container tests to pass, not skip. After #23 lands,
  run its chess-case tests too, following [chess-demo.md](chess-demo.md).
- `/api/v1/system/status`: MongoDB reachable, keyword and vector indexes ready,
  Gemini reachable with the intended model. Confirm that model ID in an actual
  run record. Do not claim a model merely because its name is configured.
- `/api/v1/migration-targets`: `python2_to_3` supported. Import the exact trusted
  `demo/chess` folder in advance, then confirm execution eligibility. Import and
  indexing can take minutes; neither belongs inside the timed demo.
- Check `/api/v1/repositories/{repo_id}/index` is ready and has embeddings.
  Preserve query/generation quota; do not re-index repeatedly before presenting.
- Confirm the imported chess case inventory is 12 visible plus 12 held out after
  #23. Keep hidden inputs off screen and out of the agent's initial context.
- Prepare a fresh plan, the Graph, Run and Report tabs, and the genuine earlier
  chess recording. Save the repository, plan and run URLs in the rehearsal log.
  Keep the recorded run distinct from the live one.
- Use readable browser zoom, hide notifications and keep credentials off screen.
  Test microphone and system audio. Use a stopwatch visible only to the operator.

## Timed sequence

Times are targets to validate during rehearsal, not measured execution durations.

| Time | Will clicks / shows | Speaker says | Evidence the judge sees |
|---|---|---|---|
| 0:00–0:15 | Already-imported chess Overview/runtime metadata, then the prepared Plan | “This is Will's terminal chess game. The source declares Python 2.7; the selected target is 3.14. Importing and indexing happened before the clock.” | Real repository, declared source version and selected target; metadata is not an execution verdict. |
| 0:15–0:30 | Review dependency order; **Approve plan**, then **Start migration** | “I approve the plan. Dependencies go first; the agent proposes a change and the verifier decides whether it can advance.” | Human approval and a new run ID, with actual events starting. |
| 0:30–0:50 | Graph tab; select `local_chess/cli.py` and one import edge | “These modules and imports come from parsing the source. A changed dependency also determines which checks need to run.” | Source-derived graph and real module contents. Do not call Filter modules semantic search. |
| 0:50–1:15 | Return to Run; show model/tool events and the current unit | “The agent's work is recorded. A generated patch is a proposal until its required checks pass.” | Actual tool/event trace, unit status and attempt count. Do not promise a failure or completion time. |
| 1:15–1:50 | Click a unit in Run to open Verification; show diff, gates and a counterexample if present | “The original and candidate see the same cases. A mismatch blocks this attempt. Hidden cases can catch a change the visible suite misses.” | Recorded before/after evidence and real verdict. If the live run has no mismatch, use the labelled isolated check below. |
| 1:50–2:20 | Show the next real repair attempt and accepted diff, if present | “If repair succeeds, the accepted snapshot is the version that passed. A failed or inconclusive check stays visible.” | Real repair chain, or an honest current blocked/pending status. At 1:50, switch to the earlier chess recording if there is no completed live result. |
| 2:20–2:45 | Open Report for the same run; show verdict, caveats and **Patch** download | “The report and downloadable patch refer to recorded evidence and the accepted revision. Passing means agreement on these tested cases.” | Final verification, counts, patch and limitations. If using a recording, keep its label visible. |
| 2:45–3:00 | Hold on the report | “This is evidence you can inspect, not a proof of equivalence. Only trusted fixtures execute; unknown uploads remain static analysis. The next step is a human review of the patch.” | Honest scope and a reviewable artifact. |

## If the live run passes without a regression

Say: **“This run did not produce a counterexample. Here is a separate, deliberately
broken chess candidate that tests the verifier.”** Use the chess promotion
regression check in `backend/tests/integration/test_chess_cases_docker.py` after
PR #69 lands. It replaces the requested promotion piece with a queen; visible
cases still match and the held-out underpromotion/black-promotion cases fail.

Prepare its real container output in advance with
`uv run pytest tests/integration/test_chess_cases_docker.py -k promotion -v -rs`
from `backend/`. Require the selected test to run and pass, not skip or select
zero tests. Show its output with the persistent label **“Separate verifier check —
deliberately injected chess promotion bug”**. This is the chess equivalent of the
Spec's T-11 demonstration; do not use the old orders rounding test or imply the
model produced this bug. Do not edit the live candidate to manufacture a story.

## If the live service stalls or the final report is not ready

At the 1:50 checkpoint, say: **“This live run is still [actual status]. I'll show
the result of an earlier real chess run on this build.”** Switch only if the
recording exists and matches the documented build. Keep **“Recorded earlier run
at YYYY-MM-DD HH:MM TZ — run_id”** visible throughout. A report can appear after
the run ends; an empty report panel is not a completed result.

If no genuine recording exists, show the real partial result and state the
limitation. Do not stage a success, replay seeded events as live, switch to orders,
or show a fixture compatibility test as a model migration. Use the remaining time
for the verifier evidence and limitations. A rehearsal cannot pass until the
planned hand-off, timing and fallback have actually been exercised.

## Completion record

Record two complete introduction-plus-demo rehearsals on the demo machine in
[rehearsal-log.md](rehearsal-log.md). Capture actual durations, failures, fixes and
the recording path. This ticket remains open until both rehearsals and the genuine
recorded fallback exist. Submission shot lists are in
[submission-videos.md](submission-videos.md).
