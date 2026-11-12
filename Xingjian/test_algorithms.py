"""Check DFS, SMT, and safe source pruning against tiny-board ray truth.

Run from this folder: python -B -m unittest -v test_algorithms
The oracle enumerates all labels on four cells and simulates every legal
assignment; it shares no search recursion or symbolic model with a solver.
"""

from collections import Counter
from itertools import product
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from xml.etree import ElementTree as ET

from block_laser import simulate
from constraint_solver import (
    _load_z3,
    build_planning_model,
    solve_constraints,
)
from file_io import load_bff, parse_bff
from output import format_report, write_bff, write_report, write_svg
from search import solve_dfs


SPLITTER_BOARD = (
    "GRID START\no o\no o\nGRID STOP\n"
    "C 1\nL 2 1 -1 1\nP 1 2\nP 3 2\n"
)
BLOCKED_BOARD = "GRID START\nB\nGRID STOP\nL 0 1 1 1\nP 1 2\n"


def make_puzzle(rows, inventory, sources, targets):
    """Use the public parser to construct a small, readable scenario."""
    records = ["GRID START", *rows, "GRID STOP"]
    records.extend(f"{kind} {count}" for kind, count in inventory.items())
    records.extend("L " + " ".join(map(str, ray)) for ray in sources)
    records.extend(f"P {x} {y}" for x, y in targets)
    return parse_bff("\n".join(records))


def traced_states(puzzle, simulation):
    """Reconstruct the processed states from sources and segment arrivals."""
    states = {
        (ray.x, ray.y, ray.vx, ray.vy) for ray in puzzle.lasers
    }
    states.update(
        (end[0], end[1], end[0] - start[0], end[1] - start[1])
        for start, end in simulation.segments
    )
    return frozenset(states)


def enumerate_ray_truth(puzzle):
    """Simulate every legal full assignment and return all valid placements."""
    cells = puzzle.placeable_positions
    solutions = set()
    tested = 0
    for labels in product(("o", "A", "B", "C"), repeat=len(cells)):
        counts = Counter(labels)
        if any(
            counts[kind] != puzzle.block_counts[kind]
            for kind in ("A", "B", "C")
        ):
            continue
        placement = {
            cell: kind for cell, kind in zip(cells, labels) if kind != "o"
        }
        tested += 1
        simulation = simulate(puzzle, placement)
        if simulation.is_solution():
            solutions.add(tuple(sorted(placement.items())))
    return solutions, tested


def algorithm_results(puzzle):
    """Run each implementation without a timeout on these bounded cases."""
    return (
        ("dfs", solve_dfs(puzzle, timeout=None)),
        ("smt", solve_constraints(puzzle, timeout=None)),
        (
            "smt-pruned",
            solve_constraints(puzzle, timeout=None, prune_unreachable=True),
        ),
    )


