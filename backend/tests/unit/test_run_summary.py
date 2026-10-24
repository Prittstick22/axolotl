"""Migration summary (#35). Offline: the report, journal, plan and diff are faked."""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.errors import AppError
from app.models.contracts import RunReport, UnitRunState
from app.orchestration import events, ports, store
from app.reports import summary as summary_module
from app.reports.summary import count_lines, run_summary

pytestmark = pytest.mark.anyio

RUN = "run_000000000001"
DIFF = (
    "--- a/game.py\n+++ b/game.py\n@@ -1,3 +1,3 @@\n"
    "-x = raw_input()\n+x = input()\n+print(x)\n context\n"
)


def event(event_type, unit_id=None, **payload):
    return SimpleNamespace(event_type=event_type, unit_id=unit_id, payload=payload)


def gate(unit_id, name, passed=0, failed=0, status="passed"):
    return event("gate_result", unit_id, gate=name, status=status, passed=passed,
                 failed=failed, inconclusive=0)


def make_report(**overrides):
    fields = {
        "run_id": RUN, "verdict": "completed_with_warnings",
        "units": [
            UnitRunState(unit_id="unit_game", module_ids=["module_game"], status="passed",
                         attempts=2, accepted_revision="rev_2"),
            UnitRunState(unit_id="unit_board", module_ids=["module_board"],
                         status="blocked", attempts=3),
        ],
        "final_verification_id": "ver_final", "changed_files": ["game.py"],
        "tests_passed": 15, "tests_failed": 2, "tests_inconclusive": 0,
        "counterexamples_found": 2, "counterexamples_resolved": 1,
        "baseline_snapshot_hash": "sha256:abc", "final_artefact_hash": None,
        "coverage_caveats": ["Agreement shown only for 15 tested case(s)."],
        "limitations": ["unit_board was blocked."], "narrative": None,
        "artefact_ids": [], "generated_at": datetime.now(UTC),
    }
    return RunReport(**{**fields, **overrides})


@pytest.fixture
def world(monkeypatch, tmp_path):
    state = {
        "report": make_report(),
        "journal": [
            event("patch_proposed", "unit_game", summary="First try"),
            gate("unit_game", "differential", passed=8, failed=2, status="failed"),
            event("counterexample_recorded", "unit_game", counterexample_id="cx_1"),
            event("patch_proposed", "unit_game", summary="Use input()"),
            gate("unit_game", "differential", passed=10),  # the repair: last one counts
            gate("unit_game", "compile", status="passed"),
            event("snapshot_accepted", "unit_game", revision="rev_2"),
            event("patch_proposed", "unit_board", summary="Board attempt"),
            gate("unit_board", "differential", passed=3, failed=2, status="failed"),
            event("counterexample_recorded", "unit_board", counterexample_id="cx_2"),
            gate(None, "final_integration", passed=2),
        ],
        "diff": [{"path": "game.py", "unified_diff": DIFF}],
        "trees": True,
    }
    run = SimpleNamespace(run_id=RUN, repository_id="repo_x", plan_id="plan_x")
    plan = SimpleNamespace(units=[
        SimpleNamespace(unit_id="unit_game", file_paths=["game.py"]),
        SimpleNamespace(unit_id="unit_board", file_paths=["board.py"]),
    ])

    async def get_run(run_id):
        return run

    async def get_plan(plan_id):
        return plan

    async def get_report(run_id):
        return state["report"]

    async def list_all_events(run_id):
        return state["journal"]

    async def diff(run_id, base, revision, paths=None):
        return state["diff"]

    async def static(run_id, repository_id, revision, warnings):
        if not state["trees"]:
            warnings.append("trees not on this machine")
            return None, None
        return ({"before": "2.7", "after": ">=3.10"},
                {"modules_changed": 1, "imports_added": 0, "imports_removed": 1,
                 "stdlib_renamed": [], "builtins_replaced": ["raw_input -> input"],
                 "methods_replaced": [], "packages": {"added": [], "removed": [],
                                                      "kept": ["setuptools"]},
                 "warnings": []})

    from app.sandbox import workspaces

    monkeypatch.setattr(store, "get_run", get_run)
    monkeypatch.setattr(store, "get_plan", get_plan)
    monkeypatch.setattr(summary_module, "get_report", get_report)
    monkeypatch.setattr(events, "list_all_events", list_all_events)
    monkeypatch.setattr(ports, "diff", diff)
    monkeypatch.setattr(summary_module, "_static", static)
    (tmp_path / "rev_2").mkdir()
    monkeypatch.setattr(workspaces, "revision_path",
                        lambda run_id, rev: tmp_path / rev if state["trees"] else tmp_path / "gone")
    return state


