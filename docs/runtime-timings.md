# Runtime timings (#46)

The verifier measures successful behaviour cases inside the existing locked-down
Linux container. The initial execution supplies the behaviour verdict and warms
the function; five further executions supply process CPU-time samples. The
harness resets fixture state and decodes fresh inputs before every repetition.
It excludes reset, callable resolution, input decoding, output encoding and
container startup from the interval. Calls within a multi-step case are measured
together. Python 2.7 uses Unix `time.clock`; Python 3 uses `process_time_ns`.
Nanoseconds are the storage unit, not a claim about timer resolution.

`CaseOutcome.duration_ns` contains the median; the additive optional
`duration_samples_ns` holds all five samples. Older records remain readable.
Raised, timed-out, changing-output or incomplete measurements have no timings.
A separate per-case timeout bounds the five benchmark repetitions. A benchmark
failure discards its samples and does not replace the original behaviour result.
This adds up to five calls per successful case and an additional timeout budget;
it is intended for the small allowlisted fixtures, not arbitrary application load.

The report uses only paired, matching cases in the successful final whole-project
verification of the final accepted revision. It groups by registered case owner
and includes only passed units. It never substitutes an earlier attempt, mixes
unpaired cases, or treats missing timing as zero. Failed/cancelled runs and legacy
records without complete samples keep `performance: null`.

For each unit the report shows the median of its case medians, in microseconds.
The spread estimate is the median of each case's sample IQR (sorted sample 4
minus sample 2). `within_noise` means the absolute before/after median difference
does not exceed the larger spread estimate. This is a descriptive heuristic,
not a statistical significance test. No speedup badge or production-performance
claim is made. CPU time excludes waiting and does not measure user latency.
Interpreter versions differ for the chess migration; both interpreter and code
changes can affect the numbers. Hardware, host load and timer resolution matter.

The Report displays its runtime panel only when measured units exist. Its method
label explicitly limits the evidence to sandbox CPU timings on tested cases.

Validation: unit tests cover fresh inputs/state resets, warm-up exclusion,
incomplete/repeated-output failures, persistence, behaviour equality, paired
report aggregation and accepted-revision selection. The real-container runtime
test checks measurements on Python 2.7 and 3.14 alongside unchanged outputs.
