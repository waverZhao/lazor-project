"""Time both integrated algorithms serially and independently check solutions."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from math import isfinite
from pathlib import Path
import platform
import sys
from time import perf_counter


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from file_io import load_bff
from search import solve_dfs, solve_targets
from test_search_integration import reference_trace


def verify_solution(puzzle, solution):
    """Check placement rules and replay geometry without production simulation."""
    if solution is None or not solution.is_solution:
        raise AssertionError("Solved status requires a complete solution.")
    layout = solution.layout
    placements = layout.placements
    if len(layout.grid) != puzzle.height or any(
        len(row) != puzzle.width for row in layout.grid
    ):
        raise AssertionError("Solution grid dimensions changed.")
    for position, kind in placements.items():
        if (
            not isinstance(position, tuple)
            or len(position) != 2
            or any(type(value) is not int for value in position)
        ):
            raise AssertionError("Placement coordinates must be integer pairs.")
        column, row = position
        if not (0 <= column < puzzle.width and 0 <= row < puzzle.height):
            raise AssertionError("A placement is outside the original grid.")
        if puzzle.grid[row][column] != "o" or kind not in ("A", "B", "C"):
            raise AssertionError("A placement violates an original cell.")
    for kind in "ABC":
        if sum(value == kind for value in placements.values()) != (
            puzzle.block_counts.get(kind, 0)
        ):
            raise AssertionError(f"Movable {kind} inventory does not match.")
    if any(layout.remaining_blocks.values()):
        raise AssertionError("The returned layout has unused inventory.")
    for row, cells in enumerate(puzzle.grid):
        for column, original in enumerate(cells):
            expected = (
                placements.get((column, row), "o")
                if original == "o" else original
            )
            if layout.grid[row][column] != expected:
                raise AssertionError("Fixed cells or placement grid changed.")
    points, segments = reference_trace(puzzle, placements)
    if not puzzle.targets <= points:
        raise AssertionError("Independent replay missed required targets.")
    if solution.visited_points != points:
        raise AssertionError("Visited points differ from independent replay.")
    if frozenset(solution.segments) != segments:
        raise AssertionError("Ray segments differ from independent replay.")
    if solution.hit_targets != puzzle.targets & points or (
        solution.missed_targets != puzzle.targets - points
    ):
        raise AssertionError("Reported target coverage is inconsistent.")


def run_case(path, algorithm, solve, timeout):
    """Read a fresh puzzle and exclude independent verification from timing."""
    record = {
        "input": str(path),
        "algorithm": algorithm,
        "status": "error",
        "verified": False,
    }
    started = perf_counter()
    elapsed = None
    try:
        puzzle = load_bff(path)
        original_inventory = dict(puzzle.block_counts)
        remaining = max(0.0, timeout - (perf_counter() - started))
        result = solve(puzzle, timeout=remaining)
        elapsed = perf_counter() - started
        record.update({
            "status": result.status,
            "search_seconds": result.elapsed_seconds,
            "counters": {
                "candidates_tested": result.candidates_tested,
                "states_expanded": result.states_expanded,
                "ray_states_processed": result.ray_states_processed,
            },
            "reason": result.reason,
        })
        if puzzle.block_counts != original_inventory:
            raise AssertionError("Search changed the puzzle inventory.")
        if result.status == "solved":
            verify_solution(puzzle, result.solution)
            record["verified"] = True
            record["targets_hit"] = len(result.solution.hit_targets)
            record["targets_required"] = len(puzzle.targets)
        elif result.status not in ("no_solution", "timeout"):
            raise AssertionError(f"Unexpected search status {result.status!r}.")
        elif result.solution is not None:
            raise AssertionError("Unsolved search returned a solution object.")
    except Exception as exc:
        if elapsed is None:
            elapsed = perf_counter() - started
        record["status"] = "error"
        record["error"] = f"{type(exc).__name__}: {exc}"
    record["load_and_solve_seconds"] = elapsed
    return record


def main(argv=None):
    """Return nonzero for an invalid solution or input/runtime error."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs="*", type=Path)
    parser.add_argument("--timeout", type=float, default=3.0)
    parser.add_argument(
        "--report", type=Path,
        default=Path("results/algorithm_comparison.json"),
    )
    args = parser.parse_args(argv)
    if not isfinite(args.timeout) or args.timeout < 0:
        parser.error("--timeout must be finite and nonnegative")
    files = args.files or sorted((ROOT / "examples" / "reference").glob("*.bff"))
    if not files:
        parser.error("No input .bff files were found")
    algorithms = (("targets", solve_targets), ("dfs", solve_dfs))
    report = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "timeout_per_case_seconds": args.timeout,
        "execution": "serial, targets then dfs for each input",
        "timing_scope": (
            "Input reading through solve return, including graph construction "
            "and the solver's final simulation. Excludes interpreter startup, "
            "module imports, independent verification and output writing. "
            "The read time is deducted from the cooperative solver budget; "
            "individual operations may finish after the deadline."
        ),
        "verification": (
            "Every solved output is checked against original fixed cells and "
            "movable inventory, then replayed using test_search_integration."
            "reference_trace. Timeouts are incomplete searches, not no-solution "
            "proofs. No-solution results are not independently proven here."
        ),
        "counter_note": (
            "Counters retain each algorithm's definitions. DFS counts complete "
            "placement simulations and placement nodes. Target search counts "
            "verified completions and directed route expansions; its ray-state "
            "counter describes the returned final trace."
        ),
        "cases": [],
        "summary_by_algorithm": {},
    }
    for path in files:
        path = path.resolve()
        for algorithm, solve in algorithms:
            record = run_case(path, algorithm, solve, args.timeout)
            report["cases"].append(record)
            verified = " verified" if record["verified"] else ""
            print(
                f"{path.name}: {algorithm} {record['status']} "
                f"{record['load_and_solve_seconds']:.6f}s{verified}",
                flush=True,
            )
            if record["status"] == "error":
                print(f"  {record['error']}", file=sys.stderr, flush=True)
    for algorithm, _ in algorithms:
        rows = [row for row in report["cases"] if row["algorithm"] == algorithm]
        report["summary_by_algorithm"][algorithm] = {
            "cases": len(rows),
            "solved": sum(row["status"] == "solved" for row in rows),
            "timeout": sum(row["status"] == "timeout" for row in rows),
            "no_solution": sum(row["status"] == "no_solution" for row in rows),
            "errors": sum(row["status"] == "error" for row in rows),
            "verified_solutions": sum(row["verified"] for row in rows),
            "sum_seconds": sum(row["load_and_solve_seconds"] for row in rows),
        }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Saved: {args.report}")
    return int(any(row["status"] == "error" for row in report["cases"]))


if __name__ == "__main__":
    sys.exit(main())
