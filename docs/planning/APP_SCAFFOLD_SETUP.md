# Axolotl — App Scaffold & Infra Setup

**For:** Dev 2 (integration lead) or whoever builds the scaffold, and their coding agent.
**Read with:** `SHARED_CONTRACTS.md` (layout §2, models §4, API §6, env §10).

## Ground rules

- **Scaffold only, no functionality.** Structure, configs, dependency manifests, empty modules, type definitions and a booting app are in. Business logic is out: parsing, graph building, DB queries, model calls, sandbox runs, verification, real UI screens.
- Match `SHARED_CONTRACTS.md` names exactly, so the other four agents land code in the right places.
- No secrets anywhere in files. `.env` is gitignored.

### In / out cheat-sheet

| ✅ Scaffold | ❌ Functionality (by owner) |
|---|---|
| Folder layout + `__init__.py` files | AST parsing, import resolution |
| `pyproject.toml`, `package.json`, lint/format/test config | MongoDB queries, indexes, search pipelines |
| `config.py` reading env vars | Gemini calls, prompts, tools |
| `models/contracts.py` + `contracts.ts` (types only) | Scheduler, state machine, events |
| FastAPI app boots; empty routers mounted; `GET /health` returns `{"status":"ok"}` | `/system/status` with real checks |
| Vite React app boots with router + empty page shells | Graph, inspector, run dashboard UIs |
| `.env.example`, `.gitignore`, README run instructions | Demo fixture, runner Dockerfile, harness |
| Smoke tests (app imports, `/health` 200) | Feature tests |

---

## 1. Repository layout

Create exactly this (empty files where noted):

```text
axolotl/
├── README.md
├── .gitignore
├── .env.example
├── .editorconfig
├── docs/
│   ├── planning/            # SHARED_CONTRACTS.md, TEAM_SPLIT.md, briefs, Spec
│   ├── architecture.md      # heading only
│   ├── api-contracts.md     # heading only
│   ├── demo-script.md       # heading only
│   ├── mongodb-atlas.md     # heading only
│   ├── argon-agent.md       # heading only
│   └── safety-and-limitations.md  # heading only
├── backend/
│   ├── pyproject.toml
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py
│   │   ├── config.py
│   │   ├── errors.py
│   │   ├── models/{__init__.py, contracts.py}
│   │   ├── api/{__init__.py, repositories.py, search.py, plans_runs.py, targets.py, system.py}
│   │   ├── persistence/__init__.py
│   │   ├── ingestion/__init__.py
│   │   ├── analysis/__init__.py
│   │   ├── graph/__init__.py
│   │   ├── search/__init__.py
│   │   ├── agents/__init__.py
│   │   ├── orchestration/__init__.py
│   │   ├── reports/__init__.py
│   │   ├── sandbox/__init__.py
│   │   └── verification/__init__.py
│   └── tests/
│       ├── conftest.py
│       ├── unit/__init__.py
│       ├── integration/__init__.py
│       ├── security/__init__.py
│       └── test_smoke.py
├── frontend/                # Vite react-ts template
│   └── src/
│       ├── main.tsx
│       ├── App.tsx
│       ├── api/client.ts
│       ├── types/contracts.ts
│       ├── pages/           # empty shells
│       └── components/{DependencyGraph,ModuleInspector,MigrationTimeline,CodeDiff}/  # .gitkeep
├── runner/.gitkeep          # Dev 4 fills
└── demo/.gitkeep            # Dev 4 fills
```

Each package `__init__.py` gets a one-line docstring naming its owner, e.g. `"""Ingestion — owner: Dev 3. See SHARED_CONTRACTS §2."""`

---

## 2. Backend

### `pyproject.toml`
- Python `>=3.12`, managed with `uv`.
- Runtime deps: `fastapi`, `uvicorn[standard]`, `pydantic>=2`, `pydantic-settings`, `pymongo>=4.9`, `networkx`, `google-genai>=2.3.0`, `python-multipart`, `httpx`.
- Dev deps: `pytest`, `pytest-asyncio`, `ruff`, `mypy`.
- Config sections: ruff (line length 100), pytest (`asyncio_mode = "auto"`, a `live` marker for real API tests, `testpaths = ["tests"]`).
- Verify: `uv sync` succeeds.

