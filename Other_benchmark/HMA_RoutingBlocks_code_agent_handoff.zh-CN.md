# HMA 与 RoutingBlocks：给 code agent 的实现交接

## 目标与分工

本轮确定加入 HMA 和 RoutingBlocks 两个搜索比较。请在实际 benchmark 工程中，基于作者代码完成任务适配、接入现有 runner 和独立 verifier，并形成可追溯的运行结果。不是单独做一次数据格式转换。

代码实现、编译、依赖、loader／runner 接入和实际执行由 code agent 在其工程中完成；本交接给出研究合同与验收标准，随后审查代码差异、验证报告和预算内结果。当前交接文件已准备，适配代码尚未实施。

## 1. 开工先确认的项目事实

请先找到并记录实际工程中的旧版实例 loader、任务／费用配置、独立 verifier、原 ALNS/VNS/TS runner、正式 test manifest 和现有输出结构。遵守该工程 AGENTS.md，不另写一套含义不同的评测器。

主数据必须是提交稿旧版。新版 generator 目录、历史样本和旧输出文件名不能单独证明其对应投稿版本；绑定 release／文件 hash、实例与 family ID，以及现有 Neural 测试使用的同一 cohort。若工程中的实例路径或接口不同，按实际接口实现，并将映射记录在交付说明中，不猜测 API。

本次主尺度为 Cus500、Cus1000 T1；Cus100用于小规模检查。先验证少量实例，再进入固定城市分层面板，最后按确定配置执行正式 cohort。既有计划中的诊断面板约每尺度30例；正式比较优先使用原稿同一500例。实际完成多少就报告多少，不将面板称为完整cohort。

## 2. 作者代码与命名

| 实现 | 起点 | 本轮核对的版本 | 报告名称 |
|---|---|---|---|
| HMA | https://github.com/0SliverBullet/EVRP-TW-SPD-HMA | `0ebbcaa4c2b8385c2c9bff06d60689cb2c64229d` | HMA adapted to the benchmark reference task |
| RoutingBlocks | https://github.com/tumBAIS/RoutingBlocks 的 `examples/evrptw` | `2d2ee06c79b5d458b1aeeafd95a2b42b5441079a` | RoutingBlocks EVRPTW ALNS adapted variant |

取得代码后核实并固定实际使用 revision；如选其他版本，列出差异与理由。RoutingBlocks的简单`examples/alns`／`examples/ils`不是本轮选定实现。保持作者原生搜索结构与可行初始化，必要任务适配之外的性能增强单独标注。主比较不用Neural warm-start。

## 3. 必须对齐的共同任务

以现有 loader／verifier 与提交版本配置为准，不以外部仓库默认值覆盖它们。

| 项目 | 要求 |
|---|---|
| 客户 | 每个customer是一个服务地点，可有多件包裹；恰好服务一次 |
| 货量 | 沿用原实例体积需求和车辆容量；所有换算保持一致，不用包裹数替代体积 |
| 时间窗 | hard TW、等待、服务时间、depot营业时域和回仓期限保持原语义；统一绝对／相对时钟 |
| 有向旅行 | 直接使用原矩阵；调度用最快时间路径的time，成本与能耗用该同一路径的distance |
| 能耗 | 按原任务单位距离能耗传播；不随solver内部缩放重新改变物理含义 |
| 车辆／线路 | 同质车、单趟depot-to-depot、无硬车队上限；K仅计服务客户的非空路线 |
| 电池 | 每车以原任务满电出发，沿途电量非负；到CS后恢复当前规定容量上限 |
| 充电 | 强制full recharge；时间为实际补入能量除以原站点有效功率，注意秒／小时与derating只应用一次 |
| 设施 | 使用原depot和CS roster；不增删站点、不取平均功率替代原值、不引入排队 |
| 目标 | `C = F_day * K + alpha * D`，读取原版本全精度系数；不是最短距离，也不是车辆数优先的字典序 |
| 最终评分 | 完整路线交给既有独立verifier重新判断可行性并计算C/K/D，不采用内部penalty作为最终obj |

禁止为匹配算法而对称化矩阵、改成欧氏距离、做额外最短路closure后覆盖实例，或添加未披露的硬车数／站点访问次数限制。库或算法确实保留的构造／搜索限制应作为实现身份披露，不冒充共同任务假设。

## 4. HMA 必查代码点

