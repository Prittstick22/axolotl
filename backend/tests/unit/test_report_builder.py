"""Report tests - owner: Dev 2. Spec §11.2, §11.3.

The report is what a judge reads, so these tests are about honesty rather than
formatting: numbers must come from recorded evidence, must not be inflated, and the
caveats must say plainly what was and was not established.
"""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.models.contracts import RunEvent, RunSummary, UnitRunState
from app.orchestration import events as events_module
from app.orchestration import ports as ports_module
from app.orchestration import store as store_module
from app.reports import artefacts as artefacts_module
from app.reports import builder as builder_module
from app.reports.builder import build_report


def event(seq: int, event_type: str, unit_id=None, attempt=None, **payload) -> RunEvent:
    return RunEvent(
        run_id="run_report0001", sequence=seq, timestamp=datetime.now(UTC),
        event_type=event_type, unit_id=unit_id, attempt=attempt, payload=payload,
    )


def make_run(units=None, state="completed", runner_mode="trusted_docker",
             inconclusive=0) -> RunSummary:
    now = datetime.now(UTC)
    units = units or [
        UnitRunState(unit_id="unit_pricing", module_ids=["module_pricing"],
                     status="passed"),
    ]
    return RunSummary(
        run_id="run_report0001", plan_id="plan_r1", repository_id="repo_r1",
        state=state, current_unit=None, units=units,
        units_total=len(units),
        units_verified=sum(1 for u in units if u.status == "passed"),
        units_blocked=sum(1 for u in units if u.status == "blocked"),
        tests_passed=0, tests_failed=0, tests_inconclusive=inconclusive,
        last_event_sequence=99, model="gemini-4-argon",
        runner_mode=runner_mode, created_at=now, updated_at=now,
    )


@pytest.fixture
def world(monkeypatch, tmp_path):
    """Journal + persistence fakes. Tests set `journal` before calling build_report."""
    state: dict = {"journal": [], "saved": None, "artefacts": []}

    async def list_events(run_id, after_sequence=0, limit=200):
        return state["journal"]

    async def persist(report):
        state["saved"] = report

    async def save_artefact(artefact):
        state["artefacts"].append(artefact)
        return artefact

    async def list_artefacts(run_id):
        return state["artefacts"]

    async def unavailable(*args, **kwargs):
        raise ports_module.IntegrationUnavailable("Dev 4", "app.sandbox", "diff")

    monkeypatch.setattr(events_module, "list_events", list_events)
    monkeypatch.setattr(builder_module.events, "list_events", list_events)
    monkeypatch.setattr(builder_module, "_persist", persist)
    monkeypatch.setattr(store_module, "save_artefact", save_artefact)
    monkeypatch.setattr(store_module, "list_artefacts", list_artefacts)
    monkeypatch.setattr(ports_module, "diff", unavailable)
    monkeypatch.setattr(ports_module, "narrate_report", unavailable)
    monkeypatch.setattr(artefacts_module, "artefact_root", lambda run_id: tmp_path)
    return state


# --------------------------------------------------------------------------
# counters must not be inflated
# --------------------------------------------------------------------------

async def test_repeat_attempts_do_not_inflate_the_test_counts(world):
    """A unit verified three times has its gates emitted three times.

    Summing every gate_result would report 36 passing tests when the suite has 12.
    The honest number is the final attempt's.
    """
    world["journal"] = [
        event(1, "gate_result", unit_id="unit_pricing", attempt=1,
              gate="existing_tests", status="failed", passed=11, failed=1),
        event(2, "gate_result", unit_id="unit_pricing", attempt=2,
              gate="existing_tests", status="failed", passed=11, failed=1),
        event(3, "gate_result", unit_id="unit_pricing", attempt=3,
              gate="existing_tests", status="passed", passed=12, failed=0),
    ]

    report = await build_report(make_run())

    assert report.tests_passed == 12, (
        f"counted {report.tests_passed}; repeat attempts were summed instead of "
        "taking the final attempt"
    )
    assert report.tests_failed == 0


