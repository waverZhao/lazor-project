"""SMT feasibility planning with a symbolic optical transition relation.

There is no placement BFS. One SMT formula describes block decisions and the
least source-reachable set of ray states. Strictly descending parent witnesses
exclude unsupported cycles, while forward closure preserves real ray loops.

Optional source-reachability pruning first builds the full model, then rebuilds
a smaller one. It preserves feasibility but does not guarantee a speedup.
This is a feasibility problem, with no shortest-path or minimum-block objective.
"""

import importlib
import sys
from dataclasses import dataclass, replace
from math import ceil, isfinite
from pathlib import Path
from time import perf_counter
from typing import Any, Literal

from block_laser import Puzzle, SimulationResult, State, simulate

PlanStatus = Literal["solved", "no_solution", "timeout", "unknown"]
EMPTY, REFLECT, OPAQUE, SPLIT = 0, 1, 2, 3
KIND_CODES = {"A": REFLECT, "B": OPAQUE, "C": SPLIT}
DIRECTIONS = ((-1, -1), (-1, 1), (1, -1), (1, 1))


def _load_z3():
    """Use installed Z3, then an explicit .deps beside this package or above it."""
    try:
        module = importlib.import_module("z3")
    except ModuleNotFoundError as exc:
        if exc.name != "z3":
            raise RuntimeError(
                "The installed z3-solver package is incomplete. Reinstall "
                "it with python -m pip install --force-reinstall z3-solver."
            ) from exc
        package_dir = Path(__file__).resolve().parent
        candidates = (package_dir / ".deps", package_dir.parent / ".deps")
        local = None
        access_errors = []
        for path in candidates:
            try:
                if (path / "z3" / "__init__.py").is_file():
                    local = path
                    break
            except OSError as access_error:
                access_errors.append((path, access_error))
        if local is None:
            if access_errors:
                inaccessible, access_error = access_errors[-1]
                raise RuntimeError(
                    f"Cannot access the local z3-solver dependency at "
                    f"{inaccessible}. Check its permissions, or install "
                    "z3-solver in this Python environment."
                ) from access_error
            raise RuntimeError(
                "SMT solving requires z3-solver. Run python -m pip install "
                "z3-solver, or install it with --target .deps beside these "
                "scripts. DFS does not require Z3."
            ) from exc
        sys.path.insert(0, str(local))
        try:
            module = importlib.import_module("z3")
        except (ImportError, OSError) as local_exc:
            raise RuntimeError(
                f"Could not load z3-solver from {local}. Reinstall a version "
                "matching this Python and operating system."
            ) from local_exc
    except (ImportError, OSError) as exc:
        raise RuntimeError(
            "Could not load the installed z3-solver. Reinstall a version "
            "matching this Python and operating system."
        ) from exc
    if not hasattr(module, "Solver"):
        raise RuntimeError("Install z3-solver, not the unrelated z3 package.")
    return module


@dataclass(frozen=True)
class Transition:
    """A possible directed optical edge and its block-dependent guard."""

    source: State
    target: State
    condition: Any


@dataclass(frozen=True)
class PlanningModel:
    """Inspectable SMT model; Z3 objects stay out of worker result messages."""

    puzzle: Puzzle
    solver: Any
    cell_vars: dict[tuple[int, int], Any]
    states: tuple[State, ...]
    reachable: dict[State, Any]
    ranks: dict[State, Any]
    edges: tuple[Transition, ...]
    pruning_enabled: bool = False
    unpruned_state_count: int = 0
    unpruned_transition_count: int = 0

    @property
    def variable_count(self) -> int:
        """Count block decisions, reachability Booleans, and witness ranks."""
        return len(self.cell_vars) + 2 * len(self.states)

    @property
    def constraint_count(self) -> int:
        """Number of top-level assertions in the feasibility model."""
        return len(self.solver.assertions())

    def to_smt2(self) -> str:
        """Export a standalone SMT-LIB formula for inspection or replay."""
        state_map = "".join(
            f"; {self.reachable[state]}, {self.ranks[state]}: state {state}\n"
            for state in self.states
        )
        return (
            "; Lazor state-transition feasibility planning\n"
            "; Cells: 0=empty, 1=A, 2=B, 3=C\n"
            "; reach states use half-grid coordinates and direction.\n"
            "; rank is a source witness, not a bound on physical looping.\n"
            f"; source-reachability pruning: {self.pruning_enabled}\n"
            f"; states: {len(self.states)}/{self.unpruned_state_count}; "
            f"transitions: {len(self.edges)}/{self.unpruned_transition_count}\n"
            + state_map
            + self.solver.to_smt2()
        )


