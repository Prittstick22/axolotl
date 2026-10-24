"""Dependency-first scheduler - owner: Dev 2. SHARED_CONTRACTS §8, Spec §5.4, §5.5.

Sync and pure: no I/O, no database, no model. Deterministic code decides what runs
next; Argon may only suggest an order among units that are already eligible.

Edge direction throughout: **A -> B means A depends on B** (A imports B).
"""

from __future__ import annotations

import networkx as nx

from app.models.contracts import MigrationUnit, Risk, UnitStatus

# --- risk weights (Spec §5.5) -------------------------------------------------
# [confirm] Spec §5.5 gives the shape of the score but no weights and no
# score->label thresholds, and forbids presenting these as failure probabilities.
# These are our choice; the factors are always surfaced alongside the label so a
# reader can judge them. Keep them boring and explainable.
W_REVERSE_DEPENDANT = 1
W_NO_VISIBLE_CASES = 2
W_DYNAMIC_EDGE = 2
W_CYCLE = 3

RISK_MEDIUM_AT = 2
RISK_HIGH_AT = 5

TERMINAL_UNIT_STATUSES: frozenset[str] = frozenset({"passed", "blocked", "failed", "skipped"})
BLOCKING_UNIT_STATUSES: frozenset[str] = frozenset({"blocked", "failed", "skipped"})


def module_suffix(module_id: str) -> str:
    """'module_pricing' -> 'pricing' (SHARED_CONTRACTS §3)."""
    return module_id.removeprefix("module_")


def unit_id_for(module_ids: list[str]) -> str:
    """SHARED_CONTRACTS §3: `unit_` + sorted module ids joined by `+` for SCCs."""
    return "unit_" + "+".join(sorted(module_suffix(m) for m in module_ids))


def score_risk(
    *,
    reverse_dependants: int,
    is_cycle: bool,
    dynamic_edges: int,
    visible_case_count: int,
) -> tuple[Risk, list[str]]:
    """Heuristic risk label plus the visible factors behind it.

    Returns (label, factors). Factors use the grammar the Dev 2 brief exemplifies:
    `reverse_dependants=3`, `dynamic_edges=1`, `cycle`, `no_visible_cases`.
    """
    factors: list[str] = []
    score = 0

    if reverse_dependants:
        factors.append(f"reverse_dependants={reverse_dependants}")
        score += W_REVERSE_DEPENDANT * reverse_dependants
    if dynamic_edges:
        factors.append(f"dynamic_edges={dynamic_edges}")
        score += W_DYNAMIC_EDGE * dynamic_edges
    if is_cycle:
        factors.append("cycle")
        score += W_CYCLE
    if visible_case_count == 0:
        factors.append("no_visible_cases")
        score += W_NO_VISIBLE_CASES

    if score >= RISK_HIGH_AT:
        return "high", factors
    if score >= RISK_MEDIUM_AT:
        return "medium", factors
    return "low", factors


def rescore_risk(units: list[MigrationUnit]) -> None:
    """Score risk again once `visible_case_count` is known. In place.

    `build_units` runs before Dev 4's cases can be counted - the counts are keyed by
    `unit_id`, which only exists once the units do - so every unit is first scored with
    `visible_case_count=0` and collects the `no_visible_cases` factor and its weight.
    `pricing.py` showed risk "High" with that factor beside "Visible cases 8".

    The two numeric inputs are read back out of the factors this module wrote, rather
    than stored on `MigrationUnit`: the grammar is defined in `score_risk` directly
    above, so both halves of the round trip live in one file and cannot drift apart.
    Idempotent - rescoring an already-scored unit gives the same answer.
    """
    for unit in units:
        unit.risk, unit.risk_factors = score_risk(
            reverse_dependants=_factor_count(unit.risk_factors, "reverse_dependants"),
            is_cycle=unit.is_cycle,
            dynamic_edges=_factor_count(unit.risk_factors, "dynamic_edges"),
            visible_case_count=unit.visible_case_count,
        )


def _factor_count(factors: list[str], name: str) -> int:
    """`["reverse_dependants=3", "cycle"], "reverse_dependants"` -> 3. Absent -> 0."""
    prefix = f"{name}="
    for factor in factors:
        if factor.startswith(prefix):
            try:
                return int(factor[len(prefix):])
            except ValueError:   # a factor shape that is not a count
                return 0
    return 0