### `app/config.py`
A `Settings` class (`pydantic-settings`) with every variable in SHARED_CONTRACTS §10, sensible defaults for non-secret values, secrets defaulting to empty. A cached `get_settings()`. No connection logic.

### `app/models/contracts.py`
Transcribe SHARED_CONTRACTS §4 verbatim. Types only, no methods. Verify: `python -c "import app.models.contracts"`.

### `app/errors.py`
An `AppError(code, message, status_code, details)` exception and a FastAPI exception handler that returns the envelope `{"error": {"code", "message", "details"}}`.

### `app/api/*.py`
Each file defines `router = APIRouter(tags=[...])` with **no routes**, plus a docstring naming the owner and listing the endpoints from SHARED_CONTRACTS §6 it will hold.

### `app/main.py`
- `create_app()` factory: settings, CORS restricted to `FRONTEND_ORIGIN`, the error handler, all five routers mounted under `/api/v1`.
- A lifespan hook with placeholder comments where Dev 3 will connect Mongo and create indexes, and Dev 2 will mark interrupted runs on startup.
- `GET /api/v1/health` → `{"status": "ok"}` (liveness only, no dependency checks).
- `app = create_app()` for `uvicorn app.main:app`.

### Tests
`test_smoke.py`: the app imports, and `/api/v1/health` returns 200 through FastAPI's `TestClient`. Verify: `uv run pytest` is green and `uv run ruff check .` is clean.

---

## 3. Frontend

- Create with the Vite `react-ts` template in `frontend/`.
- Deps: `@xyflow/react`, `@monaco-editor/react`, `@tanstack/react-query`, `react-router-dom`, `dagre` (or `elkjs`). Dev deps: `vitest`, `@testing-library/react`, `jsdom`, ESLint + Prettier config.
- `src/types/contracts.ts`: TypeScript mirror of SHARED_CONTRACTS §4, same snake_case field names, string-literal unions for enums. Types only.
- `src/api/client.ts`: a typed `fetch` wrapper reading `import.meta.env.VITE_API_BASE` (default `http://localhost:8000/api/v1`) that parses the error envelope. No endpoint functions yet beyond `getHealth()`.
- `App.tsx`: React Router with routes for the 8 screens in Spec §2.2, each an empty page component showing its title; a QueryClientProvider; a small header that calls `getHealth()` and shows "API: ok / unreachable".
- `frontend/.env.example`: `VITE_API_BASE=http://localhost:8000/api/v1`.
- Verify: `npm run dev` shows the shell, and the header shows "API: ok" when the backend runs. `npm run build` and `npm run lint` pass.

---

## 4. Repo-level files

- `.gitignore`: `.env`, `.venv/`, `__pycache__/`, `*.pyc`, `.pytest_cache/`, `.ruff_cache/`, `.mypy_cache/`, `node_modules/`, `dist/`, `.axolotl_workspaces/`, `.DS_Store`.
- `.env.example`: copy of SHARED_CONTRACTS §10 with blank secrets.
- `.editorconfig`: 4 spaces for Python, 2 for TS/JSON/MD, LF line endings.
- `README.md`: one-paragraph product description from Spec §0, prerequisites, and run commands:
  ```bash
  # backend
  cd backend && uv sync && cp ../.env.example ../.env && uv run uvicorn app.main:app --reload --port 8000
  # frontend
  cd frontend && npm install && npm run dev
  # tests
  cd backend && uv run pytest
  ```
  Plus sections to fill later: Architecture, MongoDB Atlas features, Gemini usage, Safety & limitations, Known issues, Credits.
- Optional: a GitHub Actions workflow that runs backend `ruff` + `pytest` and frontend `lint` + `build` on PRs.

---

## 5. Final check before the first commit

- [ ] `uv sync && uv run pytest` green
- [ ] `uvicorn` boots and `/api/v1/health` → ok
- [ ] `npm run dev` shows the shell with "API: ok"
- [ ] `npm run build` passes
- [ ] No `.env`, keys or `node_modules` would be committed (`git status` after `git init`)
- [ ] No business logic anywhere (search for DB calls, model calls, `ast.parse`, `docker`)

## 6. First commit

1. `git init` (or clone the event repo), add the planning docs to `docs/planning/`, then make the first commit: `chore: project scaffold`.
2. Push to `main`, share the link, and everyone clones and boots both apps.
3. Dev 2 starts real work: `/system/status`, events, scheduler. Everyone else branches `devN/<topic>`.
