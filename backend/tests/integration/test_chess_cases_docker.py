"""Run the chess oracle in actual 2.7/3.14 containers; never import chess on the host."""

import json
import os
import shutil
from pathlib import Path

import pytest

from app.config import get_settings
from app.sandbox import runner
from app.verification import comparison
from app.verification.cases import collect_cases, write_cases_file

pytestmark = pytest.mark.docker
ROOT = Path(__file__).resolve().parents[3]
CHESS = ROOT / "demo/chess"
IMAGES = ("axolotl-chess-cases:py27", "axolotl-chess-cases:py314")


@pytest.fixture
async def chess_runner(monkeypatch):
    settings = get_settings()
    for image in IMAGES:
        monkeypatch.setattr(settings, "runner_image", image)
        status = await runner.runner_status()
        if not status["available"]:
            detail = f"chess test image {image} unavailable: {status.get('detail')}"
            if os.environ.get("AXOLOTL_REQUIRE_CHESS_IMAGES") == "1":
                pytest.fail(detail)
            pytest.skip(detail)

    async def run(image, code, cases):
        monkeypatch.setattr(settings, "runner_image", image)
        raw, execution = await runner.run_behaviour_cases(code, cases)
        assert execution.exit_code == 0, execution.log_excerpt
        assert execution.policy_clean, execution.log_excerpt
        expected = {c["case_id"] for c in json.loads(cases.read_text())}
        assert len(raw) == len(expected)
        assert {c["case_id"] for c in raw} == expected
        assert all(c["status"] == "ok" for c in raw), raw
        return {c["case_id"]: comparison.outcome_from_harness(c, c["case_id"]) for c in raw}

    return run


@pytest.fixture
def chess_cases(tmp_path):
    cases = collect_cases("repo_chess", CHESS, ROOT, fixture_id="chess")
    path = write_cases_file([c.case_id for c in cases], CHESS, ROOT, tmp_path / "cases.json",
                            fixture_id="chess")
    return cases, path


async def test_chess_sessions_match_between_python27_and_python314(chess_runner, chess_cases):
    cases, path = chess_cases
    baseline = await chess_runner(IMAGES[0], CHESS, path)
    candidate = await chess_runner(IMAGES[1], CHESS, path)
    results = [comparison.compare(
        c.case_id, c.visibility, baseline[c.case_id], candidate[c.case_id],
    ) for c in cases]
    assert comparison.summarise(results) == {"match": 24, "mismatch": 0, "inconclusive": 0}

    def value(name):
        return baseline["chess_" + name].value

    initial = value("visible_render")["board"]
    assert initial == ["rnbqkbnr", "pppppppp", "........", "........",
                       "........", "........", "PPPPPPPP", "RNBQKBNR"]
    assert value("visible_invalid")["board"] == initial
    invalid = value("visible_invalid")["events"]
    assert sum(e["text"] == "Illegal move. Type moves or help." for e in invalid) == 3
    turns = value("visible_turns")
    assert (turns["turn"], turns["fullmove"], turns["halfmove"]) == ("b", 2, 1)
    assert [e["text"] for e in turns["events"] if e["kind"] == "prompt"] == [
        "White to move > ", "Black to move > ", "White to move > ", "Black to move > ",
    ]
    assert any("a b c d e f g h" in e["text"] for e in turns["events"])
    assert value("visible_undo")["board"] == initial
    assert value("visible_mate")["status"] == "checkmate"
    assert value("visible_mate")["winner"] == "b"
    assert value("hidden_white_mate")["winner"] == "w"
    assert value("visible_castle")["board"][7] == "R....RK."
    assert value("hidden_black_castle")["board"][0] == "..kr...r"
    assert value("hidden_castle_check")["board"][7] == "....K..R"
    assert value("visible_en_passant")["board"][3][3] == "."
    assert value("visible_en_passant")["board"][2][3] == "P"
    assert value("hidden_black_en_passant")["board"][4][3] == "."
    assert value("hidden_black_en_passant")["board"][5][3] == "p"
    assert value("hidden_expired_en_passant")["board"][3][3:5] == "pP"
    assert value("visible_promotion")["board"][0][0] == "Q"
    assert value("hidden_underpromotion")["board"][0][0] == "N"
    assert value("hidden_black_promotion")["board"][7][0] == "r"
    assert value("visible_stalemate")["status"] == "stalemate"
    assert value("hidden_white_stalemate")["status"] == "stalemate"
    assert value("visible_material")["status"] == "insufficient material"
    assert value("visible_repetition")["repetitions"] == 3
    assert value("visible_fifty_moves")["can_claim_draw"] is True
    assert value("hidden_fivefold")["status"] == "fivefold repetition"
    assert value("hidden_seventy_five_moves")["status"] == "75-move rule"


async def test_chess_cases_are_independent_of_execution_order(chess_runner, chess_cases, tmp_path):
    _, path = chess_cases
    baseline = await chess_runner(IMAGES[1], CHESS, path)
    reversed_path = tmp_path / "reversed.json"
    reversed_path.write_text(json.dumps(list(reversed(json.loads(path.read_text())))))
    repeated = await chess_runner(IMAGES[1], CHESS, reversed_path)
    # Repeatability concerns behaviour; optional runtime samples vary per run.
    assert baseline.keys() == repeated.keys()
    assert all(
        comparison.compare(case_id, "visible", baseline[case_id], repeated[case_id]).verdict
        == "match"
        for case_id in baseline
    )


async def test_hidden_cases_catch_a_promotion_regression(chess_runner, chess_cases, tmp_path):
    cases, path = chess_cases
    baseline = await chess_runner(IMAGES[0], CHESS, path)
    candidate_tree = tmp_path / "candidate"
    shutil.copytree(CHESS, candidate_tree)
    state_file = candidate_tree / "local_chess/game_state.py"
    original = state_file.read_text(encoding="utf-8")
    # Default promotion still works, but an explicit choice is now ignored.
    assert "PIECE_TYPES[move.promotion]" in original
    state_file.write_text(original.replace("PIECE_TYPES[move.promotion]", "PIECE_TYPES['q']"),
                          encoding="utf-8")
    candidate = await chess_runner(IMAGES[1], candidate_tree, path)
    results = {c.case_id: comparison.compare(
        c.case_id, c.visibility, baseline[c.case_id], candidate[c.case_id],
    ) for c in cases}
    assert all(r.verdict == "match" for r in results.values() if r.visibility == "visible")
    assert results["chess_hidden_underpromotion"].verdict == "mismatch"
    assert results["chess_hidden_black_promotion"].verdict == "mismatch"
