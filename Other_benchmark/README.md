# ICLR T1：HMA 与 RoutingBlocks 作者代码适配

这里把现存 ICLR EVRPTW test views 接到两个作者代码库，并用 ICLR 原有独立 verifier 重放所有候选解。正式报告名称为 **HMA adapted to the benchmark reference task** 和 **RoutingBlocks EVRPTW ALNS adapted variant**。这些名称表示已做任务适配，不表示复现了论文原任务上的成绩。

本次工作流已经创建，按 smoke、资源检查、固定面板、正式 cohort 顺序执行。持续更新的状态以 [`results/iclr_t1_author_solvers_20261106/pipeline_status.json`](results/iclr_t1_author_solvers_20261106/pipeline_status.json) 和各阶段 `status.json` 为准；本说明中的准备完成、测试通过不等于正式实验完成。

## 固定任务与实现入口

作者代码位于 `vendor/HMA`、`vendor/RoutingBlocks`，固定 revision 分别为：

| Solver | Author HEAD | 本地适配与构建 |
| --- | --- | --- |
| HMA | `0ebbcaa4c2b8385c2c9bff06d60689cb2c64229d` | `hma_adapter/`、`hma_adapter/build.sh` |
| RoutingBlocks | `2d2ee06c79b5d458b1aeeafd95a2b42b5441079a` | `routingblocks_adapter/`、`routingblocks_adapter/build.sh`、`requirements.lock` |

实际 benchmark 入口是 [`run_author_solvers.py`](../ICLR/EVRPTW-DB/EVRPTW_Benchmark/MetaHeuristics/AuthorSolvers/run_author_solvers.py)，调用本目录的 [`benchmark_bridge/run.py`](benchmark_bridge/run.py)。两个 solver 的初始化与搜索保留各自作者实现，适配层处理矩阵、资源、目标函数、候选导出和停止协议。HMA 的具体变化、测试和限制见 [`hma_adapter/provenance.json`](hma_adapter/provenance.json) 与 [`hma_reference.patch`](hma_adapter/hma_reference.patch)；RoutingBlocks 对应为 [`routingblocks_adapter/provenance.json`](routingblocks_adapter/provenance.json)、[`configuration.json`](routingblocks_adapter/configuration.json) 与 [`adaptation.patch`](routingblocks_adapter/adaptation.patch)。

输入和最终评分沿用 `/data/Maojie/ICLR/EVRPTW-DB` 中的原有接口：

- `EVRPTW_Benchmark/MetaHeuristics/benchmark_common.py`：`read_stage2_tasks(...)` 解析 T1 index；`load_input_task(...)` 接收完整 `stage2_task` 和 `objective_config`；`validate_routes(...)` 独立重放候选，默认容差 `1e-5`。
- `EVRPTW_Dataset_Generator/src/evrptw_stage2/artifacts.py`：加载 view 和 parent-index 矩阵。
- `EVRPTW_Core/evrptw_core/objective.py`：`select_objective_distance(...)` 为当前成本目标选择运行时间路径对应的距离矩阵。
- `EVRPTW_Benchmark/MetaHeuristics/benchmark_output.py`：沿用公共 summary、solution 和时间快照输出流程。

**必须通过 `load_input_task` 注入 objective。** 单独调用底层 `load_stage2_instance` 会保留原 `D_dist`；当前任务的距离来源是 `running_time_path_distance_km`（`D_time`）。有向距离、行驶时间和电量矩阵独立保留，不做对称化，不从坐标重建弧，不以距离替换时间或电量。

目标配置来自 `EVRPTW_Benchmark/Reinforcement_Learning/configs/rivian_energy_vehicle_cost_v2.json`：

```text
cost_usd = 413.6331536717643 × started_vehicle_count
         + (0.39 × 0.38910505836575876) × D_time_route_distance_km
```

该目标直接加权车辆费与电费。容量使用 instance 的体积单位；时间使用秒，电量使用 kWh。时间窗约束服务开始时刻，提前到达允许等待，并检查返回 depot 的 deadline。到达充电站后充至满电，耗时按该站 nominal kW × derating 计算；不能把不同功率站统一。每个客户恰好服务一次；空车路线、只有站点的路线、重复或漏客户、内部 depot 等由 canonical verifier 判定。资源和目标的原始数值来自各 instance，不能从典型值推断。

## 已冻结的 cohort 与证据边界

当前数据根目录：

```text
/data/Maojie/ICLR/EVRPTW-DB/EVRPTW_Dataset/Instances_v2/us_11city
```

T1 index 为 `generation_plan/core/test/test1_new_seed/view_index.parquet`，SHA256：

```text
3c2a9d91e00b9e9f130f8e9a4668c0041635de30f55fa98995080b42be045c07
```

`_reconstruction/reconstruction_contract.json` 指向 release `us_11city_full_clean_v7_bbde5db_20260823`，生成代码 commit 为 `bbde5db48dc3f939906fbafdfb18b5b973ae04f1`。目录名本身不作为版本证明。完整出处、哈希和比较结果保存在 [`manifests/iclr_t1_v1/metadata.json`](manifests/iclr_t1_v1/metadata.json)。

