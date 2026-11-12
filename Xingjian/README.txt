给 B 的搜索、数学约束和剪枝代码

Python 3.10+。本目录是一份可独立运行的配套包，接口与前一个 A 包一致。
解压后在本目录运行。不要把本包文件与旧 lazor 包随机混用。

一、B 主要看这几个文件

search.py
  solve_dfs(puzzle, timeout=110.0) -> DFSResult
  基础 DFS：按 A/B/C 分组，以组合枚举不同摆法，用完整光路模拟验证。
  相同类型的方块没有编号，因此不会重复搜索它们互换后的同一摆法。
  这是对照算法，后续可以继续增加有正确性依据的剪枝。

constraint_solver.py
  build_planning_model(puzzle, prune_unreachable=False) -> PlanningModel
  solve_constraints(puzzle, timeout=110.0, model_path=None,
                    prune_unreachable=False) -> PlanningResult
  布尔/整数 SMT 约束求解，使用 Z3。
  prune_unreachable=True 启用“光源不可达状态裁剪”实验模式。
  搜索结束后总是用你负责的 simulate() 独立复核得到的解。

MATH_MODEL.txt
  解释决策变量、库存约束、光学转移、光源与目标约束、闭环处理，
  以及裁剪安全性的理由和不能直接采用的错误剪枝方法。

run_algorithms.py
  切换三种模式，输出实际算法名称、统计、文本、SVG与BFF快照。

test_algorithms.py
  小棋盘全枚举真值对照、闭环、分光、剪枝、模型导出与接口测试。

其余是配套依赖：
  block_laser.py、file_io.py：与 A 包共享的定义与接口，直接复制。
  output.py：保留 A 包原来的四个输出函数，并新增 SMT 统计支持。
  requirements.txt：SMT 的第三方依赖版本，沿用原项目已使用的版本。

二、这里的“数学优化”具体是什么

当前实现是“约束可行性求解”：找出一个满足全部规则的方块配置。
它没有最少方块或最短光路的优化目标，也不声称算出全局最优路径。
库存按要求全部用完。Z3 内部仍然进行逻辑搜索。

裁剪模式来自原项目的源可达性实验，先构建完整模型，再裁剪并重建。
因此减少变量或约束，并不自动代表端到端运行更快；没有固定加速保证。
如果 B 后续要优化构建时间，可以把相同的安全裁剪提前到变量创建之前，
但必须保留测试与完整来源约束。

三、与 A 和激光模块统一的接口

输入：
  puzzle.grid、puzzle.block_counts、puzzle.lasers、puzzle.targets
  光源类叫 lazor；解析器用 lazor as Laser 作导入别名，是同一个类。
  placements = {(列, 行): "A"/"B"/"C"}，整格坐标从0开始。
  光源、目标、线段端点使用半格坐标，x向右、y向下。

所有算法成功后：
  result = search_result.solution
  result.is_solution()          普通方法，必须有括号
  result.all_targets_hit()      普通方法，必须有括号
  result.remaining_blocks()     普通方法，必须有括号
  result.layout.grid            摆放后的棋盘
  result.layout.placements      本次可移动方块摆法
  result.segments               光路线段
  result.hit_targets / result.missed_targets

Puzzle.width/height/placeable_positions/fixed_blocks、BoardLayout.complete
以及搜索结果的 solved 仍然是属性，不加括号。

两类搜索结果的共同字段：status、solution、elapsed_seconds、reason。
status=solved：有经过模拟复核的完整解。
status=no_solution：DFS已完全穷举，或SMT证明UNSAT。
status=timeout：预算用完，不代表无解。
SMT另有status=unknown：求解器未能给出确定结论，也不代表无解。

DFSResult 额外统计：candidates_tested、states_expanded、ray_states_processed。
PlanningResult 额外统计：build_seconds、solver_seconds、verification_seconds、
state_count、transition_count、variable_count、constraint_count、solver_checks、
solver_version、reachable_states、pruning_enabled、unpruned_state_count、
unpruned_transition_count。状态和边数是最终实际使用的模型规模。

合并到 A 包时：
  1. 添加 constraint_solver.py、requirements.txt。
  2. 用本包 output.py 更新 A 包的 output.py：原 DFS 调用仍兼容，
     并且可以接受 PlanningResult 作为 search 参数。
  3. 选择使用 run_algorithms.py，或者在 A 的 main.py 中调用所需算法。
  4. block_laser.py、file_io.py、search.py 与 A 包保持同一份定义。
不要把 PlanningResult 直接传给旧版“只识别DFS”的 output.py。

四、安装与运行

基础 DFS 只用标准库；SMT 先安装：
  python -m pip install -r requirements.txt

也支持显式的项目本地安装：
  python -m pip install --target .deps -r requirements.txt

运行同一个样例：
  python run_algorithms.py examples/demo.bff --algorithm dfs
  python run_algorithms.py examples/demo.bff --algorithm smt
  python run_algorithms.py examples/demo.bff --algorithm smt-pruned

导出可检查的数学模型：
  python run_algorithms.py examples/demo.bff --algorithm smt-pruned --model results/demo.smt2

检查课程示例（不是完整官方测试集）：
  python run_algorithms.py examples/mad_1.bff --algorithm smt --timeout 110

运行测试：
  python -m unittest -v test_algorithms

默认输出分别在 results/dfs、results/smt、results/smt-pruned，
也可通过 --output-dir 指定。成功时有 solution.txt、solution.svg、
solution.bff。所有完成的搜索都有 search_result.json，写明实际算法和状态。
只依据本次 JSON 的状态判断是否成功，复用目录中的旧解文件可能仍存在。

命令行退出码：0成功，1已证明无解，2超时/unknown/错误。
110秒为默认时间预算，建模与Python模拟阶段只在边界检查时间，
不是所有阶段都能在截止时刻强制停止，也不保证每个关卡都能及时求解。

当前电脑可使用 README 里已验证的 Python 3.12：
  C:\Users\28170\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe
队友使用自己电脑上的 Python 3.10+，不需要复制这个绝对路径。
本机已有上级 .deps；解压到其他电脑后需按上面的命令安装依赖。
压缩包不携带平台相关的 Z3 二进制文件。

五、代码来源与适用范围

基础DFS源于 lazor/dfs_baseline.py，经A包接口适配。
数学模型源于 lazor/solver.py。
剪枝模式源于 tmp/probe_forward_pruning.py 的可达状态裁剪实验，
本包改成正式可选参数，避免通过测试mock替换函数。
这些源码是协作起点；最终课程全部棋盘的正确性和时间要求仍需合并验收。

六、本次验证

基础 SMT 通过原项目 18 项回归测试，内存适配到本包的共享类、方法调用
和模块路径。随包 test_algorithms.py 的 11 项测试也全部通过，其中有
10 组微型棋盘全赋值模拟真值对照，以及 9 个命令行算法/状态组合。
验证包含：真实闭环允许、孤立闭环不能凭空点亮、C的两个分支、相邻C、
SAT/UNSAT模型回读、timeout/unknown区分、剪枝不漏解、输出与光路回放。

另用 examples/mad_1.bff 做了单次功能检查：DFS、SMT、SMT-pruned 均找到
经过模拟验证的解，全部4个目标命中；此例裁剪模型从160状态减至39状态。
这是单次功能检查，不是性能排名或保证加速的基准测试。

block_laser.py、file_io.py、search.py 已核对与 A 包对应文件逐字节一致。
SMT模型与裁剪代码的新接口通过测试；输出模块同时支持两类搜索结果。
