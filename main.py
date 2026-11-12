"""Read BFF puzzles, search for placements, and export verified results."""

from __future__ import annotations

import argparse
from math import isfinite
from pathlib import Path
import sys

from file_io import load_bff
from output import ALGORITHM_NAMES, write_bff, write_report, write_svg
from search import solve_dfs, solve_targets


def main(argv: list[str] | None = None) -> int:
    """Return 0 for solved, 1 for no solution, and 2 for timeout or error."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "boards", type=Path, nargs="+", help="Input .bff puzzle(s)",
    )
    parser.add_argument(
        "--output-dir", type=Path, default=Path("results"),
        help="Directory for each board's status, report, SVG, and BFF",
    )
    parser.add_argument(
        "--algorithm", choices=("targets", "dfs"), default="targets",
        help="Target-X constraints (default), or exhaustive placement DFS",
    )
    parser.add_argument(
        "--timeout", type=float, default=60.0,
        help="Search time budget per board in seconds (default: 60)",
    )
    args = parser.parse_args(argv)
    if not isfinite(args.timeout) or args.timeout < 0:
        parser.error("--timeout must be finite and nonnegative")

    solve = solve_targets if args.algorithm == "targets" else solve_dfs
    exit_code = 0
    used_names = set()
    for board_path in args.boards:
        name = board_path.stem
        suffix = 2
        while name in used_names:
            name = f"{board_path.stem}_{suffix}"
            suffix += 1
        used_names.add(name)
        output_dir = args.output_dir / name

        try:
            puzzle = load_bff(board_path)
            search = solve(puzzle, timeout=args.timeout)
            output_dir.mkdir(parents=True, exist_ok=True)
            status_text = (
                f"Input: {board_path}\n"
                f"Algorithm: {ALGORITHM_NAMES[args.algorithm]}\n"
                f"Status: {search.status}\n"
                f"Candidates tested: {search.candidates_tested}\n"
                f"States expanded: {search.states_expanded}\n"
                f"Search time: {search.elapsed_seconds:.6f} seconds\n"
                f"Reason: {search.reason or 'A solution was verified.'}\n"
            )
            status_path = output_dir / "search_status.txt"
            status_path.write_text(status_text, encoding="utf-8")
            print(status_text, end="")

            if search.status == "timeout":
                exit_code = 2
                continue
            if search.status == "no_solution":
                exit_code = max(exit_code, 1)
                continue

            result = search.solution
            if result is None or not result.is_solution:
                raise ValueError(
                    "Search reported success without a valid solution."
                )

            paths = (
                write_report(
                    result, output_dir / "solution.txt", search=search,
                ),
                write_svg(result, output_dir / "solution.svg", search=search),
                write_bff(result, output_dir / "solution.bff", search=search),
            )
            for path in paths:
                print(f"Saved: {path}")
        except (OSError, ValueError) as exc:
            print(f"Error: {board_path}: {exc}", file=sys.stderr)
            exit_code = 2

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
