"""Optional sandbox timings, from the final accepted tree's recorded evidence."""

from statistics import median

from app.models.contracts import PerformanceSummary, UnitTiming
from app.orchestration import ports

METHOD = (
    "Sandbox CPU timings on tested cases only: median of 5 calls after 1 warm-up, "
    "then median across the same cases per unit. Reset, imports, input decoding, "
    "output encoding and container startup are excluded. Spread is the median "
    "per-case interquartile range (IQR); it is a noise heuristic, not statistical "
    "significance. Different interpreters and host load can affect results."
)


def _samples(outcome):
    values = outcome.duration_samples_ns
    if (outcome.status != "ok" or not values or len(values) != 5
            or any(type(v) is not int or v < 0 for v in values)
            or outcome.duration_ns != median(values)):
        return None
    return sorted(values)


async def build_performance(run, revision, verification_id):
    if (not revision or not verification_id or run.runner_mode != "trusted_docker"
            or run.state not in {"completed", "completed_with_warnings"}):
        return None
    try:
        results = await ports.list_verifications(run.run_id)
        final = next((v for v in results if v.verification_id == verification_id
                      and v.run_id == run.run_id and v.unit_id is None
                      and v.workspace_revision == revision
                      and v.runner_mode == "trusted_docker"
                      and v.all_required_gates_passed), None)
        if final is None:
            return None
        cases = await ports.load_cases(run.repository_id, include_held_out=True)
    except ports.IntegrationUnavailable:
        return None

    owners = {c.case_id: c.unit_id for c in cases}
    passed_units = {u.unit_id for u in run.units if u.status == "passed"}
    grouped = {}
    seen = set()
    for comparison in final.comparisons:
        unit = owners.get(comparison.case_id)
        if (unit not in passed_units or comparison.verdict != "match"
                or comparison.case_id in seen):
            continue
        seen.add(comparison.case_id)
        before, after = _samples(comparison.old), _samples(comparison.new)
        if before is not None and after is not None:
            grouped.setdefault(unit, []).append((before, after))

    units = []
    for unit, pairs in sorted(grouped.items()):
        before = median(old[2] for old, _ in pairs)
        after = median(new[2] for _, new in pairs)
        # Five sorted samples: interpolated 25th/75th percentiles are [1]/[3].
        spread = max(median(old[3] - old[1] for old, _ in pairs),
                     median(new[3] - new[1] for _, new in pairs))
        units.append(UnitTiming(
            unit_id=unit, cases_timed=len(pairs), before_median_us=before / 1000,
            after_median_us=after / 1000, within_noise=abs(after - before) <= spread,
        ))
    return PerformanceSummary(method=METHOD, units=units) if units else None