async def test_gates_from_different_units_are_both_counted(world):
    world["journal"] = [
        event(1, "gate_result", unit_id="unit_pricing", attempt=1,
              gate="differential", status="passed", passed=4),
        event(2, "gate_result", unit_id="unit_orders", attempt=1,
              gate="differential", status="passed", passed=6),
    ]
    report = await build_report(make_run())
    assert report.tests_passed == 10


async def test_different_gates_on_one_unit_are_both_counted(world):
    world["journal"] = [
        event(1, "gate_result", unit_id="unit_pricing", attempt=1,
              gate="differential", status="passed", passed=4),
        event(2, "gate_result", unit_id="unit_pricing", attempt=1,
              gate="existing_tests", status="passed", passed=12),
    ]
    report = await build_report(make_run())
    assert report.tests_passed == 16


async def test_the_final_verification_gate_is_counted(world):
    """Final verification has no unit_id; it must not be lumped in with a unit."""
    world["journal"] = [
        event(1, "gate_result", unit_id="unit_pricing", attempt=1,
              gate="differential", status="passed", passed=4),
        event(2, "gate_result", gate="final_integration", status="passed", passed=12),
    ]
    report = await build_report(make_run())
    assert report.tests_passed == 16


# --------------------------------------------------------------------------
# caveats and limitations
# --------------------------------------------------------------------------

async def test_the_report_always_says_it_is_not_a_proof(world):
    report = await build_report(make_run())
    joined = " ".join(report.coverage_caveats).lower()
    assert "not a proof of equivalence" in joined


async def test_skipped_units_are_named_in_the_caveats(world):
    units = [
        UnitRunState(unit_id="unit_pricing", module_ids=["module_pricing"],
                     status="blocked"),
        UnitRunState(unit_id="unit_orders", module_ids=["module_orders"],
                     status="skipped"),
    ]
    report = await build_report(make_run(units, state="completed_with_warnings"))
    joined = " ".join(report.coverage_caveats)
    assert "unit_orders" in joined
    assert "prerequisite" in joined.lower()


async def test_exposed_held_out_cases_are_declared(world):
    """A revealed case is no longer an independent check, and the report must say so."""
    world["journal"] = [
        event(1, "counterexample_recorded", unit_id="unit_pricing",
              counterexample_id="cex_1", case_id="hcase_001",
              visibility="held_out", exposed_to_model=True),
    ]
    report = await build_report(make_run())
    joined = " ".join(report.coverage_caveats).lower()
    assert "held-out" in joined
    assert "no longer" in joined or "independent" in joined


async def test_a_disabled_runner_means_nothing_was_verified_by_execution(world):
    report = await build_report(make_run(runner_mode="disabled"))
    joined = " ".join(report.limitations).lower()
    assert "disabled" in joined
    assert "nothing in this report was verified by execution" in joined


async def test_inconclusive_checks_are_declared_as_not_passing(world):
    report = await build_report(make_run(inconclusive=3))
    joined = " ".join(report.coverage_caveats).lower()
    assert "inconclusive is not a pass" in joined


async def test_the_sandbox_limits_are_always_stated(world):
    report = await build_report(make_run())
    joined = " ".join(report.limitations).lower()
    assert "trusted fixtures" in joined
    assert "not a hardened" in joined


async def test_errors_during_the_run_are_surfaced(world):
    world["journal"] = [
        event(1, "error", code="AGENT_UNAVAILABLE", message="not wired up"),
    ]
    report = await build_report(make_run())
    assert any("AGENT_UNAVAILABLE" in limitation for limitation in report.limitations)


# --------------------------------------------------------------------------
# evidence, not model text
# --------------------------------------------------------------------------

async def test_the_verdict_mirrors_the_run_state(world):
    report = await build_report(make_run(state="completed_with_warnings"))
    assert report.verdict == "completed_with_warnings"


async def test_a_cancelled_run_reports_cancelled_not_failed(world):
    report = await build_report(make_run(state="cancelled"))
    assert report.verdict == "cancelled"


async def test_the_baseline_hash_comes_from_the_journal(world):
    world["journal"] = [
        event(1, "baseline_captured", snapshot_hash="sha256:abc", cases_captured=26),
    ]
    report = await build_report(make_run())
    assert report.baseline_snapshot_hash == "sha256:abc"


