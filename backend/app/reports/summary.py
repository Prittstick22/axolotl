"""Migration summary: one overview of what a run changed - owner: Dev 1 (#35).

Shown before the detail on the Report and Changes screens. Nothing here is new data:
every number is read from what the run already recorded, and each section says where
it came from (`basis`):

  report      the persisted `RunReport`: outcomes, attempts, totals, caveats.
  journal     the run's events: the last gate result per (unit, gate) for tests run,
              counterexamples, and Argon's own `patch_proposed` summary.
  diff        lines added and removed, counted from the same unified diff the Changes
              screen shows (baseline vs the last accepted revision).
  static_ast  the declared Python version and the dependency changes (#34).

The report and the journal are in Atlas, so they answer on any machine. The diff and
the dependency changes are read from the run's trees on disk, so on another laptop
they are `null` with a warning rather than a 409: the rest of the summary is still
true there.

Computed on request, never stored: it works for runs reported before it existed.
"""

from __future__ import annotations

import html
from collections import defaultdict
from typing import Any

from app.errors import REPORT_NOT_READY, AppError
from app.orchestration import events, ports, store
from app.reports.builder import (
    _final_gate_results,
    _unique_counterexamples,
    get_report,
)

BASELINE = "rev_baseline"


def count_lines(unified_diff: str) -> tuple[int, int]:
    """(added, removed) in a unified diff, not counting the `+++`/`---` file headers."""
    added = removed = 0
    for line in unified_diff.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            added += 1
        elif line.startswith("-") and not line.startswith("---"):
            removed += 1
    return added, removed


async def run_summary(run_id: str) -> dict[str, Any]:
    """GET /runs/{run_id}/summary. 404 `REPORT_NOT_READY` until the run is reported."""
    run = await store.get_run(run_id)  # 404 for an unknown run
    report = await get_report(run_id)
    if report is None:
        raise AppError(
            REPORT_NOT_READY,
            f"No report for {run_id} yet; the summary is built from it",
            status_code=404, details={"run_id": run_id},
        )
    plan = await store.get_plan(run.plan_id)
    journal = await events.list_all_events(run_id)
    accepted = [e for e in journal if e.event_type == "snapshot_accepted"]
    revision = accepted[-1].payload.get("revision") if accepted else None
    warnings: list[str] = []

    tests: dict[str | None, dict[str, Any]] = defaultdict(
        lambda: {"passed": 0, "failed": 0, "inconclusive": 0, "gates": {}}
    )
    for gate in _final_gate_results(journal):
        entry = tests[gate.unit_id]
        for key in ("passed", "failed", "inconclusive"):
            entry[key] += int(gate.payload.get(key, 0))
        entry["gates"][gate.payload.get("gate")] = gate.payload.get("status")

    states = {s.unit_id: s for s in report.units}
    passed_units = {u for u, s in states.items() if s.status == "passed"}
    found: dict[str | None, int] = defaultdict(int)
    for counterexample in _unique_counterexamples(journal):
        found[counterexample.unit_id] += 1

    proposals = {e.unit_id: e.payload.get("summary") for e in journal
                 if e.event_type == "patch_proposed" and e.unit_id}

    lines = await _lines_by_path(run_id, revision, warnings)
    python, dependencies = await _static(run_id, run.repository_id, revision, warnings)

    units = []
    for unit in plan.units:
        state = states.get(unit.unit_id)
        status = state.status if state else "not_planned"
        unit_lines = None
        if lines is not None:
            counted = [lines[p] for p in unit.file_paths if p in lines]
            unit_lines = {"added": sum(a for a, _ in counted),
                          "removed": sum(r for _, r in counted)}
        text = proposals.get(unit.unit_id)
        units.append({
            "unit_id": unit.unit_id,
            "paths": list(unit.file_paths),
            "status": status,
            "attempts": state.attempts if state else 0,
            "accepted_revision": state.accepted_revision if state else None,
            "tests": dict(tests.get(unit.unit_id) or
                          {"passed": 0, "failed": 0, "inconclusive": 0, "gates": {}}),
            "counterexamples": {
                "found": found.get(unit.unit_id, 0),
                "resolved": found.get(unit.unit_id, 0) if unit.unit_id in passed_units else 0,
            },
            "lines": unit_lines,
            # The last proposal for a unit that passed is the patch that was accepted.
            "argon_summary": {"text": text, "basis": "model_written",
                              "accepted": status == "passed"} if text else None,
        })

    statuses = [u["status"] for u in units]
    summary = {
        "run_id": run_id,
        "repository_id": run.repository_id,
        "verdict": report.verdict,
        "revision": revision,
        "units": units,
        "totals": {
            "units": len(units),
            "verified": statuses.count("passed"),
            "blocked": statuses.count("blocked"),
            "skipped": statuses.count("skipped"),
            "files_changed": len(report.changed_files),
            "lines_added": sum(a for a, _ in lines.values()) if lines is not None else None,
            "lines_removed": sum(r for _, r in lines.values()) if lines is not None else None,
            "tests_passed": report.tests_passed,
            "tests_failed": report.tests_failed,
            "tests_inconclusive": report.tests_inconclusive,
            "counterexamples_found": report.counterexamples_found,
            "counterexamples_resolved": report.counterexamples_resolved,
            "final_integration": dict(tests[None]) if None in tests else None,
        },
        "python": python,
        "dependencies": dependencies,
        "caveats": list(report.coverage_caveats),
        "limitations": list(report.limitations),
        "basis": {"units": "report", "tests": "journal", "counterexamples": "journal",
                  "argon_summary": "journal", "lines": "diff", "python": "static_ast",
                  "dependencies": "static_ast", "caveats": "report"},
        "warnings": warnings,
    }
    summary["markdown"] = render(summary)
    return summary


