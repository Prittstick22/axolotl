"""Fixture identity chooses both collected and executed hidden cases (#23/#29)."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import verification
from app.persistence import graphstore
from app.verification.cases import collect_cases, write_cases_file

ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize("fixture", ["legacy_orders", "chess"])
def test_collection_and_execution_use_the_same_fixture_suite(fixture, tmp_path):
    snapshot = ROOT / "demo" / fixture
    cases = collect_cases("repo_1", snapshot, ROOT, fixture_id=fixture)
    assert {c.visibility for c in cases} == {"visible", "held_out"}
    assert all(c.case_id.startswith("chess_") == (fixture == "chess") for c in cases)
    destination = write_cases_file(
        [c.case_id for c in cases], snapshot, ROOT, tmp_path / "cases.json", fixture_id=fixture,
    )
    payload = json.loads(destination.read_text())
    assert {c["case_id"] for c in payload} == {c.case_id for c in cases}
    for case in cases:
        raw = next(c for c in payload if c["case_id"] == case.case_id)
        assert (raw["function"], raw["args"], raw["kwargs"]) == (
            case.function, case.args, case.kwargs,
        )
    if fixture == "legacy_orders":
        assert any("steps" in raw for raw in payload)


@pytest.mark.parametrize("fixture_id", [None, "unknown"])
def test_no_builtin_hidden_cases_without_a_known_fixture(tmp_path, fixture_id):
    assert collect_cases("repo_upload", tmp_path, ROOT, fixture_id=fixture_id) == []
    with pytest.raises(ValueError, match="missing from this fixture"):
        write_cases_file(["hcase_001"], tmp_path, ROOT, tmp_path / "out.json",
                         fixture_id=fixture_id)


def test_chess_cannot_execute_an_orders_hidden_case(tmp_path):
    with pytest.raises(ValueError, match="missing from this fixture"):
        write_cases_file(["hcase_001"], ROOT / "demo/chess", ROOT, tmp_path / "cases.json",
                         fixture_id="chess")


def test_visible_and_hidden_case_id_collisions_are_rejected(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/cases.json").write_text(json.dumps([{
        "case_id": "chess_hidden_underpromotion", "function": "untrusted.f",
    }]))
    with pytest.raises(ValueError, match="duplicate"):
        collect_cases("repo_1", tmp_path, ROOT, fixture_id="chess")
    with pytest.raises(ValueError, match="duplicate"):
        write_cases_file(["chess_hidden_underpromotion"], tmp_path, ROOT,
                         tmp_path / "cases.json", fixture_id="chess")


def test_hidden_inputs_stay_outside_chess_and_have_distinct_inputs():
    cases = collect_cases("repo_chess", ROOT / "demo/chess", ROOT, fixture_id="chess")
    visible = [c for c in cases if c.visibility == "visible"]
    hidden = [c for c in cases if c.visibility == "held_out"]
    assert len(visible) == len(hidden) == 12
    assert not {c.case_hash for c in visible} & {c.case_hash for c in hidden}
    assert all(c.unit_id == "unit_local_chess__cli" for c in cases)
    assert all("expected" not in c.model_dump() for c in cases)
    assert not (ROOT / "demo/chess/challenge_cases").exists()
    assert not (ROOT / "demo/chess/chess_cases.py").exists()


@pytest.mark.parametrize("executable,expected", [(True, "chess"), (False, None)])
async def test_runtime_routing_uses_persisted_trust_not_candidate_source(
    monkeypatch, executable, expected,
):
    async def repository(repo_id):
        assert repo_id == "repo_chess"
        return SimpleNamespace(demo_fixture_id="chess", executable=executable)

    monkeypatch.setattr(graphstore, "get_repository", repository)
    assert await verification.fixture_for_repository("repo_chess") == expected
