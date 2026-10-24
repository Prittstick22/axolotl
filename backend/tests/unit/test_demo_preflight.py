"""Readiness must not be inferred from green default-runner status alone."""

import runpy
from pathlib import Path
from urllib.error import URLError

import pytest

inspect_demo = runpy.run_path(str(Path(__file__).resolve().parents[3]
                                / "demo/check_readiness.py"))["inspect_demo"]


@pytest.fixture
def responses():
    return {
        "/system/status": {"mongodb": "ok", "search_indexes": {"keyword": "ok", "vector": "ok"},
                           "runner": {"mode": "trusted_docker", "available": True},
                           "gemini": {"model": "test-model", "reachable": True}},
        "/migration-targets": [{"target_id": "python2_to_3", "supported": True,
                                "target_python": "3.14"}],
        "/repositories/repo": {"repository_id": "repo", "executable": True,
                               "demo_fixture_id": "chess", "analysis_status": "ok"},
        "/repositories/repo/index": {"state": "ready", "embedded": 24, "total": 24},
        "/runs/run": {"run_id": "run", "repository_id": "repo", "state": "completed",
                      "runner_mode": "trusted_docker", "plan_id": "plan"},
        "/plans/plan": {"plan_id": "plan", "repository_id": "repo", "target_id": "python2_to_3",
                        "status": "approved", "approved_at": "2026-10-24T12:00:00Z"},
        "/runs/run/report": {"run_id": "run", "verdict": "completed", "final_verification_id": "v",
                             "final_artefact_hash": "sha256:patch", "artefact_ids": ["patch"]},
        "/runs/run/artefacts": [{"kind": "patch", "sha256": "sha256:patch",
                                "artefact_id": "patch"}],
    }


def test_consistent_evidence_passes_automated_checks_but_keeps_manual_checks(responses):
    result = inspect_demo(responses.__getitem__, "repo", "run")
    assert result["automated_checks_passed"] is True
    assert len(result["manual_checks_still_required"]) == 4


@pytest.mark.parametrize("path,field,value", [
    ("/repositories/repo", "executable", False),
    ("/repositories/repo", "demo_fixture_id", "legacy_orders"),
    ("/repositories/repo/index", "embedded", 23),
    ("/runs/run", "state", "completed_with_warnings"),
    ("/runs/run", "repository_id", "other"),
    ("/plans/plan", "target_id", "modern_python3"),
    ("/plans/plan", "plan_id", "other"),
    ("/plans/plan", "approved_at", None),
    ("/runs/run/report", "final_verification_id", None),
    ("/runs/run/report", "final_artefact_hash", "wrong"),
])
def test_incomplete_or_unrelated_evidence_fails(responses, path, field, value):
    responses[path][field] = value
    assert inspect_demo(responses.__getitem__, "repo", "run")["automated_checks_passed"] is False


def test_a_later_plan_does_not_invalidate_an_earlier_approved_run(responses):
    responses["/plans/plan"]["status"] = "superseded"
    assert inspect_demo(responses.__getitem__, "repo", "run")["automated_checks_passed"] is True


def test_healthy_services_without_a_run_are_not_recording_evidence(responses):
    result = inspect_demo(responses.__getitem__)
    assert result["automated_checks_passed"] is False


def test_network_error_is_reported_without_leaking_diagnostics():
    def unavailable(_path):
        raise URLError("secret://example")

    result = inspect_demo(unavailable, "repo", "run")
    assert result["automated_checks_passed"] is False
    assert "secret" not in str(result)