async def _lines_by_path(
    run_id: str, revision: str | None, warnings: list[str]
) -> dict[str, tuple[int, int]] | None:
    if revision is None:
        return {}  # nothing accepted: nothing changed, which is a known zero
    from app.sandbox.workspaces import revision_path

    if not revision_path(run_id, revision).is_dir():
        warnings.append("line counts need the run's accepted revision, which is not on "
                        "this machine")
        return None
    try:
        files = await ports.diff(run_id, BASELINE, revision)
    except ports.IntegrationUnavailable as exc:
        warnings.append(f"line counts unavailable: {exc}")
        return None
    return {f["path"]: count_lines(f.get("unified_diff", "")) for f in files}


async def _static(
    run_id: str, repository_id: str, revision: str | None, warnings: list[str]
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """The declared Python version and the dependency changes, from both trees."""
    if revision is None:
        return None, None
    from app.graph.changes import compare_trees
    from app.ingestion import snapshot_path
    from app.sandbox.workspaces import revision_path

    before, after = snapshot_path(repository_id), revision_path(run_id, revision)
    if not before.is_dir() or not after.is_dir():
        warnings.append("the Python version and dependency changes need both trees, "
                        "which are not on this machine")
        return None, None
    changes = compare_trees(before, after)
    python = {"before": changes["python"]["before"]["declared"],
              "after": changes["python"]["after"]["declared"]}
    renames = {key: [f"{p['before']} -> {p['after']}" for m in changes["modules"]
                     for p in m.get(key, [])]
               for key in ("stdlib_renamed", "builtins_replaced", "methods_replaced")}
    dependencies = {
        "modules_changed": len(changes["modules"]),
        "imports_added": sum(len(m.get("imports_added", [])) for m in changes["modules"]),
        "imports_removed": sum(len(m.get("imports_removed", [])) for m in changes["modules"]),
        **renames,
        "packages": changes["packages"],
        "warnings": changes.get("warnings", []),
    }
    return python, dependencies


# --------------------------------------------------------------------------
# markdown (also axolotl/SUMMARY.md in the export)
# --------------------------------------------------------------------------

def _cell(text: Any) -> str:
    return html.escape(str(text), quote=False).replace("|", "\\|").replace("\n", " ")


def render(summary: dict[str, Any]) -> str:
    t = summary["totals"]
    lines = "n/a" if t["lines_added"] is None else f"+{t['lines_added']} / -{t['lines_removed']}"
    python = summary["python"]
    out = [
        f"# Migration summary: {summary['run_id']}", "",
        f"Verdict **{summary['verdict']}**, revision `{summary['revision'] or 'none'}`.", "",
        "## Totals", "",
        f"- Units verified: **{t['verified']} of {t['units']}** "
        f"({t['blocked']} blocked, {t['skipped']} skipped)",
        f"- Files changed: {t['files_changed']}; lines {lines}",
        f"- Checks: {t['tests_passed']} passed, {t['tests_failed']} failed, "
        f"{t['tests_inconclusive']} inconclusive",
        f"- Counterexamples: {t['counterexamples_found']} found, "
        f"{t['counterexamples_resolved']} resolved",
        "- Declared Python: " + (f"`{python['before']}` → `{python['after']}`"
                                 if python else "not read on this machine"),
        "",
        "## Caveats", "",
        *(f"- {_cell(c)}" for c in summary["caveats"] + summary["limitations"]),
        "",
        "## Per module", "",
        "| Unit | Files | Outcome | Attempts | Checks passed / failed | Lines | "
        "Argon's summary (model-written, not verified) |",
        "|---|---|---|---|---|---|---|",
    ]
    for unit in summary["units"]:
        changed = ("n/a" if unit["lines"] is None
                   else f"+{unit['lines']['added']} / -{unit['lines']['removed']}")
        note = unit["argon_summary"]
        text = "" if not note else _cell(note["text"]) + ("" if note["accepted"]
                                                         else " _(not accepted)_")
        out.append(
            f"| `{unit['unit_id']}` | {', '.join(f'`{p}`' for p in unit['paths'])} | "
            f"{unit['status']} | {unit['attempts']} | {unit['tests']['passed']} / "
            f"{unit['tests']['failed']} | {changed} | {text} |"
        )

    deps = summary["dependencies"]
    if deps:
        out += ["", "## Dependency changes", "",
                f"- Modules with changed imports: {deps['modules_changed']} "
                f"({deps['imports_added']} imports added, {deps['imports_removed']} removed)"]
        for key, label in (("stdlib_renamed", "Standard-library renames"),
                           ("builtins_replaced", "Python 2 builtins replaced"),
                           ("methods_replaced", "Python 2 methods replaced (heuristic)")):
            if deps[key]:
                out.append(f"- {label}: " + ", ".join(f"`{x}`" for x in deps[key]))
        packages = deps["packages"]
        out.append(f"- Third-party packages: added {packages['added'] or 'none'}, removed "
                   f"{packages['removed'] or 'none'}, kept {packages['kept'] or 'none'}")

    if summary["warnings"]:
        out += ["", "## Not available on this machine", "",
                *(f"- {_cell(w)}" for w in summary["warnings"])]
    out += ["", "_Every number comes from the run's report and its recorded events._"]
    return "\n".join(out) + "\n"
