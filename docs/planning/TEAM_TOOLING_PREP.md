# Axolotl — Team Tooling & Prep (everyone, before Fri 17:00)

Do this tonight so Friday 17:00 is spent building, not installing. Post a ✅ (and your versions) in the team chat when done.

---

## 1. Everyone

### Core tools
| Tool | Version | Check |
|---|---|---|
| Git | any recent | `git --version` |
| GitHub account + SSH key | — | `ssh -T git@github.com` |
| Python | **3.12** | `python3.12 --version` |
| uv (Python env/deps) | latest | `uv --version` (install: `pip install uv` or the official installer) |
| Node.js | **20 LTS+** | `node -v` |
| npm (or pnpm) | bundled | `npm -v` |
| Docker Desktop | running | `docker run --rm hello-world` |
| VS Code / Cursor / your editor | — | Python, Pylance, ESLint, Prettier extensions |
| Your coding agent | logged in | can read local files + run terminal |

### Pull now (slow on event Wi-Fi)
```bash
docker pull python:3.12-slim
```

### Read once
- `Axolotl_Product_Requirements_and_Implementation_Handoff.md` (the Spec): §0, §2, §6.2, §7.1, §11.2
- `SHARED_CONTRACTS.md` — fully
- `TEAM_SPLIT.md` and **your own** `AGENT_DEVn_*.md`

### Agree as a team
- Team chat channel for contract changes and gate check-ins
- Secrets vault (Bitwarden/1Password shared item). **Never** paste keys in chat or commit `.env`
- Branch naming: `devN/<topic>`; PR into `main`; `main` must always boot

---

## 2. Role-specific prep

### Dev 1 — Argon Agent
- [ ] Gemini API key in the vault; test one request from a scratch shell
- [ ] Read: Gemini Interactions API, function calling, background execution (links in Spec §17)
- [ ] List questions for the DeepMind workshop: `gemini-4-argon` access, quotas, exact tool-call request/response shape, `thinking_level` values
- [ ] Draft (on paper/notes) the system prompt themes from Spec §6.5

### Dev 2 — Orchestrator & Integration Lead
- [ ] Owns the scaffold — see `APP_SCAFFOLD_SETUP.md`
- [ ] Read: FastAPI routers/lifespan/CORS, Pydantic v2, `pydantic-settings`, NetworkX SCC (`strongly_connected_components`, `condensation`)
- [ ] Prepare the organiser question list (Spec §16 "Unresolved assumptions")

### Dev 3 — Graph & Atlas
- [ ] MongoDB Atlas project + cluster (or event-provided one); DB user; network access for team IPs
- [ ] Note **cluster version** (native `$rankFusion` needs 8.0+) and that Atlas Search + Vector Search are enabled
- [ ] Connection string in the vault; test with `mongosh "<uri>"`
- [ ] Decide embedding provider to confirm at the MongoDB workshop (Gemini embeddings / Voyage AI / Automated Embedding) and its dimensions
- [ ] Install `mongosh` and optionally MongoDB Compass
- [ ] Read: Python `ast` module, PyMongo's async API, Atlas `$search`, `$vectorSearch`, `$rankFusion`

### Dev 4 — Verifier
- [ ] Docker confirmed on the **demo laptop**, `python:3.12-slim` pulled
- [ ] Read: `docker run` flags `--network none`, `--read-only`, `--pids-limit`, `--cap-drop`, `--security-opt no-new-privileges`, `--memory`, `--tmpfs`
- [ ] Read: `decimal` module rounding modes (`ROUND_HALF_UP` vs `ROUND_HALF_EVEN`), `difflib`, `compileall`
- [ ] Sketch on paper the legacy fixture's pricing rule and a list of rounding-edge inputs

### Dev 5 — Frontend
- [ ] Read: React Flow (`@xyflow/react`) quick start + custom nodes, dagre/elk layout, Monaco `DiffEditor`, TanStack Query polling (`refetchInterval`)
- [ ] Wireframe the 8 screens in Spec §2.2 (Figma or paper)
- [ ] Pick status icons/colours (SHARED_CONTRACTS `UnitStatus`) with AA contrast
- [ ] Install screen recorder for Sunday videos (OBS / QuickTime) and test audio

---

## 3. Demo laptop checklist (one machine, decide tonight)
- [ ] Docker, Python 3.12, Node 20 all installed
- [ ] Can run backend (:8000), frontend (:5173) and Docker containers at once
- [ ] Charger, HDMI/USB-C adapter, hotspot backup
- [ ] Do Not Disturb / notifications off for demo

## 4. Fri 14:30–17:00
- DeepMind workshop: Dev 1 (+ Dev 2)
- MongoDB workshop: Dev 3 (+ Dev 4)
- Dev 5: finish wireframes; Dev 2: final scaffold check
- 16:50 everyone at the table, repo cloned, ready