1. **SPD→delivery-only**：pickup置零，核查载量传播、构造、split／重组和可行性检查，不只改输入字段。
2. **partial→full**：route evaluator、局部move增量／缓存、充电站插入、修复和输出必须采用相同满充传播。不能在输出之后补满电而让搜索仍按partial评价。
3. **充电速率单位**：原`recharging_rate`为时间／能量型量，不能将kW直接填入；适配原站点不同有效功率。
4. **Floyd与hyperarc**：原Data流程会经站点计算最快路径并覆盖time/dist，同时初始化统计量及hyperarc。拆分必要初始化与矩阵改写；不能简单注释整个函数后留下未初始化依赖。检查依赖三角不等式的剪枝是否仍正确。
5. **成本评价**：统一固定车费与距离费用，包括插入、接受、best解更新和路线数量变化。内部搜索penalty可保留，但不能混入最终C。
6. **容量相关数组**：检查`MAX_NODE_IN_ROUTE=100`、`MAX_POINT=1250`、`MAX_STATION_POINT=105`的真实数组／缓存用途。这些常量尚未被认定为实际路线约束，必须查用法；不能静默截断长路线或客户、也不能借此人为增大车数。
7. **停止逻辑**：`g_1`是连续未改善阈值，并参与个体／种群分解流程，不是总代数。保留或明确说明修改；不随意调大以凑满时间。每个独立实验job优先`runs=1`并显式seed，避免将作者示例10次重复的best当单次结果。

## 5. RoutingBlocks 必查代码点

1. **选择完整示例**：从`examples/evrptw`接入，而非将100次迭代的简单演示当作完整baseline。
2. **改掉与任务不符的字典序最优更新**：原示例对best feasible优先比较路线数。检查`_accept_solution`、best/current解、改善计数、vehicle-reduction机制和局部评价，使主配置实际优化同一weighted C。只在最终导出时重算C不够。
3. **full charge evaluator**：需要时实现／扩展原生资源评价；满充、站点功率与充电耗时应进入构造、LocalSearch、FacilityPlacementOptimizer及可行性恢复，而非仅在Python外层打补丁。
4. **fleet与空路线**：不要继承一个不适用于本任务的固定fleet_size上限；用于后端存储的预分配大小与任务约束分清。K不计空路线和仅站点路线。
5. **矩阵与候选限制**：保持directed time/distance语义，核对operator反转弧、候选图和缓存；记录原生granularity参数，不为同名算法悄悄添加增强。
6. **停止条件**：完整示例可同时按时间、总迭代和连续未改善迭代停止；保留并披露实际设置。不要把简单demo固定迭代次数等同共同计算预算。

## 6. 在线预算与可追溯输出

统一使用单调墙钟记录在线预算，计入算法内预处理、初始化、合并、搜索、充电修复和子调用。文件读取与外部verifier按现有协议处理，分别记录耗时，不修改全局评测口径。

HMA原`clock()`是CPU计时且Data预处理在搜索timer前；RoutingBlocks构造／邻域预计算也在run timer前，外层检查无法中断单轮长LocalSearch。因此不能直接把命令行time参数当作已满足本协议。

主预算为最多1800秒，保存60/300/900/1800秒之前已生成的完整候选及其生成时间。超时后完成的改善不得计入早期预算；外部verifier稍后验证不会改变候选生成时间。记录实际运行时、超额时间、早停原因；如增加预算内重启，作为明确variant并计入总预算。

输出沿用现有项目schema；字段映射至少包括：实例／family／城市／日型、release hash、solver revision、适配revision、配置、seed、CPU线程／硬件、checkpoint时间、候选生成时间、完整路线、内部obj与独立C/K/D、FR、充电次数／时间、验证失败类型、阶段耗时与早停原因。失败不能赋假成本，也不能称为已证明任务不可行。

搜索seed及参数在正式test前固定。报告每次独立预算的结果与离散程度，不能将多seed最佳值包装成单次1800秒。

## 7. 有意义的验证

- 原作者兼容小实例的编译与运行检查，保留原版结果作追溯；不据一次smoke run声称复现了文献性能。
- 目标任务检查至少覆盖：非对称弧与路线反转增量、hard TW与等待／回仓、满充传播、不同站点功率、体积容量、客户恰好一次、空路线计数，以及跨路线move改变K时的weighted cost。
- 对候选move与整条输出，用现有canonical evaluator／verifier核对增量与重算。抽查长路线，避免100节点数组假设造成截断。
- 检查60秒等截止点之前与之后的候选记录；用模拟短预算确认超时改进不会回填较早checkpoint。
- 小规模验证通过后，在预先确定的城市分层面板检查FR、初始／中间／最终K与成本，再扩到正式cohort。结果变好或变差都保留，不以必须胜过／落后Neural为验收条件。

## 8. 交付与审查顺序

请交付：

1. 两个solver的固定来源、patch／commit和改动清单，区分必要任务适配与额外增强。
2. 实际工程入口、环境／构建信息、可复现单实例与批量运行方式。
3. 小实例验证报告、失败case与原始路线／verifier日志。
4. 固定面板清单、按seed／checkpoint保留的完整输出与阶段耗时。
5. 正式cohort运行清单，以及真实状态：命令已准备、任务已提交、运行中、完成，分别记录。

先审模型与预算语义，再审数值和性能。代码通过、路线通过回放、solver有竞争力是三个不同层次，不能相互替代。论文中两者均称适配后的作者代码基线，不声称与原生算法完全等价。

来源与停止规则的详细核对见 [E2执行矩阵](E2_new_solver_run_matrix.zh-CN.md)；原ALNS/VNS/TS的初始化／限制敏感性仍是独立任务，不被新增两个solver替代。
