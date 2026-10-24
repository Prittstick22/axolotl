# Demo inputs

> **Decision, 24 Oct 2026:** the demo is Will's chess game, migrated from Python 2.7 to Python 3.14.
> **Chess is the only demo; there is no backup.** `legacy_orders` stays in the repo only as the test
> fixture until chess replaces it ([#54](https://github.com/Prittstick22/axolotl/issues/54)). Chess can't be migrated yet:
> see [#30](https://github.com/Prittstick22/axolotl/issues/30) and [PROJECT_STATUS.md](../docs/PROJECT_STATUS.md).

- [`chess/`](chess/README.md): Will's terminal chess program, preserved unchanged.
  The refreshed source supports Python 2.7 and 3.14; the demo targets modernisation
  between those interpreters. Both runtime test suites pass. See the
  [integration notes](../docs/chess-demo.md) before using it as a migration fixture.
- [`legacy_orders/`](legacy_orders/README.md): the backend's test fixture, not a demo. It stays until chess replaces it in the tests
  ([#54](https://github.com/Prittstick22/axolotl/issues/54)).
- `challenge_cases/`: independent orders oracle, excluded from model context and
  source snapshots. It must not be reused as the chess oracle.
