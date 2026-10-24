# Three submission videos (#15)

**Owner: Will (Dev 4). Status: scripts and shot lists ready; recordings/uploads
pending.** Use real chess runs only, following the newer chess-only decision in
#70. The older orders fallback in #15 is superseded. No video link or successful
upload is claimed until it has been opened and played from the submitted URL.

Prerequisites: [demo-script.md](demo-script.md) readiness checks, two recorded
rehearsals, actual model-generated patch, final verification and artifact downloads.
Use the approved demo build and real run IDs in every take. Screenshots, test output
and recordings must retain their original meaning when edited. Do not hide a failed
gate to imply it passed. Label any time cut (“Later in the same run”) and earlier
footage (“Recorded earlier run at YYYY-MM-DD HH:MM TZ — run_id”).

## Gemini / DeepMind: 3:00

| Time | Picture | Narration / evidence |
|---|---|---|
| 0:00–0:20 | Will's chess source and declared runtime | “Will wants to modernize this chess project without breaking its moves. Axolotl makes the proposed change and the evidence inspectable.” |
| 0:20–0:40 | Dependency graph and approved plan | “Parsed imports determine migration order. A person approves the plan.” Show the actual approval. |
| 0:40–1:15 | Run's actual Gemini/tool trace and proposed diff | Name the model ID recorded for this run. “The agent uses tools to inspect and patch the project; its own explanation cannot make a gate pass.” |
| 1:15–1:55 | Real mismatch and repair, if this run has one | Show original/candidate outputs and the next attempt. If no mismatch occurred, use the separately labelled chess promotion test from the demo script; do not imply it is the model's output. |
| 1:55–2:30 | Accepted revision, runtime evidence and final verification | Show 2.7 baseline/3.14 candidate identity and the final whole-project result. Label any real time cut. |
| 2:30–3:00 | Report, patch download and limitation | “These cases agree. That is not a proof of equivalence. Execution is restricted to trusted fixtures, and a human still reviews the patch.” |

Select footage that proves a real model changed code, not merely that the prepared
source already runs on both interpreters. Use a 3:00 target; verify the current
submission form's length requirement before export/upload.

## MongoDB: 3:00

Coordinate claims with the evidence in [mongodb-bounty.md](mongodb-bounty.md).
Its historical orders queries/rankings are not chess evidence. The current Graph
screen's **Filter modules** is local filtering, not Atlas search. Until a search UI
exists, record the real API response in a prepared read-only client/API-docs tab.

| Time | Picture | Narration / evidence |
|---|---|---|
| 0:00–0:20 | `/api/v1/system/status`, redacted if needed | Show real MongoDB/index readiness. “The graph and evidence are backed by Atlas.” |
| 0:20–0:45 | Chess graph, then its stored symbols/edges in a read-only view | “Source parsing produces the graph. Atlas stores it and serves it back to the app.” |
| 0:45–1:35 | Same chess query in keyword, vector and hybrid modes | Start with exact identifier `legal_moves`; use a rehearsed semantic query such as “which moves leave the king in check?”. Display actual hits, ranks, `mode_used` and `native_hybrid`. State only the observed ranking. |
| 1:35–2:00 | Open the returned chess symbol/source | “Retrieval gives the agent relevant code. It does not decide whether the patch is correct.” |
| 2:00–2:30 | Reload a real chess run and its verification/counterexample records | “Run events and verifier evidence persist, so we can inspect the result after the agent has finished.” If the run has no counterexample, show the actual verification record. |
| 2:30–3:00 | Failure behavior in an isolated test instance or clearly labelled test recording | “When Atlas is unavailable, these journeys return an error; they do not fabricate a cached success.” Show a real 503. Do not take down the shared demo database. |

Search endpoint: `GET /api/v1/repositories/{repo_id}/search` with parameters `q`,
`mode=keyword|vector|hybrid` and `limit=5`. Prepare all three requests against the
same repository and query. If native fusion falls back, say so and show false;
do not overlay a true label. If the semantic query is unhelpful, pick a clearer
chess question during rehearsal and record its actual outcome. Preserve quota.

## Agents That Ship: 3:00 (allowed window in #15: 2–4 minutes)

| Time | Picture | Narration / evidence |
|---|---|---|
| 0:00–0:20 | Will's problem and chess source | State the named demo user and the confidence problem. |
| 0:20–0:45 | Graph, plan and approval | Show the human checkpoint before execution. |
| 0:45–1:25 | Actual patch/tool trace | Show what the agent changed and how it used the repository context. Name the actual runtime model. |
| 1:25–2:05 | Verifier gates, mismatch/repair if real, final accepted result | Explain that the deterministic verifier controls acceptance. Any injected regression must be separately labelled. |
| 2:05–2:35 | Downloadable diff/report, limitations | Show concrete output and the tested-case boundary. |
| 2:35–3:00 | Build/tool and approval note | “We used Codex to help implement and test parts of Axolotl. Changes are proposed in pull requests for maintainer review. In the product, a person approves the migration plan; Gemini proposes changes and the independent verifier checks them.” Adjust the note to match the team's actual tools and merge state. |

## Recording, export and upload checklist

1. Record a continuous master take with audible narration and readable text. Use
   1920×1080 if available, 30 fps, MP4 H.264/AAC for a broadly playable export.
   Do not record passwords, API keys, hidden-case inputs or unrelated personal data.
2. Save the master and edited export separately, with UTC timestamp and run ID in
   the filenames. Suggested stems: `axolotl-gemini`, `axolotl-mongodb`,
   `axolotl-agents-that-ship`. Preserve the original evidence and a note of cuts.
3. Check the full export for audio, readable IDs, truthful captions, duration,
   beginning/end frames and correct model/runtime claims. A script is not footage.
4. Upload to the destination supplied by the team and accepted by the submission
   form. Choose the intended audience/access setting; do not publish publicly by
   default. Add a short description identifying the build, run and tested-case limit.
5. Open each submitted URL in the intended viewer's access context. Check playback,
   permissions, audio and that it is the correct final version. Save the checked
   URL, duration, timestamp and reviewer in [rehearsal-log.md](rehearsal-log.md).

The ticket remains open until all three actual videos and checked links exist.
Required external inputs are the working demo machine/URL, a genuine completed
chess run and the team's upload/submission destination. No credentials should be
pasted into these documents.
