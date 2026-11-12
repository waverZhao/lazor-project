# Lazor Solver

Read a `.bff` puzzle, place the movable blocks, and export a verified solution. Requires Python 3.9 or newer and only the Python standard library.

![Mad 1 solution](docs/images/mad1-solution.svg)

## Run

From the project directory:

```bash
python3 -B main.py examples/mad_1.bff
```

The default algorithm is `targets`, with a 60-second search budget per board. Use `--algorithm dfs` for baseline placement enumeration.

To extract and solve the 157-level dataset:

```bash
python3 -m zipfile -e data/lazor_level_collection.zip data
python3 -B main.py data/lazor_level_collection/levels/*/*.bff --algorithm targets --timeout 120 --output-dir results/public_levels
```

## Outputs

Every successfully solved puzzle automatically generates a result image at `results/<puzzle_name>/solution.svg`, showing the final blocks, laser paths, and targets. Batch runs generate a separate image for each solved puzzle.

Each puzzle gets a directory such as `results/mad_1/`:

| File | Contents |
| --- | --- |
| `search_status.txt` | Status, search time, and counters |
| `solution.txt` | Final grid, block positions, target coverage, and laser segments |
| `solution.svg` | Game-style board with final blocks, sources, targets, and laser paths |
| `solution.bff` | Reloadable solved layout, with placed blocks fixed and remaining inventory zero |

A `no_solution` or `timeout` run writes a status file without generating a new solution. Timeout means the search is incomplete.

## Search Method

1. Build a directed 45-degree graph. Each target has two candidate lines forming an X, with up to four arrival directions. Search backward toward a source or an already guaranteed path.
2. Intersect shared cell domains along each route. Straight travel permits empty/C; reflection permits A/C; requiring both forces C. Reflections also require clearance through the adjacent cell crossed by the outgoing ray.
3. Check exact inventory and combined Hall capacity bounds. A histogram of at most 15 domain masks reduces repeated scans. On conflict, backtrack to the parent's immutable domains.
4. Connect all targets, complete the remaining inventory by capacitated matching, and replay the board with the shared simulator. Return the first verified solution.

Each block has two switches: R controls reflection and T controls transmission. A value of 1 enables the behavior; 0 disables it.

| Type | R (reflection) | T (transmission) | Outgoing light |
| --- | :---: | :---: | --- |
| Empty `o` / unavailable `x` | 0 | 1 | Straight |
| A | 1 | 0 | Reflected |
| B | 0 | 0 | Absorbed |
| C | 1 | 1 | Straight and reflected |

Route choices restrict the candidates of a movable cell:

| Required behavior | R requirement | T requirement | Allowed candidates |
| --- | :---: | :---: | --- |
| Straight travel | Unrestricted | 1 | Empty `o` or C |
| Reflection | 1 | Unrestricted | A or C |
| Both in the same cell | 1 | 1 | C only |

Unrestricted does not mean 0: a route requiring straight travel still allows C to produce an additional reflected ray. Candidates must also satisfy the board and inventory constraints.

A straight edge costs 1 and a reflected edge costs 4 for search ordering. Inventory counts distinct movable blocks, including blocks used by multiple reflections.

Core implementation: [target_solver.py](target_solver.py), [constraints.py](constraints.py), and [search_graph.py](search_graph.py).

Explanatory diagrams: [target X lines](docs/images/mad1-candidates-en.svg) and [R/T updates](docs/images/mad1-rt-steps-en.svg).

## Level Dataset

[Download the dataset](data/lazor_level_collection.zip): 157 primary A/B/C puzzles in 17 packs, four source variants, and raw data for 52 prism levels that need additional rules. This public transcription collection has not been confirmed as the current official game's complete level set.

Primary BFFs come from [halghm4's pinned collection](https://github.com/halghm4/lazor_solver/tree/8aef93e9ce75bcdd5d1f5cb626cdacb71006cbe7/boards/all), cross-checked against [electricg's pinned transcription](https://github.com/electricg/laser-game-canvas/blob/63299ad451b4d739a1e9f35fae68f7c184e7fa8f/levels.js). The archive includes per-level provenance, hashes, conversion details, and preserved differences.

## Tests

```bash
python3 -B -m unittest discover -s tests -v
```

**All 26 tests pass. All 157 primary puzzles were solved**, independently checked for inventory and complete light paths, and verified again after exporting and reloading their BFFs. Search received only puzzle inputs, without supplied answers.

The latest serial batch took **0.339 s** for reading and solving. Median time was **0.00148 s**. The slowest puzzle, `dejavu_10`, took **0.0224 s**. Timing excludes startup, imports, independent checks, and output writing.

[Download test records and all solution files](data/public_levels_157_results.zip).

## Team Contributions and Sources

| Responsibility | Contributor | Files |
| --- | --- | --- |
| File parsing, block placement, and result output | fangyu li | [Reader](Fangyu/file_io.py), [placement search](Fangyu/search.py), [output](Fangyu/output.py) |
| Block classes and laser simulation; participation in basic DFS, SMT solving, source-unreachable-state pruning, and related explanations | Xingjian Yang | [Block/simulation](Xingjian/block_laser.py), [DFS](Xingjian/search.py), [SMT](Xingjian/constraint_solver.py) |
| X-line/R/T search design, search optimization and pruning, inventory constraints and rollback, integration and testing | weibo zhao | Three core search files, `solve_targets()`, and tests |

These are the main contributions; search implementation and pruning include shared and overlapping work. Original teammate submissions are retained in [Fangyu/](Fangyu/) and [Xingjian/](Xingjian/). The project root contains the integrated program.

Xingjian's algorithm contributions also include [optional source-unreachable-state pruning](Xingjian/constraint_solver.py#L300), [mathematical model and pruning principles](Xingjian/MATH_MODEL.txt), and [installation, running, and integration notes](Xingjian/README.txt). The SMT comparison program uses Z3; see its [requirements](Xingjian/requirements.txt). The block/simulation teammate's repository is [here](https://github.com/yangcong0168-dot/lazor-project/tree/1373f5a43193e81135d52a1d8e3cfc0a9c00f704).

The optimized implementation follows weibo zhao's X-line/R/T design and histogram proposal, developed with Codex assistance. Integration adapted the shared interfaces and reflection-clearance constraints.

`examples/mad_1.bff` is the course example. Other reference fixture sources are documented in [examples/reference/SOURCE.txt](examples/reference/SOURCE.txt).
