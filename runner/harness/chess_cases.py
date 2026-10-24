"""Protected chess session adapter, shared by Python 2.7 and Python 3 candidates.

Inputs live in the visible/held-out JSON suites. This helper stays in /harness,
outside the candidate tree; no expected values or hidden scenarios live here.
Only call inside the sandbox, through run_cases.py.
"""


def play_session(commands, fen=None):
    """Return ordered terminal events plus plain JSON game state, without object reprs."""
    from local_chess.cli import ConsoleUI
    from local_chess.game import Game
    from local_chess.game_state import GameState

    game = Game(GameState.from_fen(fen)) if fen else Game()
    pending = iter(commands)
    events = []

    def read(prompt):
        events.append({"kind": "prompt", "text": prompt})
        try:
            return next(pending)
        except StopIteration:
            raise EOFError

    def write(text):
        events.append({"kind": "output", "text": text})

    ConsoleUI(game=game, input_fn=read, output_fn=write).run()
    state = game.state
    winner = game.winner()
    return {
        "events": events,
        "board": state.board.rows(),
        "turn": state.turn.value,
        "castling_rights": state.castling_rights,
        "en_passant": list(state.en_passant) if state.en_passant is not None else None,
        "halfmove": state.halfmove,
        "fullmove": state.fullmove,
        "status": game.status().value,
        "winner": winner.value if winner is not None else None,
        "repetitions": game.repetitions(),
        "can_claim_draw": game.can_claim_draw(),
        "legal_moves": sorted(str(move) for move in game.legal_moves()),
    }
