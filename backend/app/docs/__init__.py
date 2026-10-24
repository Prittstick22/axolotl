"""Documentation for the legacy and the migrated code - owner: Dev 1. #39.

  GET  /repositories/{id}/docs   legacy docs: facts for every module, plus any purpose
                                 already generated. No model call.
  GET  /runs/{id}/docs           the same, plus the migrated side of each module: its
                                 facts, Argon's summary of the accepted patch, the unit's
                                 verdict and its dependency changes. No model call.
  POST /repositories/{id}/docs   generates the missing purposes in the background
                                 (see prose.py): the only part that calls a model.

Facts are read from the trees on disk on request, like the dependency changes (#34):
#33, storing the migrated code in Atlas, was closed. So both need the snapshot and the
run's workspace on this machine, and say so (409 SNAPSHOT_UNAVAILABLE) when they're
missing. Built-in fixtures rebuild their snapshot anywhere (#83).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.analysis.astwalk import ModuleAnalysis
from app.docs import prose, store
from app.docs.facts import module_facts
from app.docs.render import render
from app.errors import (
    DOCS_GENERATION_ACTIVE,
    MODEL_UNAVAILABLE,
    SNAPSHOT_UNAVAILABLE,
    AppError,
)


def _side(root: Path) -> tuple[dict[str, ModuleAnalysis], list, str | None]:
    """Every module in a tree, its import edges, and the declared Python version."""
    from app.graph.builder import build_edges
    from app.graph.changes import _analyse_tree
    from app.ingestion.runtime import inspect_runtime

    analyses = _analyse_tree(root)
    edges, _, _ = build_edges(analyses)
    return analyses, edges, inspect_runtime(root).get("declared_python")


def _unavailable(what: str, details: dict[str, Any]) -> AppError:
    return AppError(
        SNAPSHOT_UNAVAILABLE,
        f"The {what} is not on this machine, so its documentation cannot be read "
        "here. Ask on the machine that imported or ran it.",
        status_code=409, details=details,
    )


async def _legacy(repo_id: str) -> tuple[list[dict[str, Any]], dict, Path]:
    from app.ingestion import snapshot_path

    root = snapshot_path(repo_id)
    if not root.is_dir():
        raise _unavailable("repository's snapshot", {"repository_id": repo_id})
    analyses, edges, python = _side(root)
    found = await prose.purposes(repo_id, analyses)
    paths = {a.module_id: a.path for a in analyses.values()}
    modules = []
    for analysis in sorted(analyses.values(), key=lambda a: a.path):
        facts = module_facts(analysis, edges)
        purpose = found.get(analysis.path)
        modules.append({
            "module_id": analysis.module_id, "path": analysis.path,
            "is_test": analysis.is_test,
            "legacy": {
                "facts": facts, "purpose": purpose,
                "markdown": render(facts, side="legacy", repository_id=repo_id,
                                   python=python, purpose=purpose, paths=paths),
            },
        })
    return modules, {"python": python}, root


async def repository_docs(repo_id: str) -> dict[str, Any]:
    from app.ingestion import get_repository

    await get_repository(repo_id)  # 404 for an unknown repository
    modules, legacy, _ = await _legacy(repo_id)
    return {
        "repository_id": repo_id,
        "python": legacy["python"],
        "prose": prose.status_view(await store.get_generation(repo_id)),
        "modules": modules,
        "warnings": [],
    }


async def run_docs(run_id: str) -> dict[str, Any]:
    from app.graph.changes import compare_trees
    from app.orchestration import events
    from app.orchestration import store as runs
    from app.sandbox.workspaces import revision_path

    run = await runs.get_run(run_id)  # 404 for an unknown run
    journal = await events.list_all_events(run_id)
    accepted = [e for e in journal if e.event_type == "snapshot_accepted"]
    revision = accepted[-1].payload.get("revision") if accepted else None

    modules, legacy, before = await _legacy(run.repository_id)
    result = {
        "run_id": run_id, "repository_id": run.repository_id, "revision": revision,
        "python": {"legacy": legacy["python"], "migrated": None},
        "prose": prose.status_view(await store.get_generation(run.repository_id)),
        "modules": modules, "warnings": [],
    }
    for module in modules:
        module["migrated"] = None
    if not revision:
        # Nothing accepted is not "nothing changed": say which it is.
        result["warnings"].append("no revision was accepted in this run, so there is "
                                  "no migrated code to document")
        return result

    after = revision_path(run_id, revision)
    if not after.is_dir():
        raise _unavailable("run's accepted revision", {"run_id": run_id,
                                                       "revision": revision})
    analyses, edges, python = _side(after)
    result["python"]["migrated"] = python
    dependencies = {m["module_id"]: m for m in compare_trees(before, after)["modules"]}
    plan = await runs.get_plan(run.plan_id)
    states = {u.unit_id: u for u in run.units}
    unit_of = {path: u.unit_id for u in plan.units for path in u.file_paths}
    summaries = {e.unit_id: e.payload.get("summary") for e in journal
                 if e.event_type == "patch_proposed" and e.unit_id}
    paths = {a.module_id: a.path for a in analyses.values()}
    purposes = {m["path"]: m["legacy"]["purpose"] for m in modules}

    by_id = {m["module_id"]: m for m in modules}
    for analysis in analyses.values():
        unit_id = unit_of.get(analysis.path)
        state = states.get(unit_id) if unit_id else None
        change = {
            "run_id": run_id,
            "unit": {"unit_id": unit_id, "status": state.status,
                     "attempts": state.attempts} if state else None,
            # The last proposal for a unit that passed is the patch that was accepted.
            "summary": summaries.get(unit_id) if state and state.status == "passed"
                       else None,
            "dependencies": dependencies.get(analysis.module_id),
        }
        facts = module_facts(analysis, edges)
        if analysis.module_id not in by_id:  # a module the migration added
            by_id[analysis.module_id] = {"module_id": analysis.module_id,
                                         "path": analysis.path,
                                         "is_test": analysis.is_test, "legacy": None}
            modules.append(by_id[analysis.module_id])
        by_id[analysis.module_id]["migrated"] = {
            "facts": facts, "change": change,
            "markdown": render(facts, side="migrated", repository_id=run.repository_id,
                               python=python, purpose=purposes.get(analysis.path),
                               paths=paths, change=change),
        }
    modules.sort(key=lambda m: m["path"])
    return result


async def start_generation(repo_id: str) -> dict[str, Any]:
    """POST /repositories/{id}/docs: generate missing purposes in the background."""
    from app.config import get_settings
    from app.ingestion import get_repository, snapshot_path
    from app.search.settings import description_model

    await get_repository(repo_id)  # 404 for an unknown repository
    root = snapshot_path(repo_id)
    if not root.is_dir():
        raise _unavailable("repository's snapshot", {"repository_id": repo_id})
    if not get_settings().gemini_api_key:
        raise AppError(MODEL_UNAVAILABLE, "GEMINI_API_KEY is not configured, so no "
                       "purpose can be generated", status_code=503,
                       details={"repository_id": repo_id})
    if prose.is_active(await store.get_generation(repo_id)):
        raise AppError(DOCS_GENERATION_ACTIVE, "Documentation is already being "
                       "generated for this repository; GET it to follow progress",
                       status_code=409, details={"repository_id": repo_id})

    analyses, _, _ = _side(root)
    await prose.start(repo_id, analyses, description_model())
    return {"repository_id": repo_id,
            "prose": prose.status_view(await store.get_generation(repo_id))}


async def export_files(run_id: str) -> dict[str, str]:
    """The run's docs as Markdown files, for the export zip (#40). No model call."""
    docs = await run_docs(run_id)
    index = [f"# Documentation for {docs['run_id']}", "",
             f"Legacy: Python {docs['python']['legacy'] or 'not declared'}. "
             f"Migrated: Python {docs['python']['migrated'] or 'not declared'}.", "",
             "Facts come from the parse tree. Purposes are model-generated and Argon's "
             "change summaries are model-written; both are labelled and not verified.",
             "", f"Purposes: {docs['prose']['state']} "
             f"({docs['prose']['done']}/{docs['prose']['total']}).", ""]
    files = {}
    for module in docs["modules"]:
        sides = [s for s in ("legacy", "migrated") if module.get(s)]
        for side in sides:
            files[f"{side}/{module['path']}.md"] = module[side]["markdown"]
        links = ", ".join(f"[{s}]({s}/{module['path']}.md)" for s in sides)
        index.append(f"- `{module['path']}`: {links}")
    files["README.md"] = "\n".join(index) + "\n"
    return files
