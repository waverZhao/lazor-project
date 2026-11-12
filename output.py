"""Write readable reports, SVG diagrams, and replayable board snapshots."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from xml.etree import ElementTree as ET

from block_laser import SimulationResult

if TYPE_CHECKING:
    from search import DFSResult


SVG_NAMESPACE = "http://www.w3.org/2000/svg"
ALGORITHM_NAMES = {
    "targets": "Target-X constraint search",
    "dfs": "depth-first block-placement search",
}


def _status(result: SimulationResult) -> str:
    if result.is_solution:
        return "VALID SOLUTION"
    if result.all_targets_hit:
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
    result: SimulationResult, search: DFSResult | None
) -> None:
    """Only attach search statistics to their exact verified solution."""
    if search is not None and (
        search.status != "solved"
        or search.solution is not result
        or not result.is_solution
    ):
        raise ValueError("Search metadata must match this valid solution.")


def _search_lines(search: DFSResult) -> list[str]:
    """Report placement-search work separately from this final light trace."""
    algorithm = ALGORITHM_NAMES.get(search.algorithm, search.algorithm)
    lines = [
        f"Algorithm: {algorithm}",
        f"Search status: {search.status}",
        f"Search time: {search.elapsed_seconds:.6f} seconds",
        f"Candidate layouts tested: {search.candidates_tested}",
        f"Search states expanded: {search.states_expanded}",
        f"Ray states in candidate simulations: {search.ray_states_processed}",
    ]
    if search.reason:
        lines.append(f"Search note: {search.reason}")
    return lines


def format_report(
    result: SimulationResult, *, search: DFSResult | None = None
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
            "LAZOR SOLUTION REPORT"
            if search else "LAZOR SIMULATION REPORT"
        ),
        f"Status: {_status(result)}",
        (
            "Search found this layout; the simulator verified it."
            if search else
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
    search: DFSResult | None = None,
) -> Path:
    """Save the complete readable report as UTF-8 text."""
    return _write(path, format_report(result, search=search))


def write_bff(
    result: SimulationResult,
    path: str | Path,
    *,
    search: DFSResult | None = None,
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
    search: DFSResult | None = None,
) -> Path:
    """Draw a compact game-style board with the verified laser paths.

    The picture uses half-block coordinates at 40 pixels per unit. Search
    statistics remain in the text report; SVG metadata retains the algorithm
    and result status for accessibility and automated checks.
    """
    _validate_search(result, search)
    layout, puzzle = result.layout, result.layout.puzzle
    scale, margin, cell_size = 40, 32, 80
    width = puzzle.width * cell_size + 2 * margin
    height = puzzle.height * cell_size + 2 * margin
    ET.register_namespace("", SVG_NAMESPACE)

    def element(parent, tag, attributes=None, text=None):
        node = ET.SubElement(
            parent, f"{{{SVG_NAMESPACE}}}{tag}",
            {key: str(value) for key, value in (attributes or {}).items()},
        )
        node.text = text
        return node

    def pixels(point):
        return margin + point[0] * scale, margin + point[1] * scale

    root = ET.Element(f"{{{SVG_NAMESPACE}}}svg", {
        "width": str(width), "height": str(height),
        "viewBox": f"0 0 {width} {height}",
        "role": "img", "aria-labelledby": "title description",
        "font-family": "Arial, sans-serif",
    })
    element(root, "title", {"id": "title"}, (
        "Lazor solution" if search else "Lazor simulation"
    ))
    algorithm = (
        ALGORITHM_NAMES.get(search.algorithm, search.algorithm)
        if search else "Supplied layout"
    )
    element(root, "desc", {"id": "description"}, (
        f"{algorithm}. {_status(result)}. "
        "White A blocks reflect, dark B blocks absorb, glass C blocks split. "
        "Red lines show laser paths. Bright targets were hit; dark rings "
        "were missed. Small diamonds mark sources and their directions. "
        "Metal pins mark original fixed blocks."
    ))
    defs = element(root, "defs")
    for name, top, bottom in (
        ("board", "#535158", "#2b2a30"),
        ("slot", "#68676d", "#535258"),
        ("reflect", "#ffffff", "#c5c9cf"),
        ("opaque", "#49474d", "#1e1d23"),
        ("glass", "#9daebc", "#5c6976"),
    ):
        gradient = element(defs, "linearGradient", {
            "id": name, "x1": "0", "y1": "0", "x2": "0.7", "y2": "1",
        })
        element(gradient, "stop", {"offset": "0", "stop-color": top})
        element(gradient, "stop", {"offset": "1", "stop-color": bottom})
    glow = element(defs, "filter", {
        "id": "glow", "x": "-100%", "y": "-100%",
        "width": "300%", "height": "300%",
        "color-interpolation-filters": "sRGB",
    })
    element(glow, "feGaussianBlur", {"stdDeviation": "2.6", "result": "blur"})
    merge = element(glow, "feMerge")
    element(merge, "feMergeNode", {"in": "blur"})
    element(merge, "feMergeNode", {"in": "SourceGraphic"})
    shadow = element(defs, "filter", {
        "id": "shadow", "x": "-30%", "y": "-30%",
        "width": "160%", "height": "170%",
    })
    element(shadow, "feDropShadow", {
        "dx": "0", "dy": "3", "stdDeviation": "2.5",
        "flood-color": "#08070b", "flood-opacity": "0.8",
    })
    element(root, "rect", {
        "width": width, "height": height, "rx": 18,
        "fill": "url(#board)",
    })

    grid = element(root, "g", {"id": "grid"})
    fills = {"o": "slot", "A": "reflect", "B": "opaque", "C": "glass"}
    for row, cells in enumerate(layout.grid):
        for column, kind in enumerate(cells):
            if kind == "x":
                continue
            x, y = margin + column * cell_size, margin + row * cell_size
            block = kind in "ABC"
            element(grid, "rect", {
                "x": x + 4, "y": y + 4, "width": 72, "height": 72,
                "rx": 12, "fill": "url(#{})".format(fills[kind]),
                "stroke": "#25242b" if block else "#86838c",
                "stroke-width": 2 if block else 1,
                "stroke-opacity": 0.9 if block else 0.45,
                "filter": "url(#shadow)" if block else "none",
                "data-column": column, "data-row": row, "data-kind": kind,
            })
            if not block:
                continue
            element(grid, "rect", {
                "x": x + 8, "y": y + 8, "width": 64, "height": 64,
                "rx": 9, "fill": "none", "stroke": "#ffffff",
                "stroke-opacity": 0.45 if kind == "A" else 0.19,
            })
            if kind == "C":
                element(grid, "path", {
                    "d": f"M {x + 15} {y + 39} L {x + 48} {y + 11} "
                         f"L {x + 66} {y + 11} L {x + 15} {y + 57} Z",
                    "fill": "#ffffff", "fill-opacity": 0.15,
                })
            element(grid, "text", {
                "x": x + 60, "y": y + 65, "text-anchor": "middle",
                "font-size": 13, "font-weight": "bold",
                "fill": "#58616b" if kind == "A" else "#d9e2eb",
                "fill-opacity": 0.65,
            }, kind)
            if puzzle.grid[row][column] in "ABC":
                element(grid, "circle", {
                    "cx": x + 16, "cy": y + 16, "r": 3,
                    "fill": "#979ba2", "stroke": "#27262d",
                    "stroke-width": 1.3, "data-fixed": "true",
                })

    rays = element(root, "g", {
        "id": "rays", "stroke": "#ff555c", "stroke-width": 2.6,
        "stroke-linecap": "round", "filter": "url(#glow)",
    })
    for start, end in result.segments:
        x1, y1 = pixels(start)
        x2, y2 = pixels(end)
        element(rays, "line", {"x1": x1, "y1": y1, "x2": x2, "y2": y2})

    targets = element(root, "g", {"id": "targets"})
    for point in sorted(puzzle.targets):
        x, y = pixels(point)
        hit = point in result.hit_targets
        element(targets, "circle", {
            "cx": x, "cy": y, "r": 7,
            "stroke": "#ff505a" if hit else "#a4a0aa",
            "stroke-width": 3 if hit else 2,
            "fill": "#fff8f5" if hit else "#343239",
            "filter": "url(#glow)" if hit else "none",
            "data-status": "hit" if hit else "missed",
            "data-x": point[0], "data-y": point[1],
        })
    sources = element(root, "g", {"id": "lasers"})
    for laser in puzzle.lasers:
        x, y = pixels((laser.x, laser.y))
        element(sources, "path", {
            "d": f"M {x} {y - 6} L {x + 6} {y} L {x} {y + 6} "
                 f"L {x - 6} {y} Z",
            "fill": "#ff4c57", "filter": "url(#glow)",
        })
        element(sources, "line", {
            "x1": x, "y1": y,
            "x2": x + laser.vx * 9, "y2": y + laser.vy * 9,
            "stroke": "#fff2ee", "stroke-width": 2,
            "stroke-linecap": "round",
        })
        element(sources, "circle", {
            "cx": x, "cy": y, "r": 2, "fill": "#ffffff",
            "data-x": laser.x, "data-y": laser.y,
            "data-vx": laser.vx, "data-vy": laser.vy,
        })
    ET.indent(root, space="  ")
    document = ET.tostring(root, encoding="unicode", xml_declaration=True)
    return _write(path, document + "\n")