class AlgorithmTests(unittest.TestCase):
    """Require physical replay and complete-enumeration agreement."""

    def assert_valid_replay(self, puzzle, outcome):
        self.assertEqual(outcome.status, "solved")
        self.assertIsNotNone(outcome.solution)
        result = outcome.solution
        self.assertTrue(result.is_solution())
        placement = result.layout.placements
        counts = Counter(placement.values())
        for kind in ("A", "B", "C"):
            self.assertEqual(counts[kind], puzzle.block_counts[kind])
        for column, row in placement:
            self.assertEqual(puzzle.grid[row][column], "o")
        for row, cells in enumerate(puzzle.grid):
            for column, cell in enumerate(cells):
                if cell != "o":
                    self.assertEqual(result.layout.grid[row][column], cell)
        replay = simulate(puzzle, dict(placement))
        self.assertTrue(replay.is_solution())
        self.assertEqual(replay.hit_targets, puzzle.targets)
        self.assertEqual(set(replay.segments), set(result.segments))
        if hasattr(outcome, "reachable_states"):
            self.assertEqual(
                outcome.reachable_states, traced_states(puzzle, replay)
            )

    def test_all_algorithms_match_every_legal_two_by_two_assignment(self):
        scenarios = (
            ({"A": 1}, [(0, 1, 1, 1)], [(3, 4)]),
            ({"A": 1}, [(0, 1, 1, 1)], [(4, 1)]),
            ({"A": 2}, [(0, 1, 1, 1)], [(3, 4)]),
            ({"B": 1, "C": 1}, [(0, 1, 1, 1)], [(1, 2)]),
            ({"A": 1, "C": 1}, [(0, 1, 1, 1)], [(1, 2), (3, 4)]),
            ({"A": 2, "C": 1}, [(0, 1, 1, 1)], [(3, 0), (0, 3)]),
            ({"B": 4}, [(0, 1, 1, 1)], [(3, 4)]),
            ({"C": 2}, [(2, 1, -1, 1)], [(1, 2), (3, 2)]),
            ({}, [(0, 1, 1, 1)], [(1, 1)]),
        )
        for inventory, sources, targets in scenarios:
            puzzle = make_puzzle(
                ["o o", "o o"], inventory, sources, targets
            )
            original = (puzzle.grid, dict(puzzle.block_counts))
            solutions, tested = enumerate_ray_truth(puzzle)
            self.assertGreater(tested, 0)
            expected_status = "solved" if solutions else "no_solution"
            for name, outcome in algorithm_results(puzzle):
                with self.subTest(
                    inventory=inventory, targets=targets, algorithm=name
                ):
                    self.assertEqual(outcome.status, expected_status)
                    if solutions:
                        self.assert_valid_replay(puzzle, outcome)
                        self.assertIn(
                            tuple(sorted(outcome.solution.layout.placements.items())),
                            solutions,
                        )
                    else:
                        self.assertIsNone(outcome.solution)
                    self.assertEqual(original, (puzzle.grid, puzzle.block_counts))

    def test_mixed_fixed_cells_and_multiple_sources_match_full_enumeration(self):
        puzzle = make_puzzle(
            ["A x", "o o"], {"C": 1},
            [(0, 1, -1, -1), (2, 3, 1, -1)], [(0, 1), (3, 2)],
        )
        solutions, tested = enumerate_ray_truth(puzzle)
        self.assertEqual(tested, 2)
        for name, outcome in algorithm_results(puzzle):
            with self.subTest(algorithm=name):
                self.assertEqual(
                    outcome.status, "solved" if solutions else "no_solution"
                )
                if solutions:
                    self.assert_valid_replay(puzzle, outcome)

    def test_source_connected_mirror_cycle_is_allowed(self):
        puzzle = make_puzzle(
            ["x A x", "A x A", "x A x"], {},
            [(3, 2, 1, -1)], [(3, 2), (4, 3), (3, 4), (2, 3)],
        )
        for name, outcome in algorithm_results(puzzle):
            with self.subTest(algorithm=name):
                self.assert_valid_replay(puzzle, outcome)
                self.assertGreater(outcome.solution.repeated_states, 0)

    def test_isolated_mirror_cycle_cannot_create_its_own_light(self):
        puzzle = make_puzzle(
            ["x A x", "A x A", "x A x"], {},
            [(0, 1, -1, -1)], [(3, 2)],
        )
        for name, outcome in algorithm_results(puzzle):
            with self.subTest(algorithm=name):
                self.assertEqual(outcome.status, "no_solution")
                self.assertIsNone(outcome.solution)

    def test_single_and_adjacent_splitters_preserve_all_branches(self):
        cases = (
            (
                ["x x x", "x C x", "x x x"],
                [(2, 3, 1, 1)], [(5, 6), (0, 5)],
                {((2, 3), (3, 4)), ((2, 3), (1, 4))},
            ),
            (
                ["x C x", "x C x", "x x x"],
                [(2, 1, 1, 1)], [(6, 5), (5, 0), (0, 3)],
                {((3, 2), (4, 3)), ((3, 2), (4, 1))},
            ),
        )
        for rows, sources, targets, required_segments in cases:
            puzzle = make_puzzle(rows, {}, sources, targets)
            for name, outcome in algorithm_results(puzzle):
                with self.subTest(rows=rows, algorithm=name):
                    self.assert_valid_replay(puzzle, outcome)
                    self.assertTrue(
                        required_segments.issubset(outcome.solution.segments)
                    )