def test_lines_are_counted_without_the_file_headers():
    assert count_lines(DIFF) == (2, 1)


async def test_tests_run_come_from_the_last_gate_result_per_gate(world):
    result = await run_summary(RUN)
    game, board = result["units"]

    assert game["tests"]["passed"] == 10 and game["tests"]["failed"] == 0  # not 18
    assert game["tests"]["gates"] == {"differential": "passed", "compile": "passed"}
    assert board["tests"]["failed"] == 2
    assert result["totals"]["final_integration"]["passed"] == 2
    total = game["tests"]["passed"] + board["tests"]["passed"] + 2
    assert total == result["totals"]["tests_passed"]


async def test_counterexamples_are_resolved_only_for_a_unit_that_passed(world):
    game, board = (await run_summary(RUN))["units"]
    assert game["counterexamples"] == {"found": 1, "resolved": 1}
    assert board["counterexamples"] == {"found": 1, "resolved": 0}


async def test_argon_summary_is_the_accepted_patch_and_labelled(world):
    game, board = (await run_summary(RUN))["units"]
    assert game["argon_summary"] == {"text": "Use input()", "basis": "model_written",
                                     "accepted": True}
    assert board["argon_summary"]["accepted"] is False


async def test_totals_and_lines_per_unit(world):
    result = await run_summary(RUN)
    game, board = result["units"]
    assert game["lines"] == {"added": 2, "removed": 1}
    assert board["lines"] == {"added": 0, "removed": 0}
    totals = result["totals"]
    assert (totals["verified"], totals["blocked"], totals["units"]) == (1, 1, 2)
    assert (totals["lines_added"], totals["lines_removed"]) == (2, 1)
    assert totals["counterexamples_found"] == 2 and totals["files_changed"] == 1
    assert result["python"] == {"before": "2.7", "after": ">=3.10"}


async def test_another_machine_still_gets_everything_from_atlas(world):
    world["trees"] = False
    result = await run_summary(RUN)
    assert result["units"][0]["lines"] is None and result["totals"]["lines_added"] is None
    assert result["python"] is None and result["dependencies"] is None
    assert result["totals"]["tests_passed"] == 15  # the report still answers
    assert len(result["warnings"]) == 2
    assert "Not available on this machine" in result["markdown"]


async def test_no_report_yet_is_a_404(world):
    world["report"] = None
    with pytest.raises(AppError) as caught:
        await run_summary(RUN)
    assert (caught.value.status_code, caught.value.code) == (404, "REPORT_NOT_READY")


async def test_the_markdown_always_shows_the_caveats_and_labels_argon(world):
    markdown = (await run_summary(RUN))["markdown"]
    assert "## Caveats" in markdown
    assert "Agreement shown only for 15 tested case(s)." in markdown
    assert "unit_board was blocked." in markdown
    assert "model-written, not verified" in markdown
    assert "Board attempt _(not accepted)_" in markdown
    assert "`raw_input -> input`" in markdown


def test_the_endpoint_is_registered():
    from app.main import create_app

    assert "get" in create_app().openapi()["paths"]["/api/v1/runs/{run_id}/summary"]
