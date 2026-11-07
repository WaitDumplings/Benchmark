# Benchmark

ICLR EVRPTW 的 HMA 与 RoutingBlocks 作者代码适配、独立校验和多进程评测代码。

| Solver | 作者仓库 | 固定 revision |
| --- | --- | --- |
| HMA | [0SliverBullet/EVRP-TW-SPD-HMA](https://github.com/0SliverBullet/EVRP-TW-SPD-HMA) | `0ebbcaa4c2b8385c2c9bff06d60689cb2c64229d` |
| RoutingBlocks | [tumBAIS/RoutingBlocks](https://github.com/tumBAIS/RoutingBlocks) | `2d2ee06c79b5d458b1aeeafd95a2b42b5441079a` |

报告名称分别为 **HMA adapted to the benchmark reference task** 和 **RoutingBlocks EVRPTW ALNS adapted variant**。RoutingBlocks 使用完整 `examples/evrptw`，保留作者搜索结构；两套实现均适配有向原始矩阵、delivery-only、hard TW、站点功率各异的 full recharge，以及车辆费和电费的加权目标。

## 目录

- [`Other_benchmark/vendor/`](Other_benchmark/vendor/)：适配后的作者源码快照，保留原文件声明。
- [`Other_benchmark/hma_adapter/`](Other_benchmark/hma_adapter/) 与 [`routingblocks_adapter/`](Other_benchmark/routingblocks_adapter/)：构建、导出、求解入口、配置、patch 与来源记录。
- [`Other_benchmark/benchmark_bridge/`](Other_benchmark/benchmark_bridge/)：统一 loader/verifier、墙钟预算、候选回放、全局进程队列及阶段控制。
- [`Other_benchmark/tests/`](Other_benchmark/tests/)：公共任务语义、超时、进程锁回归与小型人工 fixtures。
- [`Other_benchmark/manifests/`](Other_benchmark/manifests/)：固定 Cus100 检查、Cus500/Cus1000 面板和正式 T1 清单、文件哈希。
- [`ICLR/EVRPTW-DB/`](ICLR/EVRPTW-DB/)：本次实际使用的 canonical loader/verifier、目标配置及其 Python 源码依赖。
- [`provenance/`](provenance/)：上游 revision、完整 tracked patch、代码快照哈希。

完整任务合同、预算口径及数据证据边界见 [`Other_benchmark/README.md`](Other_benchmark/README.md)。仓库只保存代码、配置、清单和小型验证资料；大数据集、实际实验输出、虚拟环境和二进制构建产物不在 Git 中。

## 构建与检查

`vendor` 是普通源码目录，GitHub clone 不会带作者仓库的 `.git`。运行需要上游 revision 核验的入口前，按 [`docs/RESTORE_AUTHORS.md`](docs/RESTORE_AUTHORS.md) 恢复作者 Git 元数据；恢复脚本保留当前适配源码。

```bash
python scripts/restore_author_git.py
bash Other_benchmark/hma_adapter/build.sh
bash Other_benchmark/routingblocks_adapter/build.sh
python Other_benchmark/tests/test_bridge.py
python Other_benchmark/tests/test_queue_lease.py
```

Python 公共依赖包括 NumPy、pandas 和 PyArrow；RoutingBlocks 构建依赖固定在其 [`build.sh`](Other_benchmark/routingblocks_adapter/build.sh) 和 [`requirements.lock`](Other_benchmark/routingblocks_adapter/requirements.lock)。锁继承测试使用本机 Unix socket，需要允许该 IPC。

该快照保留原实验机的 Python 解释器路径 `/home/exx/anaconda3/envs/maojie/bin/python` 和清单中的数据路径。换机器部署时需要匹配这些路径，或显式修改并生成新的来源指纹；原始矩阵需单独恢复并核对清单哈希。源码快照不意味着原投稿矩阵字节已被追溯证明一致。

## 运行

每个求解任务最多使用一个计算线程，外层全局最多 30 个任务。两个 solver 共用同一队列；同一实验不要同时启动不同工作区中的求解队列。

先只跑 HMA 的完整 T1 清单：

```bash
python Other_benchmark/benchmark_bridge/run.py \
  --manifest Other_benchmark/manifests/iclr_t1_v1/formal.jsonl \
  --output Other_benchmark/results/hma_formal \
  --solvers hma --workers 30 \
  --budget 1800 --checkpoints 60,300,900,1800
```

RoutingBlocks 使用 `--solvers routingblocks`；同时评测时使用 `--solvers hma routingblocks`。正式清单每尺度 500 例，共 1,000 例，每 solver 各运行一次；预检和面板结果单独存放。运行状态、可行路线和成本以实际输出及独立 verifier 为准，不能把代码检查通过当作正式测试完成。

## 来源与授权

见 [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md)。本仓库没有给全部第三方代码统一换发许可证。HMA 的固定上游快照未提供明确许可证；RoutingBlocks 及内嵌依赖保留各自的 MIT/BSD 声明。