class PruningAndStatusTests(unittest.TestCase):
    """Check state pruning and distinguish incomplete solving from UNSAT."""

    def test_fixed_board_pruning_keeps_exact_physical_source_reachability(self):
        z3 = _load_z3()
        cases = (
            (["x x", "x x"], [(0, 1, -1, -1)], [(0, 1)]),
            (
                ["x A x", "A x A", "x A x"],
                [(3, 2, 1, -1)], [(3, 2), (4, 3)],
            ),
            (
                ["x C x", "x C x", "x x x"],
                [(2, 1, 1, 1)], [(6, 5), (5, 0), (0, 3)],
            ),
        )
        for rows, sources, targets in cases:
            puzzle = make_puzzle(rows, {}, sources, targets)
            with self.subTest(rows=rows):
                full = build_planning_model(puzzle)
                pruned = build_planning_model(puzzle, prune_unreachable=True)
                physical = traced_states(puzzle, simulate(puzzle))
                self.assertEqual(frozenset(pruned.states), physical)
                self.assertLess(len(pruned.states), len(full.states))
                self.assertLess(len(pruned.edges), len(full.edges))
                self.assertEqual(pruned.solver.check(), z3.sat)
                assignment = pruned.solver.model()
                active = frozenset(
                    state for state, symbol in pruned.reachable.items()
                    if z3.is_true(assignment.eval(symbol, model_completion=True))
                )
                self.assertEqual(active, physical)

                plan = solve_constraints(
                    puzzle, timeout=None, prune_unreachable=True
                )
                self.assertEqual(plan.status, "solved")
                self.assertTrue(plan.pruning_enabled)
                self.assertEqual(plan.state_count, len(pruned.states))
                self.assertEqual(plan.transition_count, len(pruned.edges))
                self.assertEqual(plan.unpruned_state_count, len(full.states))
                self.assertEqual(plan.unpruned_transition_count, len(full.edges))
                self.assertEqual(plan.reachable_states, physical)

    def test_zero_timeout_is_not_a_no_solution_proof(self):
        puzzle = parse_bff(SPLITTER_BOARD)
        outcomes = (
            solve_dfs(puzzle, timeout=0),
            solve_constraints(puzzle, timeout=0),
            solve_constraints(puzzle, timeout=0, prune_unreachable=True),
        )
        for outcome in outcomes:
            with self.subTest(type=type(outcome).__name__):
                self.assertEqual(outcome.status, "timeout")
                self.assertIsNone(outcome.solution)
                self.assertFalse(outcome.solved)

    def test_unknown_does_not_become_a_no_solution_proof(self):
        z3 = _load_z3()
        puzzle = parse_bff(SPLITTER_BOARD)
        for pruning in (False, True):
            with self.subTest(pruning=pruning):
                model = build_planning_model(puzzle, prune_unreachable=pruning)
                with patch("constraint_solver.build_planning_model", return_value=model):
                    with patch.object(model.solver, "check", return_value=z3.unknown):
                        with patch.object(
                            model.solver, "reason_unknown",
                            return_value="synthetic incomplete result",
                        ):
                            plan = solve_constraints(
                                puzzle, timeout=None, prune_unreachable=pruning
                            )
                self.assertEqual(plan.status, "unknown")
                self.assertIsNone(plan.solution)
                self.assertFalse(plan.solved)
                self.assertIn("synthetic incomplete", plan.reason)

    def test_both_exported_smt_models_reload_as_sat_or_unsat(self):
        z3 = _load_z3()
        with tempfile.TemporaryDirectory() as directory:
            for pruning in (False, True):
                for name, text, status, expected in (
                    ("sat", SPLITTER_BOARD, "solved", z3.sat),
                    ("unsat", BLOCKED_BOARD, "no_solution", z3.unsat),
                ):
                    with self.subTest(pruning=pruning, case=name):
                        model_path = Path(directory) / f"{name}-{pruning}.smt2"
                        plan = solve_constraints(
                            parse_bff(text), timeout=None,
                            model_path=model_path, prune_unreachable=pruning,
                        )
                        self.assertEqual(plan.status, status)
                        self.assertTrue(model_path.is_file())
                        restored = z3.Solver()
                        restored.from_file(str(model_path))
                        self.assertEqual(restored.check(), expected)


