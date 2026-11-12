"""Read a BFF puzzle, search for a placement, and export its verified result."""

import argparse
from pathlib import Path
import sys

from file_io import load_bff
from output import write_bff, write_report, write_svg
from search import solve_dfs


def main(argv: list[str] | None = None) -> int:
    """Return 0 for solved, 1 for no solution, and 2 for timeout or error."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("board", type=Path, help="Input .bff puzzle")
    parser.add_argument(
        "--output-dir", type=Path, default=Path("results"),
        help="Directory for the status, text report, SVG, and BFF snapshot",
    )
    parser.add_argument(
        "--timeout", type=float, default=110.0,
        help="Cooperative DFS time budget in seconds (default: 110)",
    )
    args = parser.parse_args(argv)

    try:
        puzzle = load_bff(args.board)
        search = solve_dfs(puzzle, timeout=args.timeout)
        args.output_dir.mkdir(parents=True, exist_ok=True)
        status_text = (
            f"Status: {search.status}\n"
            f"Candidates tested: {search.candidates_tested}\n"
            f"States expanded: {search.states_expanded}\n"
            f"Search time: {search.elapsed_seconds:.6f} seconds\n"
            f"Reason: {search.reason or 'A complete solution was verified.'}\n"
        )
        status_path = args.output_dir / "search_status.txt"
        status_path.write_text(status_text, encoding="utf-8")
        print(status_text, end="")

        if search.status == "timeout":
            return 2
        if search.status == "no_solution":
            return 1

        result = search.solution
        if result is None or not result.is_solution():
            raise ValueError("Search reported success without a valid solution.")

        paths = (
            write_report(result, args.output_dir / "solution.txt", search=search),
            write_svg(result, args.output_dir / "solution.svg", search=search),
            write_bff(result, args.output_dir / "solution.bff", search=search),
        )
        for path in paths:
            print(f"Saved: {path}")
        return 0
    except (OSError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
