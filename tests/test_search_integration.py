"""Compare the integrated target search with independent tiny-board checks."""

from itertools import combinations, product
from pathlib import Path
import random
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from block_laser import Puzzle, lazor, simulate
from file_io import parse_bff
from search import solve_dfs, solve_targets


def complete_placements(puzzle):
    """Enumerate distinct full placements without using the search module."""
    available = tuple(
        (column, row)
        for row, cells in enumerate(puzzle.grid)
        for column, kind in enumerate(cells)
        if kind == "o"
    )

    def assign(index, remaining, placement):
        if index == 3:
            yield dict(placement)
            return
        kind = "ABC"[index]
        for chosen in combinations(remaining, puzzle.block_counts.get(kind, 0)):
            next_placement = dict(placement)
            next_placement.update((position, kind) for position in chosen)
            yield from assign(
                index + 1,
                tuple(position for position in remaining if position not in chosen),
                next_placement,
            )

    yield from assign(0, available, {})


def reference_trace(puzzle, placements):
    """Trace geometry directly, including the material crossed after a turn.

    This implementation shares no graph, transition table, block interaction,
    or reachability code with either solver or the production simulator.
    """
    grid = [list(row) for row in puzzle.grid]
    for (column, row), kind in placements.items():
        grid[row][column] = kind

    def material(x, y, dx, dy):
        if x % 2 == 0:
            column = (x - 1) // 2 if dx < 0 else x // 2
            row = y // 2
        else:
            column = x // 2
            row = (y - 1) // 2 if dy < 0 else y // 2
        if 0 <= column < puzzle.width and 0 <= row < puzzle.height:
            return grid[row][column]
        return "o"

    pending = [(ray.x, ray.y, ray.vx, ray.vy) for ray in puzzle.lasers]
    seen = set()
    points = set()
    segments = set()
    while pending:
        state = pending.pop()
        if state in seen:
            continue
        seen.add(state)
        x, y, dx, dy = state
        points.add((x, y))
        kind = material(x, y, dx, dy)
        directions = []
        if kind in ("o", "x", "C"):
            directions.append((dx, dy))
        if kind in ("A", "C"):
            directions.append((-dx, dy) if x % 2 == 0 else (dx, -dy))
        for next_dx, next_dy in directions:
            if material(x, y, next_dx, next_dy) in ("A", "B"):
                continue
            endpoint = (x + next_dx, y + next_dy)
            if not (
                0 <= endpoint[0] <= 2 * puzzle.width
                and 0 <= endpoint[1] <= 2 * puzzle.height
            ):
                continue
            segments.add(((x, y), endpoint))
            pending.append((*endpoint, next_dx, next_dy))
    return frozenset(points), frozenset(segments)