@dataclass(frozen=True)
class PlanningResult:
    """Outcome, model size, timing, and a simulator-verified solution.

    Only no_solution represents a solver proof of infeasibility. Timeout and
    unknown retain the solver reason and make no claim about feasibility.
    """

    status: PlanStatus
    solution: SimulationResult | None
    elapsed_seconds: float
    build_seconds: float
    solver_seconds: float
    verification_seconds: float
    state_count: int
    transition_count: int
    variable_count: int
    constraint_count: int
    solver_version: str
    reason: str
    solver_checks: int
    reachable_states: frozenset[State]
    pruning_enabled: bool = False
    unpruned_state_count: int = 0
    unpruned_transition_count: int = 0

    @property
    def solved(self) -> bool:
        """Whether a model and its complete optical result were verified."""
        return self.status == "solved"


def _forward_cell(state: State) -> tuple[int, int]:
    """Identify the cell entered from a half-grid face midpoint."""
    x, y, vx, vy = state
    if x % 2 == 0:
        return x // 2 - int(vx < 0), y // 2
    return x // 2, y // 2 - int(vy < 0)


def build_planning_model(
    puzzle: Puzzle,
    *,
    prune_unreachable: bool = False,
) -> PlanningModel:
    """Build an exact finite reachability model, including cycle witnesses.

    For every enabled edge u -> v, reach[u] implies reach[v]. Conversely each
    reachable non-source v needs at least ONE enabled reachable predecessor u
    with rank[u] < rank[v]. Not every edge must increase rank: cycles remain
    valid when a source-supported spanning witness tree exists.

    With prune_unreachable=True, build the full formula first and then
    rebuild it using only an optimistic superset of source-reachable states.
    No true path is removed. Building twice can cost more than it saves.
    """
    if type(prune_unreachable) is not bool:
        raise ValueError("prune_unreachable must be a bool.")
    z3 = _load_z3()
    # Validate input geometry through the established independent simulator.
    simulate(puzzle)
    counts = puzzle.block_counts
    if any(type(counts.get(kind, 0)) is not int
           or counts.get(kind, 0) < 0 for kind in KIND_CODES):
        raise ValueError(
            "Movable inventory must contain nonnegative integers."
        )
    if sum(counts.values()) > len(puzzle.placeable_positions):
        raise ValueError("Movable inventory exceeds available cells.")
    if not puzzle.lasers or not puzzle.targets:
        raise ValueError("At least one laser and target are required.")

    solver = z3.Solver()
    solver.set(random_seed=0)
    cells = {
        position: z3.Int(f"block_{position[0]}_{position[1]}")
        for position in puzzle.placeable_positions
    }
    for variable in cells.values():
        solver.add(variable >= EMPTY, variable <= SPLIT)
    for kind, code in KIND_CODES.items():
        solver.add(z3.Sum([
            z3.If(variable == code, 1, 0) for variable in cells.values()
        ]) == counts.get(kind, 0))

    def block_at(position):
        if position in cells:
            return cells[position]
        column, row = position
        if 0 <= column < puzzle.width and 0 <= row < puzzle.height:
            return z3.IntVal(KIND_CODES.get(puzzle.grid[row][column], EMPTY))
        return z3.IntVal(EMPTY)

    max_x, max_y = 2 * puzzle.width, 2 * puzzle.height
    states = tuple(
        (x, y, vx, vy)
        for x in range(max_x + 1) for y in range(max_y + 1)
        if (x + y) % 2 == 1 for vx, vy in DIRECTIONS
    )
    reachable = {
        state: z3.Bool(f"reach_{i}") for i, state in enumerate(states)
    }
    ranks = {state: z3.Int(f"rank_{i}") for i, state in enumerate(states)}
    sources = {(ray.x, ray.y, ray.vx, ray.vy) for ray in puzzle.lasers}
    incoming = {state: [] for state in states}
    edges = []

    for state in states:
        x, y, vx, vy = state
        front = block_at(_forward_cell(state))
        reflected = (-vx, vy) if x % 2 == 0 else (vx, -vy)
        branches = (
            ((vx, vy), z3.Or(front == EMPTY, front == SPLIT)),
            (reflected, z3.Or(front == REFLECT, front == SPLIT)),
        )
        for (next_vx, next_vy), optical_guard in branches:
            next_x, next_y = x + next_vx, y + next_vy
            if not (0 <= next_x <= max_x and 0 <= next_y <= max_y):
                continue
            # Do not let a source between occupied cells reflect through
            # an opaque/mirror block behind it; this mirrors the tracer rule.
            travel = block_at(_forward_cell((x, y, next_vx, next_vy)))
            guard = z3.simplify(z3.And(
                optical_guard, z3.Or(travel == EMPTY, travel == SPLIT)
            ))
            if z3.is_false(guard):
                continue
            target = (next_x, next_y, next_vx, next_vy)
            edge = Transition(state, target, guard)
            edges.append(edge)
            incoming[target].append(edge)
            solver.add(z3.Implies(
                z3.And(reachable[state], guard), reachable[target]
            ))

    for state in states:
        active, rank = reachable[state], ranks[state]
        if state in sources:
            solver.add(active, rank == 0)
            continue
        solver.add(z3.If(
            active, z3.And(rank >= 1, rank < len(states)), rank == -1
        ))
        witnesses = [z3.And(
            reachable[edge.source], edge.condition,
            ranks[edge.source] < rank,
        ) for edge in incoming[state]]
        solver.add(z3.Implies(active, z3.Or(witnesses)))

    for x, y in puzzle.targets:
        # Or([]) is False: a target outside the face-midpoint state domain
        # cannot be silently ignored or manufactured as a reachable state.
        solver.add(z3.Or([
            reachable[(x, y, vx, vy)] for vx, vy in DIRECTIONS
            if (x, y, vx, vy) in reachable
        ]))

    model = PlanningModel(
        puzzle, solver, cells, states, reachable, ranks, tuple(edges),
        unpruned_state_count=len(states),
        unpruned_transition_count=len(edges),
    )
    if prune_unreachable:
        return _prune_source_unreachable(model, z3)
    return model


