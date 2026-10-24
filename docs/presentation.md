# Will's introduction (#70)

**Deliverable: talk track, followed by the three-minute demo.** Target: 1:00–1:30.
This is ready for Will to rehearse; his delivery has not been timed. The word-count
estimate and synthetic practice audio are not human rehearsal results. Complete the two timed runs in
[rehearsal-log.md](rehearsal-log.md), including the hand-off.
The spoken track is 183 words: about 1:18 at 140 words/minute, before extra pauses.

## Spoken track

Will has a terminal chess game with legacy Python packaging. Updating it is easy
to start and hard to trust: how do you know the moves still behave correctly?

Axolotl imports the project, maps its modules and imports into a dependency graph,
and works through migration units in dependency order. A person approves the plan.
Argon, our Gemini agent, proposes changes and uses tools to inspect and repair them.

The agent never awards itself a pass. An independent verifier runs the original
and candidate on the same cases inside restricted containers. For this demo, the
baseline runs on Python 2.7 and the candidate on Python 3.14. A unit advances only
when its required checks pass.

MongoDB Atlas stores the graph, searchable code and run evidence. Keyword, vector
and hybrid search help retrieve relevant code; verification supplies the verdict.

Passing means agreement on the tested cases, not proof that every behaviour is
identical. Unrecognised uploads are analysed statically; execution is limited to
trusted fixtures. The final patch is still for a human to review.

Let's migrate Will's chess game from Python 2.7 to 3.14.

## Delivery cues

| Target elapsed time | Cue |
|---|---|
| 0:00–0:12 | Will's problem: confidence in the moves |
| 0:12–0:27 | Graph, dependency order, human-approved plan, Gemini |
| 0:27–0:48 | Independent verdict; actual 2.7/3.14 execution |
| 0:48–1:02 | Atlas stores and retrieves evidence |
| 1:02–1:18 | Tested cases, trusted execution, human review |
| 1:18–1:25 | Hand off to the already-open chess Overview |

Use the real run and speak naturally. Trim pauses or the search-mode list if a
measured take exceeds 90 seconds. Do not rush the limitation sentence. No slides
are required: the issue explicitly permits this talk-track format.

## Spoken practice reference

A synthetic reference was generated with Windows' Microsoft Hazel Desktop voice,
rate 1, and measured at **84.63 seconds** from its WAV frame count (22,050 Hz mono).
It fits the target slot and is for practicing pacing. It is not Will's voice, a
human rehearsal, a product recording or evidence that a live migration completed.
Listen for pronunciation and use the written script if the synthetic voice is unclear.

To recreate it on Windows with that installed voice, from the repository root:

```powershell
powershell -NoProfile -File demo/make_practice_audio.ps1
```

The output is `.axolotl_workspaces/demo-preparation/pitch-reference.wav` (ignored
by Git). The script refuses to overwrite an existing output; use `-OutputPath`
with a new filename for another take. `-Voice` and `-Rate` can be changed; remeasure
the resulting file if changed. Human rehearsal slots remain pending.

## Freeze check: every claim needs current evidence

- **Will:** the named owner identified in #70 and [chess-demo.md](chess-demo.md).
  This is a demo user/problem statement, not a claim of a user study or quotation.
- **Prepared source:** the supplied fixture is deliberately compatible with both
  interpreters; running it on 3.14 alone does not demonstrate a migration. The
  actual model-generated diff and final verification must be shown.
- **Dev 1 review, pending:** confirm the deployed agent works, its actual model ID,
  tool trace and repair path. “Argon” is the agent's name here; name a specific
  Gemini model only when its recorded run identity supports it.
- **Dev 2 review, pending:** confirm target availability, real plan approval,
  dependency ordering, blocked-unit behavior, final verification and downloads on
  the demonstrated build. #22 and #23 are merged; target enablement is PR #76.
  Verify the deployed target and a real run before freezing live claims.
  See [demo-script.md](demo-script.md).
- **Atlas:** check current status and search responses. Say “native Atlas hybrid
  fusion” only when `native_hybrid` is true. See [mongodb-bounty.md](mongodb-bounty.md)
  for technical evidence, not for chess search rankings; its orders examples do
  not establish how chess queries rank.
- **Sandbox:** use [runner/policy.md](../runner/policy.md). Restricted Docker is
  not a hardened multi-tenant isolation service. Do not claim arbitrary code is safe.

These are outstanding human/run checks, not approvals already received. If a
feature is unavailable on presentation day, remove that live claim or describe it
as implemented but not demonstrated; do not read the script unchanged.

## Short Q&A

| Question | Answer |
|---|---|
| Why Argon? | The Gemini agent can use repository context and tools across a propose, check and repair sequence. The independent verifier is the authority; we are not claiming this model always writes correct code. |
| How is this more than a chat wrapper? | It builds a source-derived dependency graph, applies real patches to isolated workspaces, executes checks and persists the resulting evidence and artifacts. The demo must show those actions. |
| How do you know the migration works? | We compare canonical outputs on the same visible and held-out cases and run other required gates. A final whole-project check tests the accepted tree. This is bounded evidence, not a proof of equivalence. |
| Can people execute arbitrary GitHub code? | No. Unrecognised uploads are static-only. Execution requires an allowlisted fixture matched to trusted contents, under the documented Docker restrictions. |
| What if Gemini makes a mistake? | A failing attempt is not accepted. The agent can attempt repair within its budget; blocked or inconclusive work remains visible. We do not force a successful outcome. |
| Why Atlas? | Graph data, code retrieval and run evidence share a persistent system of record. The graph scheduler still uses parsed imports; search relevance does not decide verification. |
| Are the hidden tests still independent after repair? | If a failing case is revealed for repair, the report discloses that exposure. That case no longer counts as an independent hidden check. |
| Was the original chess game really Python-2-only? | No. This is a prepared legacy-compatible fixture with Python 2 packaging that also runs from source on modern Python. We show the actual modernization diff and measured runtime evidence, not a historical claim. |
| Does the runtime panel prove it is faster? | No. If present, it reports sandbox CPU timings on those cases. Different interpreters, host conditions and measurement spread affect the numbers; it is not a production benchmark. |