async def test_a_counterexample_counts_as_resolved_only_if_its_unit_passed(world):
    world["journal"] = [
        event(1, "counterexample_recorded", unit_id="unit_pricing",
              counterexample_id="cex_1", case_id="hcase_001"),
        event(2, "counterexample_recorded", unit_id="unit_orders",
              counterexample_id="cex_2", case_id="hcase_002"),
    ]
    units = [
        UnitRunState(unit_id="unit_pricing", module_ids=["module_pricing"],
                     status="passed"),
        UnitRunState(unit_id="unit_orders", module_ids=["module_orders"],
                     status="blocked"),
    ]
    report = await build_report(make_run(units, state="completed_with_warnings"))

    assert report.counterexamples_found == 2
    assert report.counterexamples_resolved == 1


async def test_no_narrative_when_the_agent_is_not_wired_up(world):
    """An absent narrative is None, never invented prose."""
    report = await build_report(make_run())
    assert report.narrative is None


async def test_the_report_is_itself_a_downloadable_artefact(world):
    report = await build_report(make_run())
    assert report.artefact_ids
    kinds = {a["kind"] for a in world["artefacts"]}
    assert "report" in kinds


# --------------------------------------------------------------------------
# second review round
# --------------------------------------------------------------------------

async def test_final_verification_id_is_the_final_one_not_the_last_unit(world):
    """Matching on event type alone returned whichever unit was verified last,
    which is a different artefact entirely."""
    world["journal"] = [
        event(1, "verification_passed", unit_id="unit_pricing", attempt=1,
              verification_id="ver_unit_pricing"),
        event(2, "verification_passed", unit_id="unit_orders", attempt=1,
              verification_id="ver_unit_orders"),
        event(3, "verification_passed", verification_id="ver_final", scope="final"),
    ]
    report = await build_report(make_run())
    assert report.final_verification_id == "ver_final"


async def test_no_final_verification_id_when_only_units_were_verified(world):
    world["journal"] = [
        event(1, "verification_passed", unit_id="unit_pricing", attempt=1,
              verification_id="ver_unit_pricing"),
    ]
    report = await build_report(make_run())
    assert report.final_verification_id is None


async def test_the_proof_caveat_counts_verified_units_not_planned_ones(world):
    """A run where four of six units were skipped must not claim agreement
    'across 6 units'."""
    units = [
        UnitRunState(unit_id="unit_pricing", module_ids=["module_pricing"],
                     status="passed"),
        UnitRunState(unit_id="unit_orders", module_ids=["module_orders"],
                     status="blocked"),
        UnitRunState(unit_id="unit_main", module_ids=["module_main"],
                     status="skipped"),
    ]
    report = await build_report(make_run(units, state="completed_with_warnings"))

    caveat = report.coverage_caveats[0]
    assert "1 of 3" in caveat, caveat
    assert "not a proof of equivalence" in caveat


async def test_the_same_counterexample_across_attempts_is_counted_once(world):
    """A unit failing the same case every attempt re-emits it; counting raw events
    inflates the figure the same way summing every attempt's gates did."""
    world["journal"] = [
        event(1, "counterexample_recorded", unit_id="unit_pricing", attempt=1,
              counterexample_id="cex_1", case_id="hcase_001"),
        event(2, "counterexample_recorded", unit_id="unit_pricing", attempt=2,
              counterexample_id="cex_1", case_id="hcase_001"),
        event(3, "counterexample_recorded", unit_id="unit_pricing", attempt=3,
              counterexample_id="cex_1", case_id="hcase_001"),
    ]
    report = await build_report(make_run())
    assert report.counterexamples_found == 1


async def test_distinct_counterexamples_are_all_counted(world):
    world["journal"] = [
        event(1, "counterexample_recorded", unit_id="unit_pricing", attempt=1,
              counterexample_id="cex_1", case_id="hcase_001"),
        event(2, "counterexample_recorded", unit_id="unit_pricing", attempt=2,
              counterexample_id="cex_2", case_id="hcase_002"),
    ]
    report = await build_report(make_run())
    assert report.counterexamples_found == 2


