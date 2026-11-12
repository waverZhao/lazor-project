"""Shared placement-search API with optimized and exhaustive algorithms.

The baseline visits A/B/C placement combinations and simulates complete
layouts. The optimized entry point connects target Xs under shared R/T
domains. Both return the same verified result to the output module.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from itertools import combinations
from math import isfinite
from time import perf_counter
from typing import Literal

from block_laser import Puzzle, SimulationResult, simulate
from search_graph import SearchTimeout
from target_solver import TargetSolver


@dataclass(frozen=True)
class DFSResult:
    """Search status, optional solution, elapsed time, and search counters."""

    status: Literal["solved", "no_solution", "timeout"]
    solution: SimulationResult | None
    elapsed_seconds: float
    candidates_tested: int
    states_expanded: int
    ray_states_processed: int
    reason: str
    algorithm: str = "dfs"

    @property
    def solved(self) -> bool:
        """Whether a complete placement passed the common ray simulator."""
        return self.status == "solved"


def solve_dfs(
    puzzle: Puzzle,
    *,
    timeout: float | None = 110.0,
    progress: Callable[[dict], None] | None = None,
) -> DFSResult:
    """Enumerate each distinct full layout at most once, depth first.

    The time budget includes input validation, enumeration, and simulation.
    It is checked between search operations, so one simulation or progress
    callback can run beyond the deadline. The default 110 seconds does not
    guarantee a solution for every board; None allows unlimited search.

    Counters cover complete candidate simulations and partial search nodes;
    the initial geometry-validation trace is not a candidate. The callback
    receives a progress dictionary approximately every five seconds. Timeout
    means incomplete enumeration, never a proof that the puzzle has no answer.
    """
    if timeout is not None and (
        isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or not isfinite(timeout) or timeout < 0
    ):
        raise ValueError("timeout must be finite and nonnegative, or None.")

    started = perf_counter()
    deadline = None if timeout is None else started + timeout
    last_progress = started
    candidates = partial_nodes = ray_states = 0
    solution = None
    timed_out = False

    def checkpoint():
        nonlocal timed_out, last_progress
        now = perf_counter()
        if deadline is not None and now >= deadline:
            timed_out = True
        if progress is not None and now - last_progress >= 5.0:
            progress({
                "elapsed_seconds": now - started,
                "candidates_tested": candidates,
                "states_expanded": partial_nodes,
                "ray_states_processed": ray_states,
            })
            last_progress = now
        return timed_out

    def finish():
        status = "timeout" if timed_out else (
            "solved" if solution is not None else "no_solution"
        )
        reason = (
            "Time budget exhausted before enumeration completed."
            if timed_out else (
                "" if solution is not None
                else "Every distinct complete layout was checked."
            )
        )
        return DFSResult(
            status, solution, perf_counter() - started,
            candidates, partial_nodes, ray_states, reason,
        )

    if checkpoint():
        return finish()
    counts = puzzle.block_counts
    if any(type(counts.get(kind, 0)) is not int
           or counts.get(kind, 0) < 0 for kind in ("A", "B", "C")):
        raise ValueError(
            "Movable inventory must contain nonnegative integers."
        )
    positions = puzzle.placeable_positions
    if sum(counts.values()) > len(positions):
        raise ValueError("Movable inventory exceeds available cells.")
    if not puzzle.lasers or not puzzle.targets:
        raise ValueError("At least one laser and target are required.")
    simulate(puzzle)
    groups = tuple(
        (kind, counts.get(kind, 0)) for kind in ("A", "B", "C")
        if counts.get(kind, 0)
    )
    placement = {}

    def visit(group_index, available):
        nonlocal candidates, partial_nodes, ray_states, solution
        if checkpoint():
            return
        if group_index == len(groups):
            candidates += 1
            result = simulate(puzzle, placement)
            ray_states += result.states_processed
            if checkpoint():
                return
            if result.is_solution:
                solution = result
            return

        partial_nodes += 1
        kind, count = groups[group_index]
        for chosen in combinations(available, count):
            for position in chosen:
                placement[position] = kind
            # The final kind needs no remaining-position allocation.
            remaining = () if group_index + 1 == len(groups) else tuple(
                position for position in available if position not in chosen
            )
            visit(group_index + 1, remaining)
            for position in chosen:
                del placement[position]
            if solution is not None or timed_out:
                return

    visit(0, positions)
    return finish()


def solve_targets(
    puzzle: Puzzle,
    *,
    timeout: float | None = 60.0,
    progress: Callable[[dict], None] | None = None,
) -> DFSResult:
    """Connect target Xs with R/T, inventory propagation, and backtracking.

    Preserve the common result fields used by the reader/output teammate.
    The time budget includes validation, graph construction and final replay.
    Progress callbacks receive counters when the search checks its deadline.
    """
    if timeout is not None and (
        isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or not isfinite(timeout) or timeout < 0
    ):
        raise ValueError("timeout must be finite and nonnegative, or None.")
    started = perf_counter()
    deadline = float("inf") if timeout is None else started + timeout
    solver = None
    solution = None
    reason = ""
    status = "no_solution"
    last_progress = started

    def checkpoint():
        nonlocal last_progress
        now = perf_counter()
        if now >= deadline:
            raise SearchTimeout("Time budget exhausted before search completed.")
        if progress is not None and now - last_progress >= 5.0:
            progress({
                "elapsed_seconds": now - started,
                "candidates_tested": solver.stats.verified_layouts if solver else 0,
                "states_expanded": solver.stats.expanded_states if solver else 0,
                "ray_states_processed": 0,
            })
            last_progress = now

    try:
        checkpoint()
        counts = puzzle.block_counts
        if any(type(counts.get(kind, 0)) is not int
               or counts.get(kind, 0) < 0 for kind in "ABC"):
            raise ValueError("Movable inventory must be nonnegative integers.")
        if sum(counts.values()) > len(puzzle.placeable_positions):
            raise ValueError("Movable inventory exceeds available cells.")
        if not puzzle.lasers or not puzzle.targets:
            raise ValueError("At least one laser and target are required.")
        if any(
            not (0 <= x <= 2 * puzzle.width and 0 <= y <= 2 * puzzle.height)
            or (x + y) % 2 != 1 for x, y in puzzle.targets
        ):
            raise ValueError("Targets must be face midpoints inside the board.")
        simulate(puzzle)
        checkpoint()
        solver = TargetSolver(puzzle)
        checkpoint()
        solver._check_deadline = checkpoint
        result = solver.solve(timeout=None)
        checkpoint()
        if result is not None:
            solution = result.trace
            checkpoint()
            if not solution.is_solution:
                raise AssertionError("Common simulator rejected search output")
            status = "solved"
        else:
            reason = "All consistent target routes were exhausted."
    except SearchTimeout as exc:
        status = "timeout"
        solution = None
        reason = str(exc)
    stats = solver.stats if solver is not None else None
    return DFSResult(
        status=status,
        solution=solution,
        elapsed_seconds=perf_counter() - started,
        candidates_tested=stats.verified_layouts if stats else 0,
        states_expanded=stats.expanded_states if stats else 0,
        ray_states_processed=solution.states_processed if solution else 0,
        reason=reason,
        algorithm="targets",
    )