def _prune_source_unreachable(model: PlanningModel, z3: Any) -> PlanningModel:
    """Rebuild a model on an optimistic source-reachable state superset.

    An edge is retained whenever its optical guard was not simplified to
    False in the full model. Here we ignore guards altogether when walking
    the graph, including whether different guards can hold simultaneously.
    Every physically possible ray path is therefore contained in this graph.

    States outside its source-reachable set cannot occur under any legal
    placement. Removing them is safe. The retained graph still requires
    forward closure AND rank witnesses, so disconnected cycles cannot create
    light and source-connected loops remain valid. All cell variables and
    inventory constraints survive even if their cells lie outside this set.
    """
    puzzle = model.puzzle
    adjacency: dict[State, list[State]] = {state: [] for state in model.states}
    for edge in model.edges:
        adjacency[edge.source].append(edge.target)

    sources = {(ray.x, ray.y, ray.vx, ray.vy) for ray in puzzle.lasers}
    keep = set(sources)
    pending = list(sources)
    while pending:
        for successor in adjacency[pending.pop()]:
            if successor not in keep:
                keep.add(successor)
                pending.append(successor)

    states = tuple(state for state in model.states if state in keep)
    reachable = {state: model.reachable[state] for state in states}
    ranks = {state: model.ranks[state] for state in states}
    # Forward reachability makes keep closed under every candidate edge.
    edges = tuple(edge for edge in model.edges if edge.source in keep)
    incoming: dict[State, list[Transition]] = {state: [] for state in states}
    solver = z3.Solver()
    solver.set(random_seed=0)

    for variable in model.cell_vars.values():
        solver.add(variable >= EMPTY, variable <= SPLIT)
    for kind, code in KIND_CODES.items():
        solver.add(z3.Sum([
            z3.If(variable == code, 1, 0)
            for variable in model.cell_vars.values()
        ]) == puzzle.block_counts.get(kind, 0))

    for edge in edges:
        incoming[edge.target].append(edge)
        solver.add(z3.Implies(
            z3.And(reachable[edge.source], edge.condition),
            reachable[edge.target],
        ))

    for state in states:
        active, rank = reachable[state], ranks[state]
        if state in sources:
            solver.add(active, rank == 0)
            continue
        solver.add(z3.If(
            active, z3.And(rank >= 1, rank < len(states)), rank == -1
        ))
        solver.add(z3.Implies(active, z3.Or([
            z3.And(reachable[edge.source], edge.condition,
                   ranks[edge.source] < rank)
            for edge in incoming[state]
        ])))

    for x, y in puzzle.targets:
        # An unreachable target must make the formula UNSAT, not disappear.
        solver.add(z3.Or([
            reachable[(x, y, vx, vy)] for vx, vy in DIRECTIONS
            if (x, y, vx, vy) in reachable
        ]))

    return replace(
        model, solver=solver, states=states, reachable=reachable,
        ranks=ranks, edges=edges, pruning_enabled=True,
    )


