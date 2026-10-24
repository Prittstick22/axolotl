# Axolotl — Pre-Kickoff Prep (Thu night / Fri before 17:00)

**Goal:** be ready to have the app skeleton booting by **Fri 18:00**.

## Part 1 — Tonight: machine and tools (everyone)

Install and confirm versions; post them in the team chat.

- [ ] Git, GitHub access, SSH key working (`ssh -T git@github.com`)
- [ ] Python **3.12** + `uv` (or pip/venv) → `python3.12 --version`
- [ ] Node **20+** and npm/pnpm → `node -v`
- [ ] Docker Desktop running → `docker run --rm hello-world`
- [ ] Pull the base image now (slow on event Wi-Fi): `docker pull python:3.12-slim`
- [ ] Editor + your coding agent set up and logged in
- [ ] Optional: `mongosh` and MongoDB Compass

## Part 2 — Tonight: accounts and access (infra person)

- [ ] **MongoDB Atlas:** project + cluster created (or confirm event-provided credits/cluster). Note the **cluster version** (native hybrid search needs 8.0+). Create a DB user and allow your IPs. Test the connection with `mongosh "<uri>"`. Don't create collections or indexes yet.
- [ ] Confirm **Atlas Search + Vector Search** are available on that tier (check the Atlas UI "Search" tab).
- [ ] **Gemini:** API key created and tested with a one-line request from a throwaway shell (any model you currently have access to). Note what you'll need confirmed at kickoff: `gemini-4-argon` access, quota, Interactions API tool-call format.
- [ ] Decide where secrets live (password manager / shared vault). **Never** in the repo or chat.
- [ ] Confirm the **demo laptop** can run Docker + backend + frontend together.

## Part 3 — Tonight: reading (infra person)

Skim so you aren't learning during the build:
- FastAPI: app factory, `APIRouter`, CORS middleware, lifespan events
- PyMongo async API (`AsyncMongoClient`, `pymongo>=4.9`): client setup and `ping`
- Pydantic v2 + `pydantic-settings` for `.env` config
- Vite React-TS template and TanStack Query basics
- `SHARED_CONTRACTS.md` §2 (layout), §4 (models), §6 (API), §10 (env) — the skeleton must match these exactly

---

## Part 4 — Fri 17:00–18:00 runbook (Dev 2 executes, others help)

1. **17:00** Create the GitHub repo (or clear the existing empty one), clone it, and create the folder layout from SHARED_CONTRACTS §2.
2. **Backend:** create `backend/pyproject.toml` with FastAPI, uvicorn, pymongo (>=4.9), pydantic, pydantic-settings, networkx, google-genai (>=2.3.0), pytest, httpx. Then install.
3. `app/config.py`: settings from env vars listed in SHARED_CONTRACTS §10.
4. `app/models/contracts.py`: type in the models from SHARED_CONTRACTS §4.
5. `app/main.py`: app, `/api/v1` prefix, CORS for `FRONTEND_ORIGIN`, error envelope, and an empty router file per owner (`repositories`, `search`, `plans_runs`, `targets`, `system`).
6. `GET /api/v1/system/status` does a real Mongo `ping`; runner and Gemini show `unavailable` until their owners wire them.
7. Two smoke tests: the app boots, and `/system/status` returns JSON.
8. **Frontend:** Vite React-TS app in `frontend/`, add `@xyflow/react`, `@monaco-editor/react`, `@tanstack/react-query`. Create one page that calls `/system/status` and shows the chips.
9. `.env.example`, `.gitignore` (venvs, `node_modules`, `.env`, `.axolotl_workspaces/`), and a README with run commands.
10. **~17:50** Push to `main`. Everyone clones, creates a `devN/<topic>` branch, and confirms it boots. **Gate 18:00.**

Add the planning MD files (`SHARED_CONTRACTS.md`, `TEAM_SPLIT.md`, briefs) into `docs/planning/` in the first commit, if organisers are OK with that.

## Part 5 — Things to ask organisers at 17:00

- One entry across DeepMind + MongoDB + Agents That Ship?
- Gemini 4 Argon access/quotas; event Atlas credits/cluster version.