| Manifest | Instances | 固定选择规则 |
| --- | ---: | --- |
| [`smoke.jsonl`](manifests/iclr_t1_v1/smoke.jsonl) | 10 | Cus100，10 城各 1 |
| [`panel.jsonl`](manifests/iclr_t1_v1/panel.jsonl) | 60 | Cus500 和 Cus1000，每个尺度 10 城各 3 |
| [`formal.jsonl`](manifests/iclr_t1_v1/formal.jsonl) | 1000 | Cus500 全部 500 + Cus1000 全部 500，各尺度每城 50 |

选择使用固定 base seed `1234`、按 city 分层的稳定 hash 排序，没有按性能选样。每个 solver 的 instance seed 使用旧接口 `stable_view_seed(1234, view_id)`。每行 manifest 包含完整 `stage2_task`、objective、seed、源文件 SHA256 和 dataset identity，可直接交给 runner。

冻结过程已逐字节读取当前实际加载的 7,040 个唯一文件，共 9,194,530,945 bytes；重复 family 的 parent 矩阵只 hash 一次。总 content identity 为 `93ecf4b57fc044feaaa17c83bbd17318f766da68f3ca3a86cf4a4c5a055055ef`。runner 开始每个任务前核对该任务需要的源文件与 index，读取和 hash 耗时单独记录。

需要保留以下可比性限制：

- 现存历史 Neural 证据 `results_legacy/TERRAN_stable_cost_v1/eval_after_val2100_20260912/tests/` 的 T1 index hash 和 Cus500 的 500 个 view ID 及顺序与当前完全一致。该证据证明实际跑过的这份 Neural cohort 匹配，不能证明它就是最终投稿表采用的运行。
- Cus1000 来自相同 index/release，但目前没有证明最终投稿 Neural 的 Cus1000 名单。
- 旧 reconstruction / matrix restore 明确没有执行原始文件 hash 验证。因此本次冻结的是**当前**矩阵字节，不能追溯宣称它们与投稿时矩阵逐字节一致。
- 上述旧 Neural archive 的成本按 `D_dist` 计算，当前任务按 `D_time` 计算。不能直接将这份历史 cost 与本次 solver cost 混入同一比较表；应对相同路线用同一目标重放，或重新运行相同协议。

[`benchmark_bridge/cohort.py`](benchmark_bridge/cohort.py) 支持重新冻结到新目录；现有完整 manifest 不覆盖。`--hash-scope smoke` 可先冻结小范围，但正式 runner 拒绝未完成所需文件 hash 的 job。

## 验证与时间协议

共享 [`tests/test_bridge.py`](tests/test_bridge.py) 已通过 **27 个 unittest**；[`tests/fixtures/index.json`](tests/fixtures/index.json) 包含 **10 个 synthetic instances、21 个 route cases**。测试使用旧 canonical verifier，覆盖有向反弧、服务开始等待、回库 deadline、不同充电功率、容量与恰好一次、空路线、超过 100 个节点，以及加权成本与“先车辆数后距离”不同的情况。进程测试覆盖 ACK 等待计时、晚到候选、不完整 JSON、父 wrapper 提前退出后的子进程清理、最后一行读取、硬 deadline、独立 spawn PID 和单核 affinity。

两个 native evaluator 均已通过全部 21 个公共 route cases 对照，分别记录在 `hma_adapter/evidence/canonical_comparison.json` 和 `routingblocks_adapter/evidence/canonical_fixture_comparison.json`。HMA 另记录容差、move 差值、ASAN/UBSAN 和预算检查；RoutingBlocks 的 `test_native.py` 另检查局部搜索 move 差值、cache 更新、FPO、容差和长路线。后续证据保留在各自 `evidence/`，不以共享测试代替作者内部算法测试。

另有 [`tests/test_queue_lease.py`](tests/test_queue_lease.py) 的 2 项回归通过：coordinator、worker、wrapper 被依次终止时，尚在运行的 native 子进程仍持有全局锁；native 错误退出不会被较慢的 verifier 耗时误标为正常超时。该测试需要本机 Unix socket 传递 FD，已在宿主环境验证。HMA wrapper 的真实 native FD 继承证据见 `hma_adapter/evidence/lock_inheritance.json`。

单个任务的算法预算包括 adapter export、初始化、搜索和阻塞 ACK 所需的 canonical replay 时间；数据读取与冻结 hash 检查单独记录。候选必须携带完整 routes 和真实生成时间，经 verifier 可行后才能成为 incumbent。60 / 300 / 900 / 1800 秒快照只使用对应时刻之前的合法候选，晚到的解不能回填早期快照。独立 watchdog 在单调时钟 deadline 杀掉整个 solver 进程组；wrapper 提前退出也不能遗留后台 native 子进程。内部成本仅用于一致性诊断，最终数值由 canonical objective 重算。

没有可行 incumbent 的任务保留失败/unfinished 状态和空成本；不会把它当作零成本或从总体中静默删除。诊断预算与正式预算分开存储。

