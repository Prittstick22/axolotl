"""The Ask pane's tool allowlist - owner: Dev 2. #123.

A chat that can reach `propose_patch` can change the code it is describing; one that
can reach `get_counterexamples` can read held-out failures aloud. Neither is reachable
today, and the point of this file is that neither becomes reachable quietly.

Held-out data has leaked through four separate read paths already (#45 comparisons,
#56 gate logs, #57 the endpoint, #94 the event journal). Every one of them was a path
nobody had asserted on, not a rule somebody broke. So this asserts the set itself.
"""

import pytest

from app.agents import ask
from app.agents.tools import TOOL_MODELS

WRITE_TOOLS = {"propose_patch", "run_verification", "finish_unit"}
HELD_OUT_TOOLS = {"get_counterexamples", "get_behaviour_cases"}


def test_the_allowlist_is_exactly_these_five():
    """Pinned deliberately. Adding a tool here is a decision, not a detail: if this
    fails, say why the new tool cannot change anything and cannot see a held-out case."""
    assert set(ask.ASK_TOOLS) == {
        "get_repository_overview",
        "get_dependency_graph",
        "get_module_source",
        "get_symbol",
        "search_repository",
    }


def test_no_tool_that_changes_anything_is_reachable():
    assert not WRITE_TOOLS & set(ask.ASK_TOOLS)


def test_no_tool_that_touches_held_out_cases_is_reachable():
    """`get_behaviour_cases` returns only visible cases, so it is excluded for the
    other reason: it needs `ctx.unit.unit_id`, and a question has no unit."""
    assert not HELD_OUT_TOOLS & set(ask.ASK_TOOLS)


def test_every_allowlisted_tool_is_a_real_declared_tool():
    """A typo here would silently drop a tool rather than fail."""
    for name in ask.ASK_TOOLS:
        assert name in TOOL_MODELS, name


def test_the_model_is_only_shown_the_allowlisted_schemas():
    assert [s["name"] for s in ask.ASK_SCHEMAS] == list(ask.ASK_TOOLS)


def test_the_allowlist_is_a_strict_subset_of_the_migration_agents_tools():
    """It reuses the migration agent's tools; it never adds one of its own."""
    assert set(ask.ASK_TOOLS) < set(TOOL_MODELS)


# --------------------------------------------------------------------------
# the dispatcher, not just the list
# --------------------------------------------------------------------------

@pytest.mark.parametrize("name", sorted(WRITE_TOOLS | HELD_OUT_TOOLS))
async def test_a_refused_tool_is_refused_by_the_dispatcher_too(name):
    """Not only absent from the schemas: refused by name if the model asks anyway.

    This is what makes `controller=None` safe. A write tool reaching `ToolDispatcher`
    would raise `AttributeError` on the controller; it never gets there.
    """
    dispatcher = ask.ReadOnlyDispatcher("repo_1")

    result = await dispatcher.execute(name, {})

    assert result["error"] == "tool_not_available"
    assert result["tool"] == name


async def test_an_unknown_tool_is_refused_rather_than_guessed():
    dispatcher = ask.ReadOnlyDispatcher("repo_1")
    assert (await dispatcher.execute("rm_rf", {}))["error"] == "tool_not_available"


async def test_a_refusal_does_not_touch_the_controller():
    """There is no controller. If a refusal fell through, this would raise."""
    dispatcher = ask.ReadOnlyDispatcher("repo_1")
    assert dispatcher.controller is None

    await dispatcher.execute("propose_patch", {"files": [], "summary": "", "rationale": ""})

    assert dispatcher.controller is None
    assert dispatcher.retrieval == [], "a refused tool is not retrieval"


def test_the_context_carries_a_repository_and_nothing_else():
    """No unit, no budgets, no run: there is nothing for a write tool to act on."""
    context = ask.AskContext("repo_1")

    assert context.repository_id == "repo_1"
    for attribute in ("unit", "max_patch_attempts", "run_id"):
        assert not hasattr(context, attribute), attribute
