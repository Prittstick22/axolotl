"""Scheduler unit tests - owner: Dev 2. Covers T-05 (SCC) and T-13 (gated progression).

The graph used throughout is the demo fixture's real module graph: edge A -> B means
A depends on B. It is the exact 7-edge set in demo/legacy_orders/README.md.
"""

import networkx as nx
import pytest

from app.orchestration.scheduler import (
    build_units,
    eligible_units,
    impacted_units,
    is_valid_order,
    rescore_risk,
    score_risk,
    topological_order,
    unit_id_for,
    units_to_skip,
)

FIXTURE_EDGES = [
    ("module_main", "module_orders"),
    ("module_main", "module_refunds"),
    ("module_orders", "module_pricing"),
    ("module_orders", "module_customers"),
    ("module_orders", "module_storage"),
    ("module_refunds", "module_pricing"),
    ("module_refunds", "module_storage"),
]

FILES_BY_MODULE = {
    "module_main": "main.py",
    "module_orders": "orders.py",
    "module_refunds": "refunds.py",
    "module_pricing": "pricing.py",
    "module_customers": "customers.py",
    "module_storage": "storage.py",
}


@pytest.fixture
def fixture_graph() -> nx.DiGraph:
    graph = nx.DiGraph()
    graph.add_nodes_from(FILES_BY_MODULE)
    graph.add_edges_from(FIXTURE_EDGES)
    return graph


@pytest.fixture
def fixture_units(fixture_graph):
    return build_units(fixture_graph, FILES_BY_MODULE)


def by_id(units):
    return {u.unit_id: u for u in units}


# --------------------------------------------------------------------------
# build_units
# --------------------------------------------------------------------------

def test_acyclic_graph_gives_one_unit_per_module(fixture_units):
    assert len(fixture_units) == 6
    assert set(by_id(fixture_units)) == {
        "unit_main", "unit_orders", "unit_refunds",
        "unit_pricing", "unit_customers", "unit_storage",
    }
    assert all(not u.is_cycle for u in fixture_units)


def test_dependencies_point_at_what_a_unit_needs(fixture_units):
    units = by_id(fixture_units)
    assert units["unit_orders"].depends_on_unit_ids == [
        "unit_customers", "unit_pricing", "unit_storage",
    ]
    assert units["unit_refunds"].depends_on_unit_ids == ["unit_pricing", "unit_storage"]
    assert units["unit_main"].depends_on_unit_ids == ["unit_orders", "unit_refunds"]
    assert units["unit_pricing"].depends_on_unit_ids == []


def test_dependants_are_the_reverse(fixture_units):
    units = by_id(fixture_units)
    assert units["unit_pricing"].dependant_unit_ids == ["unit_orders", "unit_refunds"]
    assert units["unit_customers"].dependant_unit_ids == ["unit_orders"]
    assert units["unit_main"].dependant_unit_ids == []


def test_file_paths_are_carried_through(fixture_units):
    assert by_id(fixture_units)["unit_pricing"].file_paths == ["pricing.py"]


def test_visible_case_counts_are_summed_per_unit(fixture_graph):
    units = by_id(build_units(
        fixture_graph, FILES_BY_MODULE, visible_case_counts={"module_pricing": 8}
    ))
    assert units["unit_pricing"].visible_case_count == 8
    assert units["unit_storage"].visible_case_count == 0


# --------------------------------------------------------------------------
# T-05: cycles collapse into one unit
# --------------------------------------------------------------------------

def test_two_module_cycle_becomes_one_unit_flagged_as_a_cycle():
    graph = nx.DiGraph()
    graph.add_edges_from([("module_a", "module_b"), ("module_b", "module_a"),
                          ("module_a", "module_c")])
    units = by_id(build_units(graph, {
        "module_a": "a.py", "module_b": "b.py", "module_c": "c.py",
    }))

    assert "unit_a+b" in units
    cycle_unit = units["unit_a+b"]
    assert cycle_unit.is_cycle is True
    assert cycle_unit.module_ids == ["module_a", "module_b"]
    assert sorted(cycle_unit.file_paths) == ["a.py", "b.py"]
    assert "cycle" in cycle_unit.risk_factors
    # The cycle depends on c, and c is a separate, non-cyclic unit.
    assert cycle_unit.depends_on_unit_ids == ["unit_c"]
    assert units["unit_c"].is_cycle is False


def test_three_module_cycle_ids_are_sorted_and_deterministic():
    graph = nx.DiGraph()
    graph.add_edges_from([("module_z", "module_y"), ("module_y", "module_x"),
                          ("module_x", "module_z")])
    units = build_units(graph, {})
    assert len(units) == 1
    assert units[0].unit_id == "unit_x+y+z"
    assert unit_id_for(["module_z", "module_x", "module_y"]) == "unit_x+y+z"


# --------------------------------------------------------------------------
# eligible_units
# --------------------------------------------------------------------------

def test_only_leaves_are_eligible_at_the_start(fixture_units):
    statuses = {u.unit_id: "queued" for u in fixture_units}
    assert eligible_units(fixture_units, statuses) == [
        "unit_customers", "unit_pricing", "unit_storage",
    ]


