Fangyu's part

Files:
  file_io.py  - read and validate .bff files
  search.py   - basic placement search; the optimization teammate can edit this file
  output.py   - write the text report, SVG path diagram, and solved .bff file
  main.py     - connect file reading, search, simulation, and output

Integration:
  Put these four files in the same folder as the teammate's corrected
  block_laser.py. Do not use the older file named block and simulation.py.

Run:
  python main.py board_name.bff --output-dir results --timeout 110

The optimization teammate should keep the solve_dfs() input and DFSResult
fields unchanged so main.py and output.py continue to work.
