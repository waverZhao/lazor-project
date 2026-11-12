"""Connect target Xs to the sources using shared block-behaviour constraints.

A required straight edge restricts its cell to empty/C; a required reflected
edge restricts it to A/C. Intersections of those domains keep every selected
route compatible with one global board. A target-driven search connects the
most constrained uncovered target to an already guaranteed optical path.
"""

from dataclasses import dataclass
from heapq import heappop, heappush
from time import monotonic
from typing import Dict, Iterator, List, Optional, Sequence, Tuple

from constraints import DomainStore, REFLECTION, STRAIGHT
from block_laser import Puzzle as Board, Position as Point
from search_graph import (
    DiagonalGraph, SearchStats, SearchTimeout, Solution, simulate,
)


@dataclass
class TargetStats(SearchStats):
    """Counters for goal choices, candidate routes, and domain propagation."""

    target_choices: int = 0
    route_attempts: int = 0
    domain_updates: int = 0
    failed_cache_hits: int = 0


class TargetSolver:
    """First-solution constraint search driven by each target's possible X arms.

    Geometric reachability is a necessary test, not a sufficient proof. Search
    proves a target connected only after a consistent sequence of required
    edges has been joined to the guaranteed source-connected component.
    """

    def __init__(self, board: Board, prune: bool = True):
        self.board = board
        self.graph = DiagonalGraph(board)
        self.domains = DomainStore(board)
        self.prune = prune
        self.stats = TargetStats()
        self._deadline = float("inf")
        self._failed = set()
        self.target_states: Dict[Point, Tuple[int, ...]] = {
            point: tuple(4 * self.graph.point_ids[point] + direction for direction in range(4))
            for point in sorted(board.targets)
        }
        self.target_lines = {
            (x, y): ((1, y - x), (-1, y + x)) for x, y in sorted(board.targets)
        }
        self.line_targets: Dict[Tuple[int, int], int] = {}
        for point, lines in self.target_lines.items():
            for line in lines:
                self.line_targets[line] = (
                    self.line_targets.get(line, 0) | self.graph.target_bits[point]
                )

        # Reflection also crosses the cell behind the collision face. That
        # cell must transmit, matching block_laser.simulate's clearance rule.
        # Edges are (successor, collision cell, support, turn, travel cell).
        self.edges: List[List[Tuple[int, int, int, int, Optional[int]]]] = [
            [] for _ in self.graph.rays
        ]
        self.reverse: List[List[Tuple[int, int, int, int, Optional[int]]]] = [
            [] for _ in self.graph.rays
        ]
        for state, cell in enumerate(self.graph.ahead):
            if cell is None:
                continue
            for successor, support, turn in (
                (self.graph.straight_next[state], STRAIGHT, 0),
                (self.graph.reflection_next[state], REFLECTION, 1),
            ):
                if successor is not None:
                    flipped = 2 if self.graph.rays[state].x % 2 == 0 else 1
                    travel = self.graph.ahead[state ^ flipped] if turn else None
                    self.edges[state].append(
                        (successor, cell, support, turn, travel)
                    )
                    self.reverse[successor].append(
                        (state, cell, support, turn, travel)
                    )

    def solve(self, timeout: Optional[float] = 60.0) -> Optional[Solution]:
        """Return the first fully verified solution, or None after exhaustion."""
        started = monotonic()
        self._deadline = started + timeout if timeout is not None else float("inf")
        self.stats = TargetStats()
        self._failed = set()
        try:
            self._check_deadline()
            initial = self.domains.propagate(self.domains.initial, strong=self.prune)
            if initial is None:
                return None
            return self._search(initial)
        finally:
            self.stats.elapsed = monotonic() - started

    def _check_deadline(self) -> None:
        if monotonic() >= self._deadline:
            raise SearchTimeout("No solution returned before the time limit")

    @staticmethod
    def _allowed(domains, cell, support, travel, guaranteed=False):
        """Check collision and reflection clearance under shared domains."""
        requirements = ((cell, support),) if travel is None else (
            (cell, support), (travel, STRAIGHT),
        )
        if guaranteed:
            return all(not domains[index] & ~mask
                       for index, mask in requirements)
        return all(domains[index] & mask for index, mask in requirements)

    def _reachable(self, domains: Sequence[int], guaranteed: bool) -> Tuple[int, int]:
        """Return reachable state/target masks under all or some allowed types."""
        pending = list(self.graph.sources)
        seen = 0
        hits = 0
        while pending:
            state = pending.pop()
            bit = 1 << state
            if seen & bit:
                continue
            seen |= bit
            hits |= self.graph.hits[state]
            for successor, cell, support, _, travel in self.edges[state]:
                if self._allowed(domains, cell, support, travel, guaranteed):
                    pending.append(successor)
        return seen, hits

    def _distances(self, domains: Sequence[int], connected: int) -> List[int]:
        """Lower bounds for weighted route cost, not for unique block counts."""
        unreachable = 10 ** 12
        distance = [unreachable] * len(self.graph.rays)
        queue = []
        mask = connected
        while mask:
            bit = mask & -mask
            state = bit.bit_length() - 1
            distance[state] = 0
            heappush(queue, (0, state))
            mask ^= bit
        while queue:
            cost, state = heappop(queue)
            if distance[state] != cost:
                continue
            for successor, cell, support, turn, travel in self.edges[state]:
                if not self._allowed(domains, cell, support, travel):
                    continue
                candidate = cost + 1 + 3 * turn
                if candidate < distance[successor]:
                    distance[successor] = candidate
                    heappush(queue, (candidate, successor))
        return distance

    def _choose_target(
        self, possible: int, hits: int, distance: Sequence[int]
    ) -> Optional[Tuple[Point, Tuple[int, ...]]]:
        missing = self.graph.all_targets ^ hits
        choices = []
        for point, states in self.target_states.items():
            if not self.graph.target_bits[point] & missing:
                continue
            arrivals = tuple(state for state in states if possible & (1 << state))
            if not arrivals:
                return None
            companions = sum(
                bin(self.line_targets[line] & missing).count("1")
                for line in self.target_lines[point]
            )
            # A common diagonal can usually be swept later in one pass. First
            # connect isolated targets, and among ties look farther ahead rather
            # than repeatedly collecting the nearest easy goal. Arrival count
            # is a tie-breaker, not proof that the goal must be visited early.
            priority = (
                companions, -min(distance[state] for state in arrivals), len(arrivals), point
            )
            choices.append((priority, point, arrivals))
        if not choices:
            return None
        _, point, arrivals = min(choices)
        return point, tuple(sorted(arrivals, key=lambda state: (distance[state], state)))

    def _routes(
        self,
        arrivals: Sequence[int],
        domains: Tuple[int, ...],
        connected: int,
        possible: int,
        distance: Sequence[int],
    ) -> Iterator[Tuple[int, ...]]:
        """Enumerate consistent reverse routes with an explicit stack.

        A route may use target-free intermediate lines. Once it touches an
        already guaranteed state, all its edges are guaranteed in the returned
        domains. A directed-state simple path suffices for reachability.
        """
        yielded = set()

        def candidates(state: int, current: Sequence[int], visited: int):
            options = [
                edge for edge in self.reverse[state]
                if possible & (1 << edge[0])
                and not visited & (1 << edge[0])
                and self._allowed(current, edge[1], edge[2], edge[4])
            ]
            options.sort(key=lambda edge: (
                distance[edge[0]] + 1 + 3 * edge[3],
                edge[3], distance[edge[0]], edge[0],
            ))
            return iter(options)

        # Iterative cost bounds keep one arrival's long detours from starving
        # short routes through another arm of the target's X. The cost is
        # ordering only; increasing bounds eventually admit every simple path.
        limit = min(distance[state] for state in arrivals)
        while True:
            next_limit = float("inf")
            for arrival in arrivals:
                if distance[arrival] > limit:
                    next_limit = min(next_limit, distance[arrival])
                    continue
                visited = 1 << arrival
                stack = [(arrival, domains, visited, 0,
                          candidates(arrival, domains, visited))]
                while stack:
                    self.stats.expanded_states += 1
                    if self.stats.expanded_states % 64 == 0:
                        self._check_deadline()
                    state, current, visited, cost, options = stack[-1]
                    if connected & (1 << state):
                        stack.pop()
                        if current not in yielded:
                            yielded.add(current)
                            self.stats.route_attempts += 1
                            yield current
                        continue
                    try:
                        predecessor, cell, support, turn, travel = next(options)
                    except StopIteration:
                        stack.pop()
                        continue
                    next_cost = cost + 1 + 3 * turn
                    estimate = next_cost + distance[predecessor]
                    if estimate > limit:
                        next_limit = min(next_limit, estimate)
                        continue
                    narrowed = self.domains.restrict(current, cell, support)
                    if narrowed is not None and travel is not None:
                        narrowed = self.domains.restrict(
                            narrowed, travel, STRAIGHT,
                        )
                    if narrowed is None:
                        self.stats.pruned += 1
                        continue
                    if narrowed != current:
                        self.stats.domain_updates += 1
                    next_visited = visited | (1 << predecessor)
                    stack.append((
                        predecessor, narrowed, next_visited, next_cost,
                        candidates(predecessor, narrowed, next_visited),
                    ))
            if next_limit == float("inf"):
                return
            limit = next_limit

    def _search(self, domains: Tuple[int, ...]) -> Optional[Solution]:
        self._check_deadline()
        self.stats.search_nodes += 1
        if domains in self._failed:
            self.stats.failed_cache_hits += 1
            return None
        connected, hits = self._reachable(domains, guaranteed=True)
        if hits == self.graph.all_targets:
            cells = self.domains.complete(domains)
            if cells is None:
                self.stats.pruned += 1
                return None
            self.stats.verified_layouts += 1
            trace = simulate(self.board, cells)
            if not self.board.targets.issubset(trace.hit_targets):
                raise AssertionError("A required target route was not preserved by completion")
            return Solution(cells, trace, self.stats)
        possible, possible_hits = self._reachable(domains, guaranteed=False)
        if possible_hits != self.graph.all_targets:
            self.stats.pruned += 1
            return None
        distance = self._distances(domains, connected)
        selected = self._choose_target(possible, hits, distance)
        if selected is None:
            self.stats.pruned += 1
            return None
        _, arrivals = selected
        self.stats.target_choices += 1
        for candidate in self._routes(arrivals, domains, connected, possible, distance):
            candidate = self.domains.propagate(candidate, strong=self.prune)
            if candidate is None:
                self.stats.pruned += 1
                continue
            if candidate == domains:
                raise AssertionError("Connecting an uncovered target made no domain progress")
            self.stats.branches += 1
            result = self._search(candidate)
            if result is not None:
                return result
        if len(self._failed) < 10000:
            self._failed.add(domains)
        return None
