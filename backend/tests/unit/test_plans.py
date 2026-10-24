"""Plan creation - owner: Dev 2. SHARED_CONTRACTS §6, §8. Offline.

`create_plan` had no tests at all, which is how #3 survived: the risk scoring itself
was correct and well tested, and the bug was that nothing rescored after Dev 4's case
counts arrived. A `rescore_risk` unit test cannot catch a missing call to it.

Everything outside Dev 2's lane is stubbed: the graph, the cases, the repository
record and Argon.
"""

from __future__ import annotations

import networkx as nx
import pytest

from app.orchestration import plans as plans_module
from app.orchestration import ports as ports_module
from app.orchestration import store as store_module

pytestmark = pytest.mark.anyio

REPO = "repo_000000000001"

# pricing is imported by everything, so it has the blast radius that makes risk
# interesting; main imports and is imported by nothing else.
EDGES = [
    ("module_orders", "module_pricing"),
    ("module_refunds", "module_pricing"),
    ("module_main", "module_orders"),
]
FILES = {
    "module_pricing": "pricing.py",
    "module_orders": "orders.py",
    "module_refunds": "refunds.py",
    "module_main": "main.py",
}


class Case:
    """Just enough of Dev 4's case record: `create_plan` only reads `unit_id`."""

    def __init__(self, unit_id: str) -> None:
        self.unit_id = unit_id


@pytest.fixture
def world(monkeypatch):
    state: dict = {"cases": [], "cases_available": True, "saved": []}

    graph = nx.DiGraph()
    graph.add_nodes_from(FILES)
    graph.add_edges_from(EDGES)

    async def load_networkx(repo_id):
        return graph

    def files_by_module(g):
        return FILES

    async def load_cases(repo_id, unit_id, *, include_held_out):
        if not state["cases_available"]:
            raise ports_module.IntegrationUnavailable(
                "Dev 4", "app.verification", "load_cases"
            )
        return state["cases"]

    async def get_repository(repo_id):
        from datetime import UTC, datetime

        from app.models.contracts import RepositoryRecord

        return RepositoryRecord(
            repository_id=repo_id, name="legacy_orders",
            snapshot_hash="sha256:abc", origin="demo_fixture", executable=True,
            demo_fixture_id="legacy_orders", file_count=4, module_count=4,
            symbol_count=20, test_file_count=1, analysis_status="ok",
            created_at=datetime.now(UTC),
        )

    async def unavailable(*args, **kwargs):
        raise ports_module.IntegrationUnavailable("Dev 1", "app.agents", "propose_plan")

    async def save_plan(plan):
        state["saved"].append(plan)
        return plan

    async def supersede_open_plans(repo_id, except_plan_id):
        return 0

    monkeypatch.setattr(ports_module, "load_networkx", load_networkx)
    monkeypatch.setattr(ports_module, "files_by_module", files_by_module)
    monkeypatch.setattr(ports_module, "load_cases", load_cases)
    monkeypatch.setattr(ports_module, "get_repository", get_repository)
    # No Argon: the deterministic order is used and said so in the rationale.
    monkeypatch.setattr(ports_module, "get_graph", unavailable)
    monkeypatch.setattr(ports_module, "propose_plan", unavailable)
    monkeypatch.setattr(store_module, "save_plan", save_plan)
    monkeypatch.setattr(store_module, "supersede_open_plans", supersede_open_plans)
    return state


def unit(plan, unit_id):
    return next(u for u in plan.units if u.unit_id == unit_id)


async def test_a_tested_unit_is_not_marked_as_having_no_visible_cases(world):
    """#3. The Plan screen showed `pricing.py` as risk High with the factor
    `no_visible_cases`, directly beside "Visible cases 8"."""
    world["cases"] = [Case("unit_pricing")] * 8

    plan = await plans_module.create_plan(REPO, "modern_python3")
    pricing = unit(plan, "unit_pricing")

    assert pricing.visible_case_count == 8
    assert "no_visible_cases" not in pricing.risk_factors
    # Blast radius is real and stays: three units import pricing transitively.
    assert "reverse_dependants=3" in pricing.risk_factors
    assert pricing.risk == "medium"


async def test_an_untested_unit_is_still_high_risk(world):
    """The fix must not make risk look better than it is."""
    world["cases"] = [Case("unit_pricing")] * 8   # nothing for orders

    plan = await plans_module.create_plan(REPO, "modern_python3")
    orders = unit(plan, "unit_orders")

    assert orders.visible_case_count == 0
    assert "no_visible_cases" in orders.risk_factors


async def test_every_unit_is_rescored_not_just_the_first(world):
    world["cases"] = [Case(u) for u in
                      ("unit_pricing", "unit_orders", "unit_refunds", "unit_main")] * 5

    plan = await plans_module.create_plan(REPO, "modern_python3")

    assert all(u.visible_case_count == 5 for u in plan.units)
    assert not any("no_visible_cases" in u.risk_factors for u in plan.units)


async def test_unavailable_cases_leave_the_factor_in_place(world):
    """With Dev 4 unwired the counts are genuinely unknown, and `no_visible_cases` is
    the honest answer - rescoring then would be inventing reassurance."""
    world["cases_available"] = False

    plan = await plans_module.create_plan(REPO, "modern_python3")

    assert all(u.visible_case_count == 0 for u in plan.units)
    assert all("no_visible_cases" in u.risk_factors for u in plan.units)


async def test_risk_and_its_factors_always_agree(world):
    """A label without its factors, or factors without the label, is what the screen
    showed: "High" next to evidence that contradicted it."""
    world["cases"] = [Case("unit_pricing")] * 8

    plan = await plans_module.create_plan(REPO, "modern_python3")

    for u in plan.units:
        if u.visible_case_count > 0:
            assert "no_visible_cases" not in u.risk_factors
        else:
            assert "no_visible_cases" in u.risk_factors
        if not u.risk_factors:
            assert u.risk == "low"


async def test_the_plan_is_persisted_once_scored(world):
    world["cases"] = [Case("unit_pricing")] * 8

    plan = await plans_module.create_plan(REPO, "modern_python3")

    assert len(world["saved"]) == 1
    saved = world["saved"][0]
    assert saved.plan_id == plan.plan_id
    # The stored plan carries the corrected risk, not the provisional one.
    assert "no_visible_cases" not in unit(saved, "unit_pricing").risk_factors