class SearchIntegrationTests(unittest.TestCase):
    def assert_valid_solution(self, puzzle, result):
        self.assertEqual(result.status, "solved")
        solution = result.solution
        self.assertIsNotNone(solution)
        self.assertTrue(solution.is_solution)
        self.assertTrue(solution.layout.complete)
        self.assertFalse(solution.missed_targets)
        placements = solution.layout.placements
        for kind in "ABC":
            self.assertEqual(
                sum(placed == kind for placed in placements.values()),
                puzzle.block_counts.get(kind, 0),
            )
        for row, cells in enumerate(puzzle.grid):
            for column, original in enumerate(cells):
                placed = solution.layout.grid[row][column]
                if original != "o":
                    self.assertEqual(placed, original)
                    self.assertNotIn((column, row), placements)
                else:
                    self.assertEqual(placed, placements.get((column, row), "o"))
        points, segments = reference_trace(puzzle, placements)
        self.assertTrue(puzzle.targets <= points)
        self.assertEqual(solution.visited_points, points)
        self.assertEqual(frozenset(solution.segments), segments)

    def test_fixed_block_behind_reflection_blocks_the_outgoing_segment(self):
        cases = (
            ("B A", "2 1 1 1", "1 2"),
            ("A B", "2 1 -1 1", "3 2"),
            ("B\nA", "1 2 1 1", "2 1"),
            ("A\nB", "1 2 1 -1", "2 3"),
        )
        for grid, source, target in cases:
            puzzle = parse_bff(
                f"GRID START\n{grid}\nGRID STOP\nL {source}\nP {target}\n"
            )
            self.assertFalse(puzzle.targets <= reference_trace(puzzle, {})[0])
            for solve in (solve_dfs, solve_targets):
                with self.subTest(grid=grid, algorithm=solve.__name__):
                    result = solve(puzzle, timeout=5)
                    self.assertEqual(result.status, "no_solution")
                    self.assertIsNone(result.solution)

    def test_split_block_behind_reflection_allows_the_outgoing_segment(self):
        cases = (
            ("C A", "2 1 1 1", "1 2"),
            ("A C", "2 1 -1 1", "3 2"),
            ("C\nA", "1 2 1 1", "2 1"),
            ("A\nC", "1 2 1 -1", "2 3"),
        )
        for grid, source, target in cases:
            puzzle = parse_bff(
                f"GRID START\n{grid}\nGRID STOP\nL {source}\nP {target}\n"
            )
            for solve in (solve_dfs, solve_targets):
                with self.subTest(grid=grid, algorithm=solve.__name__):
                    self.assert_valid_solution(puzzle, solve(puzzle, timeout=5))

    def test_split_and_mirror_loop_terminates_without_losing_targets(self):
        puzzle = Puzzle(
            grid=(("o", "C", "o"), ("A", "o", "A"), ("o", "A", "o")),
            block_counts={"A": 0, "B": 0, "C": 0},
            lasers=(lazor(3, 2, 1, -1),),
            targets=frozenset({(4, 3), (3, 4), (2, 3), (4, 1)}),
        )
        self.assertGreater(simulate(puzzle).repeated_states, 0)
        self.assert_valid_solution(puzzle, solve_targets(puzzle, timeout=5))

    def test_simulator_matches_independent_geometry_for_all_2_by_2_layouts(self):
        for symbols in product("oABC", repeat=4):
            with self.subTest(grid="".join(symbols)):
                puzzle = Puzzle(
                    grid=(symbols[:2], symbols[2:]),
                    block_counts={"A": 0, "B": 0, "C": 0},
                    lasers=(lazor(0, 1, 1, 1), lazor(2, 1, 1, -1)),
                    targets=frozenset({(1, 0), (2, 3), (3, 4)}),
                )
                points, segments = reference_trace(puzzle, {})
                result = simulate(puzzle)
                self.assertEqual(result.visited_points, points)
                self.assertEqual(frozenset(result.segments), segments)
                self.assertEqual(result.hit_targets, puzzle.targets & points)

    def test_target_search_matches_complete_tiny_board_enumeration(self):
        fixtures = (
            ((("o", "o"), ("o", "o")), (1, 0, 0)),
            ((("o", "o"), ("o", "o")), (0, 1, 1)),
            ((("o", "o"), ("o", "o")), (1, 1, 1)),
            ((("o", "x", "o"), ("o", "A", "o")), (1, 1, 1)),
            ((("o", "o", "o"), ("o", "o", "o")), (1, 1, 1)),
        )
        randomizer = random.Random(20261111)
        solved = unsolved = 0
        for grid, inventory in fixtures:
            width, height = len(grid[0]), len(grid)
            lasers = (
                lazor(0, 1, 1, 1),
                lazor(2 * width, 2 * height - 1, -1, -1),
            )
            base = Puzzle(grid, dict(zip("ABC", inventory)), lasers, frozenset())
            possible_hits = tuple(
                reference_trace(base, placement)[0]
                for placement in complete_placements(base)
            )
            points = tuple(
                (x, y)
                for y in range(2 * height + 1)
                for x in range(2 * width + 1)
                if (x + y) % 2 == 1
                and (x, y) not in ((0, 1), (2 * width, 2 * height - 1))
            )
            queries = [frozenset({point}) for point in points]
            queries.extend(
                frozenset(randomizer.sample(points, min(3, len(points))))
                for _ in range(12)
            )
            queries.append(frozenset(points))
            for targets in queries:
                with self.subTest(grid=grid, inventory=inventory, targets=targets):
                    puzzle = Puzzle(grid, dict(base.block_counts), lasers, targets)
                    expected = any(targets <= hits for hits in possible_hits)
                    result = solve_targets(puzzle, timeout=5)
                    self.assertEqual(
                        result.status, "solved" if expected else "no_solution"
                    )
                    if expected:
                        solved += 1
                        self.assert_valid_solution(puzzle, result)
                    else:
                        unsolved += 1
                        self.assertIsNone(result.solution)
        self.assertGreater(solved, 0)
        self.assertGreater(unsolved, 0)

    def test_all_inventory_must_be_used_even_if_empty_board_hits_target(self):
        for kind, expected in (("A", False), ("B", False), ("C", True)):
            with self.subTest(kind=kind):
                puzzle = Puzzle(
                    grid=(("o",),),
                    block_counts={item: int(item == kind) for item in "ABC"},
                    lasers=(lazor(0, 1, 1, 1),),
                    targets=frozenset({(1, 2)}),
                )
                result = solve_targets(puzzle, timeout=5)
                self.assertEqual(
                    result.status, "solved" if expected else "no_solution"
                )
                if expected:
                    self.assert_valid_solution(puzzle, result)

    def test_zero_timeout_is_incomplete_search_not_no_solution(self):
        puzzle = parse_bff(
            "GRID START\no o\no o\nGRID STOP\n"
            "A 1\nL 0 1 1 1\nP 1 2\n"
        )
        for solve in (solve_dfs, solve_targets):
            with self.subTest(algorithm=solve.__name__):
                result = solve(puzzle, timeout=0)
                self.assertEqual(result.status, "timeout")
                self.assertIsNone(result.solution)
                self.assertGreaterEqual(result.elapsed_seconds, 0)


if __name__ == "__main__":
    unittest.main()
