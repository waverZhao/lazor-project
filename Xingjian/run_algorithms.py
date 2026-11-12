"""Run DFS, SMT, or SMT with optional source-reachability pruning."""

import argparse
import json
from pathlib import Path
import sys

from constraint_solver import solve_constraints
from file_io import load_bff
from output import write_bff, write_report, write_svg
from search import solve_dfs


def main(argv: list[str] | None = None) -> int:
    """Return 0 for solved, 1 for proven no solution, and 2 otherwise."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("board", type=Path, help="Input BFF puzzle")
    parser.add_argument(
        "--algorithm", choices=("dfs", "smt", "smt-pruned"), default="smt",
    )
    parser.add_argument("--timeout", type=float, default=110.0)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--model", type=Path, help="Optional SMT-LIB export")
    args = parser.parse_args(argv)
    if args.algorithm == "dfs" and args.model is not None:
        parser.error("--model is available only for smt and smt-pruned.")
    folder = args.output_dir or Path("results") / args.algorithm

    try:
        puzzle = load_bff(args.board)
        if args.algorithm == "dfs":
            search = solve_dfs(puzzle, timeout=args.timeout)
            statistics = {
                "candidates_tested": search.candidates_tested,
                "states_expanded": search.states_expanded,
                "ray_states_processed": search.ray_states_processed,
            }
        else:
            search = solve_constraints(
                puzzle, timeout=args.timeout, model_path=args.model,
                prune_unreachable=args.algorithm == "smt-pruned",
            )
            statistics = {
                name: getattr(search, name) for name in (
                    "build_seconds", "solver_seconds", "verification_seconds",
                    "state_count", "transition_count", "variable_count",
                    "constraint_count", "solver_version", "solver_checks",
                    "pruning_enabled", "unpruned_state_count",
                    "unpruned_transition_count",
                )
            }

        summary = {
            "algorithm": args.algorithm,
            "status": search.status,
            "elapsed_seconds": search.elapsed_seconds,
            "reason": search.reason,
            **statistics,
        }
        if search.status == "solved":
            result = search.solution
            if result is None or not result.is_solution():
                raise ValueError("Search returned success without a valid solution.")
            summary["placements"] = [
                {"column": column, "row": row, "kind": kind}
                for (column, row), kind in sorted(result.layout.placements.items())
            ]

        folder.mkdir(parents=True, exist_ok=True)
        (folder / "search_result.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(json.dumps(summary, ensure_ascii=False, indent=2))

        if search.status == "no_solution":
            return 1
        if search.status != "solved":
            return 2

        paths = (
            write_report(search.solution, folder / "solution.txt", search=search),
            write_svg(search.solution, folder / "solution.svg", search=search),
            write_bff(search.solution, folder / "solution.bff", search=search),
        )
        for path in paths:
            print(f"Saved: {path}")
        return 0
    except (OSError, ValueError, RuntimeError, ImportError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