def build_units(
    g: nx.DiGraph,
    files_by_module: dict[str, str],
    *,
    visible_case_counts: dict[str, int] | None = None,
    dynamic_edge_counts: dict[str, int] | None = None,
) -> list[MigrationUnit]:
    """Collapse strongly connected components into migration units.

    `g` holds local modules only (Dev 3's `load_networkx`). A multi-module SCC
    becomes one unit with `is_cycle=True`, because its members cannot be migrated
    independently.

    The two keyword arguments are additive and optional: without them units get
    `visible_case_count=0` and no dynamic-edge factor.
    """
    visible_case_counts = visible_case_counts or {}
    dynamic_edge_counts = dynamic_edge_counts or {}

    condensation = nx.condensation(g)
    members: dict[int, list[str]] = {
        component: sorted(condensation.nodes[component]["members"])
        for component in condensation.nodes
    }
    unit_ids: dict[int, str] = {c: unit_id_for(m) for c, m in members.items()}

    units: list[MigrationUnit] = []
    for component in sorted(condensation.nodes, key=lambda c: unit_ids[c]):
        module_ids = members[component]

        # condensation edge u -> v means unit u depends on unit v
        depends_on = sorted(unit_ids[v] for v in condensation.successors(component))
        dependants = sorted(unit_ids[u] for u in condensation.predecessors(component))

        file_paths = sorted(
            files_by_module[m] for m in module_ids if m in files_by_module
        )
        visible = sum(visible_case_counts.get(m, 0) for m in module_ids)
        dynamic = sum(dynamic_edge_counts.get(m, 0) for m in module_ids)
        is_cycle = len(module_ids) > 1

        # Risk uses *transitive* reverse dependants: the blast radius if this
        # unit's behaviour changes, not just its immediate importers.
        reverse_count = len(_transitive(condensation.predecessors, component, unit_ids))

        risk, factors = score_risk(
            reverse_dependants=reverse_count,
            is_cycle=is_cycle,
            dynamic_edges=dynamic,
            visible_case_count=visible,
        )

        units.append(
            MigrationUnit(
                unit_id=unit_ids[component],
                module_ids=module_ids,
                file_paths=file_paths,
                depends_on_unit_ids=depends_on,
                dependant_unit_ids=dependants,
                is_cycle=is_cycle,
                risk=risk,
                risk_factors=factors,
                visible_case_count=visible,
            )
        )

    return units


def _transitive(neighbours, start, unit_ids: dict[int, str]) -> set[str]:
    """Transitive closure over condensation nodes, excluding the start node."""
    seen: set[int] = set()
    stack = list(neighbours(start))
    while stack:
        node = stack.pop()
        if node in seen or node == start:
            continue
        seen.add(node)
        stack.extend(neighbours(node))
    return {unit_ids[n] for n in seen}


def eligible_units(
    units: list[MigrationUnit], statuses: dict[str, UnitStatus]
) -> list[str]:
    """Unit ids that may start now: themselves `queued`, every dependency `passed`.

    Deterministic: dependency-count first, then unit_id, so two runs over the same
    graph pick the same next unit. Argon may reorder *within* this list; it can
    never add to it.
    """
    by_id = {u.unit_id: u for u in units}
    ready: list[MigrationUnit] = []

    for unit in units:
        if statuses.get(unit.unit_id, "not_planned") != "queued":
            continue
        if all(
            statuses.get(dep) == "passed"
            for dep in unit.depends_on_unit_ids
            if dep in by_id
        ):
            ready.append(unit)

    ready.sort(key=lambda u: (len(u.depends_on_unit_ids), u.unit_id))
    return [u.unit_id for u in ready]


def impacted_units(unit_id: str, units: list[MigrationUnit]) -> list[str]:
    """Transitive reverse dependants of `unit_id` - whose tests must also rerun.

    Excludes `unit_id` itself. Sorted, so the gate order is reproducible.
    """
    by_id = {u.unit_id: u for u in units}
    if unit_id not in by_id:
        return []

    seen: set[str] = set()
    stack = list(by_id[unit_id].dependant_unit_ids)
    while stack:
        current = stack.pop()
        if current in seen or current == unit_id or current not in by_id:
            continue
        seen.add(current)
        stack.extend(by_id[current].dependant_unit_ids)
    return sorted(seen)


def units_to_skip(
    units: list[MigrationUnit], statuses: dict[str, UnitStatus]
) -> dict[str, str]:
    """T-13: nothing is migrated through a failed prerequisite.

    Returns {unit_id: reason} for every non-terminal unit that transitively depends
    on a `blocked`, `failed` or `skipped` unit. The caller moves them to `skipped`
    and records the reason - this function decides, it does not mutate.
    """
    by_id = {u.unit_id: u for u in units}
    skip: dict[str, str] = {}

    changed = True
    while changed:
        changed = False
        for unit in units:
            current = statuses.get(unit.unit_id, "not_planned")
            if current in TERMINAL_UNIT_STATUSES or unit.unit_id in skip:
                continue
            for dep in unit.depends_on_unit_ids:
                if dep not in by_id:
                    continue
                if dep in skip:
                    dep_status: str | None = "skipped"
                else:
                    dep_status = statuses.get(dep)
                if dep_status in BLOCKING_UNIT_STATUSES:
                    skip[unit.unit_id] = (
                        f"dependency {dep} is {dep_status}; never migrated through "
                        "a failed prerequisite"
                    )
                    changed = True
                    break

    return skip


def topological_order(units: list[MigrationUnit]) -> list[str]:
    """Deterministic dependency-first order over all units.

    Used as the fallback when Argon's `suggested_order` is not a valid topological
    order of the condensation. Ties break on unit_id so it is reproducible.
    """
    graph = nx.DiGraph()
    for unit in units:
        graph.add_node(unit.unit_id)
    for unit in units:
        for dep in unit.depends_on_unit_ids:
            if dep in graph:
                graph.add_edge(dep, unit.unit_id)  # dependency first
    return list(nx.lexicographical_topological_sort(graph))


def is_valid_order(order: list[str], units: list[MigrationUnit]) -> bool:
    """True if `order` lists every unit exactly once, dependencies before dependants."""
    expected = {u.unit_id for u in units}
    if sorted(order) != sorted(expected):
        return False

    position = {unit_id: i for i, unit_id in enumerate(order)}
    for unit in units:
        for dep in unit.depends_on_unit_ids:
            if dep in position and position[dep] > position[unit.unit_id]:
                return False
    return True
