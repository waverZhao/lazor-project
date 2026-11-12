"""Write readable reports, SVG diagrams, and replayable board snapshots."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from xml.etree import ElementTree as ET

from block_laser import SimulationResult

if TYPE_CHECKING:
    from constraint_solver import PlanningResult
    from search import DFSResult

    SearchResult = DFSResult | PlanningResult


SVG_NAMESPACE = "http://www.w3.org/2000/svg"


def _status(result: SimulationResult) -> str:
    if result.is_solution():
        return "VALID SOLUTION"
    if result.all_targets_hit():
        return "TARGETS HIT - INVENTORY INCOMPLETE"
    return "TARGETS MISSED"


def _points(points: frozenset[tuple[int, int]]) -> str:
    return ", ".join(str(point) for point in sorted(points)) or "none"


def _write(path: str | Path, text: str) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(text, encoding="utf-8")
    return destination


def _validate_search(
    result: SimulationResult, search: SearchResult | None
) -> None:
    """Only attach search statistics to their exact verified solution."""
    if search is not None and (
        search.status != "solved"
        or search.solution is not result
        or not result.is_solution()
    ):
        raise ValueError("Search metadata must match this valid solution.")


def _is_smt(search: SearchResult) -> bool:
    """Recognize planning metadata without importing the optional solver."""
    return hasattr(search, "solver_checks")


def _algorithm_name(search: SearchResult) -> str:
    """Name the algorithm used, without implying a speed guarantee."""
    if not _is_smt(search):
        return "depth-first block-placement search (DFS)"
    if search.pruning_enabled:
        return "SMT + optimistic source-reachability pruning"
    return "SMT feasibility constraints (Z3)"


def _solution_label(search: SearchResult) -> str:
    """Use a compact algorithm label in headings."""
    if not _is_smt(search):
        return "DFS"
    return "SMT with source pruning" if search.pruning_enabled else "SMT"


def _search_lines(search: SearchResult) -> list[str]:
    """Keep DFS counts and SMT construction/solve costs distinct."""
    lines = [f"Algorithm: {_algorithm_name(search)}"]
    if _is_smt(search):
        lines.extend([
            "Objective: find any feasible layout; no path/block minimization.",
            f"Planning status: {search.status}",
            f"Planning time: {search.elapsed_seconds:.6f} seconds",
            f"Model build time: {search.build_seconds:.6f} seconds",
            f"Constraint solve time: {search.solver_seconds:.6f} seconds",
            f"Verification time: {search.verification_seconds:.6f} seconds",
            f"Solver version: {search.solver_version}",
            f"Optical states: {search.state_count}",
            f"Conditional transitions: {search.transition_count}",
            f"Model variables: {search.variable_count}",
            f"Model constraints: {search.constraint_count}",
            f"Solver checks: {search.solver_checks}",
            f"Reachable states: {len(search.reachable_states)}",
            f"Source pruning enabled: {search.pruning_enabled}",
            f"Unpruned optical states: {search.unpruned_state_count}",
            "Unpruned conditional transitions: "
            f"{search.unpruned_transition_count}",
        ])
        if search.pruning_enabled:
            lines.append(
                "Pruning removes optimistically unreachable states; "
                "it does not guarantee a faster solve."
            )
    else:
        lines.extend([
            f"Search status: {search.status}",
            f"Search time: {search.elapsed_seconds:.6f} seconds",
            f"Candidate layouts tested: {search.candidates_tested}",
            f"Search states expanded: {search.states_expanded}",
            "Ray states processed during search: "
            f"{search.ray_states_processed}",
        ])
    if search.reason:
        lines.append(f"Search note: {search.reason}")
    return lines


def _search_summary(search: SearchResult) -> str:
    """Return short measured statistics for the SVG header."""
    if _is_smt(search):
        return (
            f"SMT: {search.elapsed_seconds:.6f} s | "
            f"{search.state_count} states | "
            f"{search.constraint_count} constraints"
        )
    return (
        f"DFS: {search.elapsed_seconds:.6f} s | "
        f"{search.candidates_tested} layouts tested | "
        f"{search.states_expanded} search states"
    )


def format_report(
    result: SimulationResult, *, search: SearchResult | None = None
) -> str:
    """Describe this specific layout without claiming an unsolved puzzle.

    A layout is a valid solution only when it uses all supplied movable blocks
    and hits every target. A failed layout does not establish that no other
    arrangement works. Supplying the matching solved search result adds search
    statistics and identifies the layout as an automatically found solution.
    """
    _validate_search(result, search)
    layout = result.layout
    puzzle = layout.puzzle
    lines = [
        (
            f"LAZOR {_solution_label(search).upper()} SOLUTION REPORT"
            if search is not None else "LAZOR SIMULATION REPORT"
        ),
        f"Status: {_status(result)}",
        (
            f"{_algorithm_name(search)} found this layout; "
            "the simulator verified it."
            if search is not None else
            "This report checks one supplied layout; no search was performed."
        ),
        f"Board: {puzzle.width} columns x {puzzle.height} rows",
    ]
    if search is not None:
        lines.extend(_search_lines(search))
    lines.extend([
        "",
        "FINAL GRID (top row first)",
        "GRID START",
        *(" ".join(row) for row in layout.grid),
        "GRID STOP",
        "",
        "Legend: o=empty, x=no placement, A=reflect, B=opaque, C=split",
        "Block positions below use zero-based (column, row).",
        "Original fixed blocks:",
    ])
    fixed = puzzle.fixed_blocks
    lines.extend(
        f"  {fixed[position].kind} at {position}"
        for position in sorted(fixed)
    )
    if not fixed:
        lines.append("  none")
    lines.append("Newly placed movable blocks:")
    lines.extend(
        f"  {layout.placements[position]} at {position}"
        for position in sorted(layout.placements)
    )
    if not layout.placements:
        lines.append("  none")
    remaining = ", ".join(
        f"{kind}={layout.remaining_blocks.get(kind, 0)}"
        for kind in ("A", "B", "C")
    )
    lines.extend([
        f"Remaining movable blocks: {remaining}",
        "",
        "LASERS AND TARGETS (half-block coordinates)",
        "Origin: top left; +x points right, +y points down.",
    ])
    for index, laser in enumerate(puzzle.lasers, 1):
        lines.append(
            f"L{index}: start=({laser.x}, {laser.y}), "
            f"direction=({laser.vx}, {laser.vy})"
        )
    lines.extend([
        f"Hit targets: {_points(result.hit_targets)}",
        f"Missed targets: {_points(result.missed_targets)}",
        f"Targets hit: {len(result.hit_targets)}/{len(puzzle.targets)}",
        f"States processed: {result.states_processed}",
        f"Repeated states skipped: {result.repeated_states}",
        "Repeated states may be loops or merging ray branches.",
        f"Simulation time: {result.elapsed_seconds:.6f} seconds",
        "",
        "DIRECTED RAY SEGMENTS (half-block coordinates)",
    ])
    lines.extend(f"  {start} -> {end}" for start, end in result.segments)
    if not result.segments:
        lines.append("  none")
    return "\n".join(lines) + "\n"


def write_report(
    result: SimulationResult,
    path: str | Path,
    *,
    search: SearchResult | None = None,
) -> Path:
    """Save the complete readable report as UTF-8 text."""
    return _write(path, format_report(result, search=search))


def write_bff(
    result: SimulationResult,
    path: str | Path,
    *,
    search: SearchResult | None = None,
) -> Path:
    """Save a replayable layout snapshot, preserving remaining inventory.

    All currently placed blocks become fixed grid cells in the exported
    snapshot. Comments preserve their origin, but this is not a replacement
    for the original puzzle: the original movable inventory is no longer
    available for rearrangement in this file.
    """
    _validate_search(result, search)
    layout = result.layout
    puzzle = layout.puzzle
    lines = [
        "# Lazor layout snapshot; not the original movable puzzle.",
        f"# Status: {_status(result)}",
        "# All blocks currently on the board are fixed in this snapshot.",
        "# Unplaced inventory is retained below GRID STOP.",
        "# Block coordinates in comments use (column, row).",
    ]
    if search is not None:
        lines.extend(f"# {line}" for line in _search_lines(search))
    fixed = puzzle.fixed_blocks
    for position in sorted(fixed):
        lines.append(f"# Original fixed: {fixed[position].kind} {position}")
    for position in sorted(layout.placements):
        lines.append(
            f"# Newly placed: {layout.placements[position]} {position}"
        )
    lines.extend([
        "GRID START",
        *(" ".join(row) for row in layout.grid),
        "GRID STOP",
        "",
    ])
    for kind in ("A", "B", "C"):
        lines.append(f"{kind} {layout.remaining_blocks.get(kind, 0)}")
    lines.append("")
    for laser in puzzle.lasers:
        lines.append(f"L {laser.x} {laser.y} {laser.vx} {laser.vy}")
    lines.append("")
    for x, y in sorted(puzzle.targets):
        lines.append(f"P {x} {y}")
    return _write(path, "\n".join(lines) + "\n")


def write_svg(
    result: SimulationResult,
    path: str | Path,
    *,
    search: SearchResult | None = None,
) -> Path:
    """Save a self-contained SVG diagram with rays and target status.

    Grid labels refer to whole-block column/row indices. Ray geometry uses
    half-block coordinates, scaled by 40 pixels per half-block.
    """
    _validate_search(result, search)
    layout = result.layout
    puzzle = layout.puzzle
    scale = 40
    left = 72
    top = 168 if search is not None else 144
    board_width, board_height = puzzle.width * 80, puzzle.height * 80
    width = max(640, board_width + 2 * left)
    algorithm_notes = []
    if search is not None and _is_smt(search):
        algorithm_notes.append(
            "Feasibility only; no shortest-path or minimum-block objective."
        )
        if search.pruning_enabled:
            algorithm_notes.append(
                "Source pruning may reduce the model; speed is not guaranteed."
            )
    height = top + board_height + 208 + 26 * len(algorithm_notes)
    ET.register_namespace("", SVG_NAMESPACE)

    def element(parent, tag, attributes=None, text=None):
        node = ET.SubElement(
            parent, f"{{{SVG_NAMESPACE}}}{tag}",
            {key: str(value) for key, value in (attributes or {}).items()},
        )
        node.text = text
        return node

    def label(parent, x, y, text, **attributes):
        return element(parent, "text", {"x": x, "y": y, **attributes}, text)

    def pixels(point):
        return left + point[0] * scale, top + point[1] * scale

    root = ET.Element(f"{{{SVG_NAMESPACE}}}svg", {
        "width": str(width), "height": str(height),
        "viewBox": f"0 0 {width} {height}",
        "role": "img", "aria-labelledby": "title description",
        "font-family": "Arial, sans-serif", "font-size": "13",
    })
    title = (
        f"Lazor {_solution_label(search)} solution"
        if search is not None else "Lazor simulation"
    )
    element(root, "title", {"id": "title"}, title)
    element(root, "desc", {"id": "description"}, (
        (
            f"{_algorithm_name(search)} found this layout. "
            if search is not None else "One supplied layout. "
        )
        + "Red lines show rays; green targets were hit, "
        "orange targets were missed. Blue circles mark laser starts."
    ))
    element(root, "rect", {
        "width": width, "height": height, "fill": "#f8fafc",
    })
    label(root, 32, 38, title, **{
        "font-size": 26, "font-weight": "bold", "fill": "#0f172a",
    })
    status_color = "#15803d" if result.is_solution() else "#92400e"
    label(root, 32, 68, _status(result), **{
        "font-size": 17, "font-weight": "bold", "fill": status_color,
    })
    label(root, 32, 92, (
        f"{len(result.hit_targets)}/{len(puzzle.targets)} targets hit | "
        f"{sum(layout.remaining_blocks.values())} blocks unplaced | "
        + ("trace " if search is not None else "")
        + f"{result.elapsed_seconds:.6f} s"
    ), fill="#475569")
    if search is not None:
        label(root, 32, 112, f"Algorithm: {_algorithm_name(search)}",
              fill="#475569")
        label(root, 32, 132, _search_summary(search), fill="#475569")

    fills = {
        "o": "#ffffff", "x": "#e2e8f0", "A": "#bfdbfe",
        "B": "#475569", "C": "#ddd6fe",
    }
    grid = element(root, "g", {"id": "grid"})
    for row, cells in enumerate(layout.grid):
        for column, symbol in enumerate(cells):
            x, y = left + column * 80, top + row * 80
            attributes = {
                "x": x + 2, "y": y + 2, "width": 76, "height": 76,
                "rx": 7, "fill": fills[symbol], "stroke": "#64748b",
                "stroke-width": 2 if symbol in "ABC" else 1,
                "data-column": column, "data-row": row,
                "data-kind": symbol,
            }
            if (column, row) in layout.placements:
                attributes["stroke-dasharray"] = "6 4"
            element(grid, "rect", attributes)
            label(grid, x + 40, y + 47, symbol, **{
                "font-size": 22, "text-anchor": "middle",
                "fill": "#ffffff" if symbol == "B" else "#334155",
            })
    for column in range(puzzle.width):
        label(root, left + column * 80 + 40, top - 16, str(column), **{
            "text-anchor": "middle", "fill": "#475569",
        })
    for row in range(puzzle.height):
        label(root, left - 18, top + row * 80 + 45, str(row), **{
            "text-anchor": "middle", "fill": "#475569",
        })

    rays = element(root, "g", {
        "id": "rays", "stroke": "#dc2626", "stroke-width": 3,
        "stroke-linecap": "round", "stroke-opacity": "0.85",
    })
    for start, end in result.segments:
        x1, y1 = pixels(start)
        x2, y2 = pixels(end)
        element(rays, "line", {"x1": x1, "y1": y1, "x2": x2, "y2": y2})

    targets = element(root, "g", {"id": "targets"})
    for index, point in enumerate(sorted(puzzle.targets), 1):
        x, y = pixels(point)
        hit = point in result.hit_targets
        color = "#15803d" if hit else "#c2410c"
        element(targets, "circle", {
            "cx": x, "cy": y, "r": 9, "stroke": color,
            "stroke-width": 3, "fill": "#dcfce7" if hit else "#fff7ed",
            "data-status": "hit" if hit else "missed",
            "data-x": point[0], "data-y": point[1],
        })
        label(targets, x + 12, y - 10, f"P{index}", fill=color)
    sources = element(root, "g", {"id": "lasers"})
    for index, laser in enumerate(puzzle.lasers, 1):
        x, y = pixels((laser.x, laser.y))
        element(sources, "circle", {
            "cx": x, "cy": y, "r": 5, "fill": "#2563eb",
            "stroke": "#ffffff", "stroke-width": 1,
        })
        label(sources, x + 10, y + 18, f"L{index}", fill="#1d4ed8")

    footer = top + board_height + 38
    notes = [
        "A: reflect   B: opaque   C: split   o: empty   x: no placement",
        "Solid block border: original fixed; dashed: newly placed.",
        "Red: laser path   Blue: source   Green: hit   Orange: missed",
        "Cell labels: (column, row); ray coordinates use half-block units.",
        (
            "Solution: inventory and target coverage verified."
            if search is not None else
            "This checks one layout; no search for other layouts was "
            "performed."
        ),
    ] + algorithm_notes
    for index, note in enumerate(notes):
        label(root, 32, footer + index * 26, note, fill="#475569")
    ET.indent(root, space="  ")
    document = ET.tostring(root, encoding="unicode", xml_declaration=True)
    return _write(path, document + "\n")