class OutputAndCommandTests(unittest.TestCase):
    """Keep result labels and saved artifacts aligned with the algorithm."""

    def test_all_algorithm_outputs_replay_and_name_their_actual_algorithm(self):
        puzzle = parse_bff(SPLITTER_BOARD)
        with tempfile.TemporaryDirectory() as directory:
            for name, outcome in algorithm_results(puzzle):
                with self.subTest(algorithm=name):
                    self.assertEqual(outcome.status, "solved")
                    result = outcome.solution
                    folder = Path(directory) / name
                    report = format_report(result, search=outcome)
                    if name == "dfs":
                        self.assertIn("(DFS)", report)
                        self.assertNotIn("Algorithm: SMT", report)
                    else:
                        self.assertIn("Algorithm: SMT", report)
                        self.assertIn(
                            f"Source pruning enabled: {name == 'smt-pruned'}",
                            report,
                        )
                    report_path = write_report(
                        result, folder / "solution.txt", search=outcome
                    )
                    svg_path = write_svg(
                        result, folder / "solution.svg", search=outcome
                    )
                    bff_path = write_bff(
                        result, folder / "solution.bff", search=outcome
                    )
                    self.assertEqual(report_path.read_text("utf-8"), report)
                    restored = load_bff(bff_path)
                    replay = simulate(restored)
                    self.assertTrue(replay.is_solution())
                    self.assertEqual(replay.segments, result.segments)
                    self.assertEqual(replay.hit_targets, result.hit_targets)
                    root = ET.parse(svg_path).getroot()
                    rays = root.findall(
                        "s:g[@id='rays']/s:line",
                        {"s": "http://www.w3.org/2000/svg"},
                    )
                    self.assertEqual(len(rays), len(result.segments))

    def test_cli_algorithms_have_distinct_success_unsat_and_timeout_exits(self):
        entry = Path(__file__).resolve().with_name("run_algorithms.py")
        self.assertTrue(entry.is_file())
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            for algorithm in ("dfs", "smt", "smt-pruned"):
                for case, text, timeout, expected_code in (
                    ("solved", SPLITTER_BOARD, "20", 0),
                    ("blocked", BLOCKED_BOARD, "20", 1),
                    ("timeout", SPLITTER_BOARD, "0", 2),
                ):
                    with self.subTest(algorithm=algorithm, case=case):
                        board = folder / f"{algorithm}-{case}.bff"
                        board.write_text(text, encoding="utf-8")
                        destination = folder / f"out-{algorithm}-{case}"
                        command = [
                            sys.executable, "-B", str(entry), str(board),
                            "--algorithm", algorithm,
                            "--output-dir", str(destination),
                            "--timeout", timeout,
                        ]
                        model_path = destination / "formula.smt2"
                        if algorithm != "dfs" and case == "solved":
                            command.extend(["--model", str(model_path)])
                        completed = subprocess.run(
                            command, cwd=folder, capture_output=True,
                            text=True, timeout=30,
                        )
                        self.assertEqual(
                            completed.returncode, expected_code,
                            completed.stdout + completed.stderr,
                        )
                        if expected_code == 0:
                            for suffix in (".txt", ".svg", ".bff"):
                                self.assertTrue(list(destination.glob(f"*{suffix}")))
                            if algorithm != "dfs":
                                self.assertTrue(model_path.is_file())
                        else:
                            self.assertFalse(list(destination.glob("*.bff")))


if __name__ == "__main__":
    unittest.main()