def test_a_unit_becomes_eligible_once_every_dependency_passed(fixture_units):
    statuses = {u.unit_id: "queued" for u in fixture_units}
    statuses.update({"unit_pricing": "passed", "unit_storage": "passed"})
    # orders still waits on customers; refunds is now free.
    assert "unit_refunds" in eligible_units(fixture_units, statuses)
    assert "unit_orders" not in eligible_units(fixture_units, statuses)

    statuses["unit_customers"] = "passed"
    assert "unit_orders" in eligible_units(fixture_units, statuses)


def test_a_unit_already_running_is_not_eligible_again(fixture_units):
    statuses = {u.unit_id: "queued" for u in fixture_units}
    statuses["unit_pricing"] = "migrating"
    assert "unit_pricing" not in eligible_units(fixture_units, statuses)


def test_eligibility_order_is_deterministic(fixture_units):
    statuses = {u.unit_id: "queued" for u in fixture_units}
    assert eligible_units(fixture_units, statuses) == eligible_units(
        list(reversed(fixture_units)), statuses
    )


# --------------------------------------------------------------------------
# impacted_units
# --------------------------------------------------------------------------

def test_impacted_units_are_transitive_reverse_dependants(fixture_units):
    assert impacted_units("unit_pricing", fixture_units) == [
        "unit_main", "unit_orders", "unit_refunds",
    ]


def test_a_leaf_dependant_impacts_nothing(fixture_units):
    assert impacted_units("unit_main", fixture_units) == []


def test_impacted_units_excludes_itself_and_handles_unknown(fixture_units):
    assert "unit_pricing" not in impacted_units("unit_pricing", fixture_units)
    assert impacted_units("unit_nope", fixture_units) == []


def test_impacted_units_terminates_on_a_cycle():
    graph = nx.DiGraph()
    graph.add_edges_from([("module_a", "module_b"), ("module_b", "module_a")])
    units = build_units(graph, {})
    # Both modules are one unit, so nothing depends on it from outside.
    assert impacted_units("unit_a+b", units) == []


# --------------------------------------------------------------------------
# T-13: nothing is migrated through a failed prerequisite
# --------------------------------------------------------------------------

def test_blocked_dependency_skips_its_dependants_transitively(fixture_units):
    statuses = {u.unit_id: "queued" for u in fixture_units}
    statuses["unit_pricing"] = "blocked"

    skipped = units_to_skip(fixture_units, statuses)

    assert set(skipped) == {"unit_orders", "unit_refunds", "unit_main"}
    assert "unit_pricing" in skipped["unit_orders"]
    assert "never migrated through a failed prerequisite" in skipped["unit_orders"]
    # customers and storage do not depend on pricing, so they are untouched.
    assert "unit_customers" not in skipped
    assert "unit_storage" not in skipped


def test_a_blocked_unit_makes_its_dependants_ineligible(fixture_units):
    statuses = {u.unit_id: "queued" for u in fixture_units}
    statuses.update({"unit_pricing": "blocked", "unit_storage": "passed",
                     "unit_customers": "passed"})
    assert "unit_orders" not in eligible_units(fixture_units, statuses)
    assert "unit_refunds" not in eligible_units(fixture_units, statuses)


def test_nothing_is_skipped_when_everything_is_healthy(fixture_units):
    statuses = {u.unit_id: "queued" for u in fixture_units}
    assert units_to_skip(fixture_units, statuses) == {}


def test_already_passed_units_are_never_retrospectively_skipped(fixture_units):
    statuses = {u.unit_id: "queued" for u in fixture_units}
    statuses.update({"unit_pricing": "passed", "unit_storage": "passed",
                     "unit_customers": "blocked"})
    skipped = units_to_skip(fixture_units, statuses)
    assert "unit_pricing" not in skipped
    assert "unit_orders" in skipped        # orders depends on blocked customers
    assert "unit_refunds" not in skipped   # refunds does not


# --------------------------------------------------------------------------
# risk
# --------------------------------------------------------------------------

def test_risk_rises_with_blast_radius_and_names_its_factors():
    label, factors = score_risk(
        reverse_dependants=3, is_cycle=False, dynamic_edges=0, visible_case_count=8
    )
    assert label == "medium"
    assert factors == ["reverse_dependants=3"]


def test_untested_widely_used_module_is_high_risk():
    label, factors = score_risk(
        reverse_dependants=3, is_cycle=False, dynamic_edges=0, visible_case_count=0
    )
    assert label == "high"
    assert factors == ["reverse_dependants=3", "no_visible_cases"]


def test_an_isolated_well_tested_leaf_is_low_risk():
    label, factors = score_risk(
        reverse_dependants=0, is_cycle=False, dynamic_edges=0, visible_case_count=5
    )
    assert label == "low"
    assert factors == []


def test_a_cycle_is_always_at_least_medium():
    label, factors = score_risk(
        reverse_dependants=0, is_cycle=True, dynamic_edges=0, visible_case_count=5
    )
    assert label in {"medium", "high"}
    assert "cycle" in factors


