"""Directed 45-degree geometry for the target-driven constraint search.

The graph stores optimistic local transitions. Shared cell domains enforce
both collision behavior and clearance of the cell a reflected ray crosses.
Final layouts are checked by the team's block_laser simulator.
"""

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from block_laser import (
    Puzzle as Board, Position as Point, lazor as Ray, Segment,
    SimulationResult, simulate as simulate_layout,
)


DIRECTIONS = ((1, 1), (1, -1), (-1, 1), (-1, -1))
KINDS = "ABC"


class SearchTimeout(TimeoutError):
    """Search exhausted its time allowance without returning a solution."""


@dataclass
class SearchStats:
    """Counters from one first-solution search."""

    search_nodes: int = 0
    expanded_states: int = 0
    branches: int = 0
    pruned: int = 0
    verified_layouts: int = 0
    elapsed: float = 0.0


@dataclass(frozen=True)
class Solution:
    """A verified, fully placed board and its complete optical trace."""

    cells: Tuple[str, ...]
    trace: SimulationResult
    stats: SearchStats


class DiagonalGraph:
    """All legal 45-degree lines and their in-board edge-midpoint nodes.

    Each node has four directed states. Edges join adjacent intersections on
    a line; a long straight segment is therefore represented without making
    a dense all-pairs graph. Straight and reflected successors are computed
    once; the shared R/T truth table selects which edges each type supports.
    """

    def __init__(self, board: Board):
        self.board = board
        self.points = tuple(
            (x, y)
            for y in range(2 * board.height + 1)
            for x in range(2 * board.width + 1)
            if (x + y) % 2 == 1
        )
        self.point_ids = {point: index for index, point in enumerate(self.points)}
        lines: Dict[Tuple[int, int], List[Point]] = {}
        for x, y in self.points:
            lines.setdefault((1, y - x), []).append((x, y))
            lines.setdefault((-1, y + x), []).append((x, y))
        self.lines = {
            key: tuple(sorted(points)) for key, points in sorted(lines.items())
        }
        self.rays = tuple(
            Ray(x, y, vx, vy)
            for x, y in self.points
            for vx, vy in DIRECTIONS
        )
        self.state_ids = {ray: index for index, ray in enumerate(self.rays)}
        self.sources = tuple(self.state_ids[ray] for ray in board.lasers)
        self.target_bits = {
            point: 1 << index for index, point in enumerate(sorted(board.targets))
        }
        self.all_targets = (1 << len(self.target_bits)) - 1
        self.hits = tuple(self.target_bits.get(ray.position, 0) for ray in self.rays)
        self.ahead: List[Optional[int]] = []
        straight_next: List[Optional[int]] = []
        reflection_next: List[Optional[int]] = []
        for state, ray in enumerate(self.rays):
            if ray.x % 2 == 0:
                row = ray.y // 2
                column = ray.x // 2 - (ray.vx < 0)
                reflection_direction = (state & 3) ^ 2
            else:
                row = ray.y // 2 - (ray.vy < 0)
                column = ray.x // 2
                reflection_direction = (state & 3) ^ 1
            inside = 0 <= row < board.height and 0 <= column < board.width
            self.ahead.append(row * board.width + column if inside else None)
            # An outgoing boundary state has no cell in front of it. Do not
            # invent an outside reflector even if a reflected point is inside.
            if inside:
                direct_point = self.point_ids.get((ray.x + ray.vx, ray.y + ray.vy))
                rvx, rvy = DIRECTIONS[reflection_direction]
                reflected_point = self.point_ids.get((ray.x + rvx, ray.y + rvy))
                straight_next.append(
                    None if direct_point is None else 4 * direct_point + (state & 3)
                )
                reflection_next.append(
                    None if reflected_point is None else
                    4 * reflected_point + reflection_direction
                )
            else:
                straight_next.append(None)
                reflection_next.append(None)
        self.straight_next = tuple(straight_next)
        self.reflection_next = tuple(reflection_next)

    def line_for(self, ray: Ray) -> Tuple[int, int]:
        """Return (slope, intercept) for a directed ray's supporting line."""
        slope = ray.vy * ray.vx
        return slope, ray.y - slope * ray.x


def _validate_layout(board: Board, cells: Sequence[str]) -> Tuple[str, ...]:
    """Check that a candidate obeys fixed cells and the entire inventory."""
    layout = tuple(cells)
    if len(layout) != len(board.cells):
        raise ValueError("Layout has the wrong number of cells")
    counts = [0, 0, 0]
    for original, placed in zip(board.cells, layout):
        if original != "o":
            if placed != original:
                raise ValueError("A fixed or unavailable cell was changed")
        elif placed not in "oABC" or len(placed) != 1:
            raise ValueError("A movable cell must contain o, A, B, or C")
        elif placed in KINDS:
            counts[KINDS.index(placed)] += 1
    if tuple(counts) != board.inventory:
        raise ValueError("Layout does not use the specified movable inventory")
    return layout


def simulate(
    board: Board,
    cells: Sequence[str],
) -> SimulationResult:
    """Validate search output with the team's common laser simulator."""
    layout = _validate_layout(board, cells)
    placements = {
        (index % board.width, index // board.width): kind
        for index, (original, kind) in enumerate(zip(board.cells, layout))
        if original == "o" and kind in "ABC"
    }
    result = simulate_layout(board, placements)
    if not result.layout.complete:
        raise ValueError("Search output left movable blocks unplaced")
    return result