async def test_final_verification_counterexamples_are_counted(world):
    """These were never journalled at all, so the report undercounted."""
    world["journal"] = [
        event(1, "counterexample_recorded", unit_id="unit_pricing", attempt=1,
              counterexample_id="cex_1", case_id="hcase_001"),
        event(2, "counterexample_recorded", counterexample_id="cex_final",
              scope="final"),
    ]
    report = await build_report(make_run())
    assert report.counterexamples_found == 2


async def test_a_final_verification_counterexample_is_never_counted_resolved(world):
    """Nothing runs after the final check, so it cannot have been resolved."""
    world["journal"] = [
        event(1, "counterexample_recorded", counterexample_id="cex_final",
              scope="final"),
    ]
    report = await build_report(make_run())
    assert report.counterexamples_found == 1
    assert report.counterexamples_resolved == 0


@pytest.mark.parametrize("changed", [False, True], ids=["unchanged", "two-revisions"])
async def test_report_and_patch_compare_the_final_tree_to_the_original_snapshot(
    world, monkeypatch, tmp_path, changed,
):
    """#44: use the real workspace diff, not a precomputed report-side fake."""
    import app.ingestion as ingestion
    import app.verification as verification
    from app.models.contracts import FilePatch, PatchProposal
    from app.sandbox import workspaces

    run = make_run()
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    originals = {
        "pricing.py": "TAX = 1\n",
        "customers.py": "NAME = 'legacy'\n",
        "README.md": "Unchanged documentation\n",
        "tests/test_original.py": "def test_original(): pass\n",
    }
    for relative, content in originals.items():
        path = snapshot / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    async def repository_for_run(run_id):
        assert run_id == run.run_id
        return run.repository_id

    def snapshot_path(repo_id):
        assert repo_id == run.repository_id
        return snapshot

    monkeypatch.setattr(ingestion, "snapshot_path", snapshot_path)
    monkeypatch.setattr(verification, "repository_for_run", repository_for_run)
    monkeypatch.setattr(workspaces, "workspace_root", lambda: tmp_path / "workspaces")
    monkeypatch.setattr(ports_module, "diff", workspaces.diff)
    monkeypatch.setattr(ports_module, "workspace_path", workspaces.workspace_path)

    first = await workspaces.create_workspace(run.run_id, run.repository_id)
    if changed:
        ack = await workspaces.apply_patch(first, PatchProposal(
            unit_id="unit_pricing", files=[FilePatch(path="pricing.py", new_content="TAX = 2\n")],
            summary="Update pricing", rationale="test",
        ), ["pricing.py"])
        assert ack.accepted
    await workspaces.accept_workspace(first)
    final = await workspaces.create_workspace(run.run_id, run.repository_id, first.revision_id)
    if changed:
        ack = await workspaces.apply_patch(final, PatchProposal(
            unit_id="unit_customers",
            files=[FilePatch(path="customers.py", new_content="NAME = 'modern'\n")],
            summary="Update customers", rationale="test",
        ), ["customers.py"])
        assert ack.accepted
    await workspaces.accept_workspace(final)
    world["journal"] = [
        event(1, "snapshot_accepted", revision=first.revision_id),
        event(2, "snapshot_accepted", revision=final.revision_id),
    ]

    report = await build_report(run)
    patch = next(a for a in world["artefacts"] if a["kind"] == "patch")
    expected = ["customers.py", "pricing.py"] if changed else []
    assert report.changed_files == patch["changed_files"] == expected
    assert patch["base_revision"] == "rev_baseline"
    assert patch["revision"] == final.revision_id
    content = Path(patch["path"]).read_text(encoding="utf-8")
    if changed:
        assert "-TAX = 1\n+TAX = 2\n" in content
        assert "-NAME = 'legacy'\n+NAME = 'modern'\n" in content
        assert "README.md" not in content and "tests/test_original.py" not in content
    else:
        assert content == ""
    assert (await artefacts_module.verify_patch_matches_revision(
        run.run_id, final.revision_id,
    ))["matches"] is True
    for relative, original in originals.items():
        assert (snapshot / relative).read_text(encoding="utf-8") == original
