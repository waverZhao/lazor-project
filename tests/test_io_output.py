"""Check the shared reader, result exports, and command-line integration."""

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from xml.etree import ElementTree as ET

from block_laser import simulate
from file_io import BFFParseError, load_bff, parse_bff
from output import format_report, write_bff, write_report, write_svg


BFF = """GRID START
o o
o o
GRID STOP
A 1
L 0 1 1 1
P 1 2
"""


class FileIOTests(unittest.TestCase):
    def test_comments_bom_and_records_before_grid(self):
        puzzle = parse_bff(
            "\ufeff# one source and target\n"
            "A 1 # movable inventory\nL 0 1 1 1\nP 1 2\n"
            "GRID START\no A\nx C\nGRID STOP\n"
        )
        self.assertEqual(puzzle.grid, (("o", "A"), ("x", "C")))
        self.assertEqual(puzzle.block_counts, {"A": 1, "B": 0, "C": 0})
        self.assertEqual(puzzle.placeable_positions, ((0, 0),))
        self.assertEqual(set(puzzle.fixed_blocks), {(1, 0), (1, 1)})
        self.assertTrue(all(
            block.fixed for block in puzzle.fixed_blocks.values()
        ))

    def test_rejects_malformed_input_with_source(self):
        cases = (
            BFF.replace("o o\no o", "o o\no"),
            BFF.replace("A 1", "A 1\nA 1"),
            BFF.replace("A 1", "A -1"),
            BFF.replace("A 1", "A 5"),
            BFF.replace("L 0 1 1 1", "L 0 1 0 1"),
            BFF.replace("L 0 1 1 1", "L 0 0 1 1"),
            BFF.replace("P 1 2", "P 1 1"),
            BFF.replace("P 1 2", "P 5 2"),
            BFF.replace("GRID STOP", ""),
            BFF.replace("P 1 2", ""),
            BFF + "Q 1 2\n",
        )
        for text in cases:
            with self.subTest(text=text):
                with self.assertRaises(BFFParseError) as caught:
                    parse_bff(text, source="bad.bff")
                self.assertIn("bad.bff", str(caught.exception))

    def test_loads_utf8_bom_and_reports_bad_encoding(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "board.bff"
            path.write_text(BFF, encoding="utf-8-sig")
            self.assertEqual(load_bff(path), parse_bff(BFF))
            path.write_bytes(b"\xff\xfeinvalid")
            with self.assertRaisesRegex(BFFParseError, "UTF-8"):
                load_bff(path)


class OutputTests(unittest.TestCase):
    def setUp(self):
        self.puzzle = parse_bff(BFF)
        self.result = simulate(self.puzzle, {(1, 0): "A"})
        self.assertTrue(self.result.is_solution)

    def test_status_uses_boolean_properties(self):
        partial = simulate(self.puzzle)
        self.assertTrue(partial.all_targets_hit)
        self.assertFalse(partial.is_solution)
        self.assertIn(
            "TARGETS HIT - INVENTORY INCOMPLETE", format_report(partial),
        )
        missed = simulate(parse_bff(BFF.replace("P 1 2", "P 3 0")))
        self.assertFalse(missed.all_targets_hit)
        self.assertIn("TARGETS MISSED", format_report(missed))

    def test_report_and_snapshot_roundtrip(self):
        with TemporaryDirectory() as directory:
            root = Path(directory) / "nested"
            report = write_report(self.result, root / "solution.txt")
            self.assertIn("Status: VALID SOLUTION", report.read_text())
            self.assertIn("A at (1, 0)", report.read_text())
            snapshot = write_bff(self.result, root / "solution.bff")
            replay = load_bff(snapshot)
            self.assertEqual(replay.grid, self.result.layout.grid)
            self.assertFalse(any(replay.block_counts.values()))
            self.assertEqual(replay.targets, self.puzzle.targets)
            self.assertTrue(simulate(replay).is_solution)

    def test_svg_has_ray_geometry_and_target_status(self):
        with TemporaryDirectory() as directory:
            path = write_svg(self.result, Path(directory) / "solution.svg")
            root = ET.parse(path).getroot()
        namespace = {"svg": "http://www.w3.org/2000/svg"}
        rays = root.find("svg:g[@id='rays']", namespace)
        self.assertIsNotNone(rays)
        self.assertEqual(len(rays), len(self.result.segments))
        target = root.find(
            "svg:g[@id='targets']/svg:circle", namespace,
        )
        self.assertEqual(target.get("data-status"), "hit")
        self.assertEqual(
            (target.get("data-x"), target.get("data-y")), ("1", "2"),
        )

    def test_search_metadata_names_actual_algorithm(self):
        from search import DFSResult

        for algorithm, name in (
            ("targets", "Target-X constraint search"),
            ("dfs", "depth-first block-placement search"),
        ):
            with self.subTest(algorithm=algorithm):
                search = DFSResult(
                    "solved", self.result, 0.01, 1, 2, 3, "",
                    algorithm=algorithm,
                )
                self.assertIn(f"Algorithm: {name}", format_report(
                    self.result, search=search,
                ))
                with TemporaryDirectory() as directory:
                    path = write_svg(
                        self.result, Path(directory) / "solution.svg",
                        search=search,
                    )
                    self.assertIn(name, path.read_text())
        other = simulate(self.puzzle, {(1, 0): "A"})
        with self.assertRaisesRegex(ValueError, "metadata"):
            format_report(other, search=search)


class MainTests(unittest.TestCase):
    def run_main(self, arguments):
        from main import main

        with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            return main(arguments)

    def test_both_algorithms_write_replayable_results(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            board = root / "board.bff"
            board.write_text(BFF)
            for algorithm in ("targets", "dfs"):
                with self.subTest(algorithm=algorithm):
                    output = root / algorithm
                    self.assertEqual(self.run_main([
                        str(board), "--algorithm", algorithm,
                        "--output-dir", str(output),
                    ]), 0)
                    result_dir = output / "board"
                    for name in (
                        "search_status.txt", "solution.txt",
                        "solution.svg", "solution.bff",
                    ):
                        self.assertTrue((result_dir / name).is_file())
                    self.assertTrue(simulate(load_bff(
                        result_dir / "solution.bff",
                    )).is_solution)

    def test_multiple_inputs_with_same_stem_do_not_overwrite(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            boards = []
            for folder in ("first", "second"):
                path = root / folder / "same.bff"
                path.parent.mkdir()
                path.write_text(BFF)
                boards.append(str(path))
            output = root / "results"
            self.assertEqual(self.run_main([
                *boards, "--output-dir", str(output),
            ]), 0)
            self.assertTrue((output / "same" / "solution.bff").is_file())
            self.assertTrue((output / "same_2" / "solution.bff").is_file())

    def test_bad_input_does_not_stop_remaining_boards(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            good = root / "good.bff"
            good.write_text(BFF)
            output = root / "results"
            self.assertEqual(self.run_main([
                str(root / "missing.bff"), str(good),
                "--output-dir", str(output),
            ]), 2)
            self.assertTrue((output / "good" / "solution.bff").is_file())

    def test_no_solution_and_timeout_are_distinct(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            board = root / "board.bff"
            board.write_text(
                BFF.replace("A 1", "A 0").replace("P 1 2", "P 3 0")
            )
            no_solution = root / "no_solution"
            self.assertEqual(self.run_main([
                str(board), "--output-dir", str(no_solution),
            ]), 1)
            self.assertIn("Status: no_solution", (
                no_solution / "board" / "search_status.txt"
            ).read_text())
            timeout = root / "timeout"
            self.assertEqual(self.run_main([
                str(board), "--timeout", "0", "--output-dir", str(timeout),
            ]), 2)
            self.assertIn("Status: timeout", (
                timeout / "board" / "search_status.txt"
            ).read_text())
            self.assertFalse((timeout / "board" / "solution.bff").exists())


if __name__ == "__main__":
    unittest.main()