## CPU 上限与执行流程

**最新用户授权上限为全局 30 个 CPU 核。** 一次 runner invocation 只有一个 `spawn` ProcessPool，HMA 与 RoutingBlocks 共用 worker 上限，不能各自开 30 个。每个 worker 绑定不同物理核上的一个逻辑 CPU，其 native 子进程继承绑定；`OMP_NUM_THREADS`、`MKL_NUM_THREADS`、`OPENBLAS_NUM_THREADS` 和 `NUMEXPR_NUM_THREADS` 均为 1。coordinator 也限制在已分配的 CPU 集合内。全局 flock 阻止两个 runner 队列同时执行。

[`benchmark_bridge/pipeline.py`](benchmark_bridge/pipeline.py) 按固定顺序推进：

| 阶段 | 全局 workers 目标 | 每个 job 预算 | 两个 solver 合计 jobs |
| --- | ---: | ---: | ---: |
| Cus100 smoke | 8 | 60 s | 20 |
| Cus1000 resource ramp | 8 → 16 → 30 | 各 60 s | 8 → 16 → 30 |
| 固定 city panel | 30 | 1800 s | 120 |
| 全量 T1 formal | 30 | 1800 s | 2000 |

每阶段检查所有 job 完成记录、adapter error、native 宣称可行与 canonical 的矛盾、内部成本差异和线程超限。失败则停止为 `needs_review`，保留原始结果。可行率或相对解质量的高低不作为通过 gate 的条件。每阶段根据观测峰值 RSS 留出 3 倍余量，同时预留系统内存的 15% 或 24 GiB（取更大者）；必要时降低后续 workers。这是基于实际观测的资源保护，不是对任意后续 instance 内存峰值的数学上界。

启动完整流程使用以下命令；如果对应 pipeline 已在运行，应查看状态，不再启动第二个队列：

```bash
/home/exx/anaconda3/envs/maojie/bin/python \
  /data/Maojie/Other_benchmark/benchmark_bridge/pipeline.py \
  --output /data/Maojie/Other_benchmark/results/iclr_t1_author_solvers_20261106
```

只创建计划可加 `--prepare-only`，需要在 panel 后暂停可加 `--stop-after panel`。单个阶段的实际入口示例为：

```bash
/home/exx/anaconda3/envs/maojie/bin/python \
  /data/Maojie/ICLR/EVRPTW-DB/EVRPTW_Benchmark/MetaHeuristics/AuthorSolvers/run_author_solvers.py \
  --manifest /data/Maojie/Other_benchmark/manifests/iclr_t1_v1/panel.jsonl \
  --output /data/Maojie/Other_benchmark/results/manual_panel_NEW \
  --workers 30 --budget 1800 --checkpoints 60,300,900,1800
```

该单阶段示例不能与 pipeline 并行提交。正式阶段需要留出每 solver 1000 个任务的全部记录，不以短预算诊断替代正式 1800 秒结果。

每阶段根目录的 `status.json` 记录进度；`hma/summary.csv`、`routingblocks/summary.csv` 保存每 instance 结果，`time_trace.csv` 保存 checkpoint 结果。各 attempt 保留候选 JSONL、`candidate_audits.jsonl`、solution、stderr、native stop 信息、`resources.jsonl` 和 `resources_summary.json`。输出包含 author revision、adapter/source hash、dataset identity、算法 profile、随机种子、执行 worker 上限与硬件/affinity 证据。

## 源码与 native artifact 冻结

代码修改全部完成后运行 [`benchmark_bridge/freeze_release.py`](benchmark_bridge/freeze_release.py)。开发过程中可先 `--inspect`；创建最终包需要新的唯一 label：

```bash
/home/exx/anaconda3/envs/maojie/bin/python \
  /data/Maojie/Other_benchmark/benchmark_bridge/freeze_release.py \
  --output-dir /data/Maojie/Other_benchmark/releases \
  --label iclr_author_solvers_20261106_v1
```

输出 `.tar.gz`、逐文件 SHA256 的 `.manifest.json` 和包的 `.tar.gz.sha256`。包内保存 bridge、tests 与小 fixtures、adapter 源码/config/build scripts/锁文件、native binaries、上游与适配 wheel、作者 HEAD、完整 tracked binary diff，以及所有必要的 untracked native source 实际内容。后者不会仅靠 `git diff` 推断。还包含当前 cohort/hash manifests 与所调用的 ICLR canonical 源码。归档前后检查文件及作者 git 状态，归档后逐成员验证 SHA256；若冻结期间源代码变化则报错，不能发布混合快照。

源码包包含少量明确列出的正确性报告，包括 HMA 崩溃修复和锁继承报告；其余 `.venv`、编译缓存、大数据集、运行 results/evidence 和 Git 全历史不打包。矩阵需另行恢复并对照 manifest；native binaries / wheels 与当前系统和 Python ABI 有关，迁移机器时使用所附脚本重新构建。源码包本身不声明正式实验已经完成；正式结果和资源证据应另行交付并关联到该源码包的 SHA256。