def solve_constraints(
    puzzle: Puzzle,
    *,
    timeout: float | None = 110.0,
    model_path: str | Path | None = None,
    prune_unreachable: bool = False,
) -> PlanningResult:
    """Solve one feasibility formula and independently replay its answer.

    The budget includes building, SMT checking, and verification. Z3 gets the
    remaining budget, not the original budget again. Python build/verification
    stages are checked at their boundaries rather than forcibly interrupted.
    SMT internally performs logical search; this is not a closed-form formula.

    Optional source-reachability pruning rebuilds a fully constructed model;
    its total cost is included in build_seconds, with no guaranteed speedup.
    """
    if type(prune_unreachable) is not bool:
        raise ValueError("prune_unreachable must be a bool.")
    if timeout is not None and (
        isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or not isfinite(timeout) or timeout < 0
    ):
        raise ValueError("timeout must be finite and nonnegative, or None.")
    started = perf_counter()
    deadline = None if timeout is None else started + timeout
    model = None
    build_time = solve_time = verify_time = 0.0
    version = "not loaded"
    checks = 0

    def expired():
        return deadline is not None and perf_counter() >= deadline

    def finish(status, solution=None, reason="", reached=frozenset()):
        return PlanningResult(
            status=status, solution=solution,
            elapsed_seconds=perf_counter() - started,
            build_seconds=build_time, solver_seconds=solve_time,
            verification_seconds=verify_time,
            state_count=len(model.states) if model else 0,
            transition_count=len(model.edges) if model else 0,
            variable_count=model.variable_count if model else 0,
            constraint_count=model.constraint_count if model else 0,
            solver_version=version, reason=reason, solver_checks=checks,
            reachable_states=reached,
            pruning_enabled=prune_unreachable,
            unpruned_state_count=model.unpruned_state_count if model else 0,
            unpruned_transition_count=(
                model.unpruned_transition_count if model else 0
            ),
        )

    if expired():
        return finish("timeout", reason="Budget exhausted before modeling.")
    build_started = perf_counter()
    model = build_planning_model(puzzle, prune_unreachable=prune_unreachable)
    z3 = _load_z3()
    version = z3.get_version_string()
    if model_path is not None:
        path = Path(model_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(model.to_smt2(), encoding="utf-8")
    build_time = perf_counter() - build_started
    if expired():
        return finish("timeout", reason="Budget exhausted while modeling.")
    if deadline is not None:
        remaining_ms = max(1, ceil((deadline - perf_counter()) * 1000))
        model.solver.set(timeout=remaining_ms)

    solve_started = perf_counter()
    answer = model.solver.check()
    solve_time = perf_counter() - solve_started
    checks = 1
    if answer == z3.unknown:
        reason = model.solver.reason_unknown()
        status = (
            "timeout"
            if "timeout" in reason.lower() or expired()
            else "unknown"
        )
        return finish(status, reason=reason)
    if expired():
        return finish("timeout", reason="Budget exhausted during SMT check.")
    if answer == z3.unsat:
        return finish("no_solution", reason="The planning formula is UNSAT.")

    verification_started = perf_counter()
    assignment = model.solver.model()
    reverse_codes = {value: key for key, value in KIND_CODES.items()}
    placement = {}
    for position, variable in model.cell_vars.items():
        code = assignment.eval(variable, model_completion=True).as_long()
        if code != EMPTY:
            placement[position] = reverse_codes[code]
    reached = frozenset(
        state for state in model.states
        if z3.is_true(assignment.eval(model.reachable[state]))
    )
    result = simulate(puzzle, placement)
    replay_states = {
        (ray.x, ray.y, ray.vx, ray.vy) for ray in puzzle.lasers
    }
    replay_states.update(
        (end[0], end[1], end[0] - start[0], end[1] - start[1])
        for start, end in result.segments
    )
    projected_points = frozenset((x, y) for x, y, _, _ in reached)
    projected_segments = {
        (edge.source[:2], edge.target[:2]) for edge in model.edges
        if edge.source in reached
        and z3.is_true(assignment.eval(edge.condition))
    }
    if (not result.is_solution() or reached != replay_states
            or projected_points != result.visited_points
            or projected_segments != set(result.segments)):
        raise RuntimeError(
            "Constraint model failed independent ray verification."
        )
    verify_time = perf_counter() - verification_started
    if expired():
        return finish(
            "timeout", reason="Budget exhausted during verification."
        )
    return finish("solved", result, reached=reached)