def test_pricing_is_the_riskiest_unit_in_the_fixture(fixture_graph):
    units = by_id(build_units(fixture_graph, FILES_BY_MODULE))
    # pricing is imported by orders and refunds, which main imports: 3 transitively.
    assert "reverse_dependants=3" in units["unit_pricing"].risk_factors
    assert units["unit_main"].risk_factors == ["no_visible_cases"]


# --------------------------------------------------------------------------
# ordering
# --------------------------------------------------------------------------

def test_topological_order_puts_dependencies_first(fixture_units):
    order = topological_order(fixture_units)
    assert order.index("unit_pricing") < order.index("unit_orders")
    assert order.index("unit_storage") < order.index("unit_refunds")
    assert order.index("unit_orders") < order.index("unit_main")
    assert is_valid_order(order, fixture_units)


def test_topological_order_is_stable(fixture_units):
    assert topological_order(fixture_units) == topological_order(fixture_units)


def test_an_order_that_migrates_a_dependant_first_is_rejected(fixture_units):
    bad = ["unit_main", "unit_orders", "unit_refunds",
           "unit_pricing", "unit_customers", "unit_storage"]
    assert is_valid_order(bad, fixture_units) is False


def test_an_order_missing_a_unit_is_rejected(fixture_units):
    order = topological_order(fixture_units)
    assert is_valid_order(order[:-1], fixture_units) is False


def test_an_order_with_a_duplicate_is_rejected(fixture_units):
    order = topological_order(fixture_units)
    assert is_valid_order([*order[:-1], order[0]], fixture_units) is False


# --------------------------------------------------------------------------
# #3: risk is scored again once the visible case counts are known
# --------------------------------------------------------------------------

def test_a_unit_with_cases_loses_the_no_visible_cases_factor(fixture_units):
    """The Plan screen showed `pricing.py` as "High" with `no_visible_cases` beside
    "Visible cases 8"."""
    units = by_id(fixture_units)
    pricing = units["unit_pricing"]

    # As built: the counts do not exist yet, so every unit looks untested.
    assert "no_visible_cases" in pricing.risk_factors
    before = pricing.risk

    for unit in fixture_units:
        unit.visible_case_count = 8
    rescore_risk(fixture_units)

    assert "no_visible_cases" not in pricing.risk_factors
    assert pricing.risk != before or before == "low"


def test_rescoring_keeps_the_factors_that_do_not_depend_on_cases(fixture_units):
    """Blast radius, cycles and dynamic edges are unaffected by case counts, so they
    must survive the round trip through the factor strings."""
    units = by_id(fixture_units)
    storage = units["unit_storage"]
    kept = [f for f in storage.risk_factors if f != "no_visible_cases"]

    for unit in fixture_units:
        unit.visible_case_count = 3
    rescore_risk(fixture_units)

    assert storage.risk_factors == kept


def test_a_unit_with_no_cases_keeps_the_factor(fixture_units):
    """Rescoring is not a way of making risk look better than it is."""
    units = by_id(fixture_units)
    for unit in fixture_units:
        unit.visible_case_count = 0
    rescore_risk(fixture_units)

    assert "no_visible_cases" in units["unit_pricing"].risk_factors


def test_rescoring_twice_changes_nothing(fixture_units):
    """It reads its own output, so it has to be idempotent."""
    for unit in fixture_units:
        unit.visible_case_count = 4
    rescore_risk(fixture_units)
    once = {u.unit_id: (u.risk, list(u.risk_factors)) for u in fixture_units}

    rescore_risk(fixture_units)
    twice = {u.unit_id: (u.risk, list(u.risk_factors)) for u in fixture_units}

    assert once == twice


def test_a_cycle_unit_keeps_its_cycle_factor_through_a_rescore():
    """`is_cycle` comes off the unit, not the factor string, so pin it."""
    graph = nx.DiGraph()
    graph.add_edges_from([("module_a", "module_b"), ("module_b", "module_a")])
    units = build_units(graph, {"module_a": "a.py", "module_b": "b.py"})

    assert len(units) == 1
    assert units[0].is_cycle
    units[0].visible_case_count = 6
    rescore_risk(units)

    assert "cycle" in units[0].risk_factors
    assert "no_visible_cases" not in units[0].risk_factors


def test_a_dynamic_edge_count_survives_the_round_trip():
    graph = nx.DiGraph()
    graph.add_nodes_from(["module_a"])
    units = build_units(graph, {"module_a": "a.py"},
                        dynamic_edge_counts={"module_a": 2})

    assert "dynamic_edges=2" in units[0].risk_factors
    units[0].visible_case_count = 5
    rescore_risk(units)

    assert "dynamic_edges=2" in units[0].risk_factors


def test_a_malformed_factor_does_not_crash_the_rescore():
    """Defensive: a factor that is not a count reads as zero rather than raising."""
    from app.orchestration.scheduler import _factor_count

    assert _factor_count(["reverse_dependants=nonsense"], "reverse_dependants") == 0
    assert _factor_count(["cycle"], "reverse_dependants") == 0
    assert _factor_count([], "dynamic_edges") == 0
