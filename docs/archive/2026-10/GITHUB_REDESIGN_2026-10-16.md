# GitHub 参考与本轮改进

调研时间：2026-10-15 至 2026-10-16（北京时间）。关注度取自此次 GitHub REST 仓库元数据的 `stargazers_count`，不是实际用户数，也不是 GitHub 的订阅人数；数字会变化。

## 新增参考项目

| 项目 | 本次星数 | 已查看的官方资料 | 对当前项目的取舍 |
| --- | ---: | --- | --- |
| [DeerFlow](https://github.com/bytedance/deer-flow) | 83,909 | README、todo middleware、run event contract | 借鉴任务完成条件与事件契约；现有 LangGraph/SSE 已覆盖部分能力，暂不引入另一整套框架或多个 Agent |
| [CopilotKit](https://github.com/CopilotKit/CopilotKit) | 38,035 | README、examples 目录 | 借鉴共享状态、结构化交互与人工选择，让助手围绕行程中的具体问题工作；本轮不安装 SDK |
| [TREK](https://github.com/liketrek/TREK) | 14,795 | README、DayPlanSidebarRouteConnector、TimeConfirmModal | 借鉴以每日安排为中心的产品组织、轻量交通连接与固定时间保护；离线写入与协同编辑留后续 |
| [AdventureLog](https://github.com/seanmorley15/AdventureLog) | 3,807 | README | 借鉴行程、住宿、清单和地图组成完整旅行产品的方向；当前已有清单与导出，不重复堆入口 |
| [TRIP](https://github.com/itskovacs/trip) | 1,953 | README、trip-create-checklist-modal | 借鉴简洁 POI 地图、多日行程和结构化清单；保持现有白色系界面 |
| [Timefold Solver](https://github.com/TimefoldAI/timefold-solver) | 1,831 | 仓库信息、官方约束解释文档 | 借鉴硬约束、软目标和可解释的约束诊断；继续使用已有 OR-Tools，不引入 Java 服务 |
| [ChinaTravel](https://github.com/LAMDA-NeSy/ChinaTravel) | 133 | README、symbol_verification/readme | 旅行专项评测补充，明确不称为高星项目；借鉴独立事实、时序和约束检查，不能用偏好分掩盖无效行程 |

这些项目属于旅行产品、Agent 交互和约束优化三个不同方向。高星不证明旅行排程正确；项目 README 宣称的功能也不等于本项目已经验收了同等能力。

关键源码与文档：

- [TREK 交通连接组件](https://github.com/liketrek/TREK/blob/main/client/src/components/Planner/DayPlanSidebarRouteConnector.tsx)：相邻地点之间显示轻量交通摘要，酒店出发/返回是独立连接。
- [TREK 时间变更交互](https://github.com/liketrek/TREK/blob/main/client/src/components/Planner/DayPlanSidebarTimeConfirmModal.tsx)：涉及已设时间时单独提示。
- [DeerFlow todo middleware](https://github.com/bytedance/deer-flow/blob/main/backend/packages/harness/deerflow/agents/middlewares/todo_middleware.py)：未完成任务与模型最终回答是不同状态。
- [DeerFlow 事件契约](https://github.com/bytedance/deer-flow/blob/main/contracts/run_event_stream_contract.json)：序号、生命周期与兼容规则；当前事件系统未来可向此类明确契约演进。
- [Timefold 约束解释](https://docs.timefold.ai/timefold-solver/1.x/constraints-and-score/understanding-the-score)：解释哪些实体、哪些约束影响方案，而不只显示一个总分。
- [ChinaTravel 校验分类](https://github.com/LAMDA-NeSy/ChinaTravel/blob/main/chinatravel/symbol_verification/readme.md)：地点、交通、时间、费用等维度分开验证。

本轮没有复制第三方实现或资源，没有新增这些项目的依赖。TREK、AdventureLog 的代码许可与本项目不同；参考产品模式后独立实现。

## 当前代码的真实基线

已有：精简 POI 输出、并行工具、patch 修复、连接与路线缓存、SSE、可取消持久任务、单日求解、拖拽/跨日编辑、天气备选、换乘与轨迹、冲突方案、手册和日历导出。旧调研里的“尚无持久任务”“只能重排行程”已不适用于当前代码。

当前优先问题：

1. 时间冲突只有一句超时提示，用户不清楚时间花在哪。
2. 旧整体建议可能写着“轻松”，即使修改后的时间线已经超时。
3. 查询覆盖率不体现数据新鲜度；已查到交通不等于当前实时交通。
4. 模型修复拿到文字冲突，但缺少统一的分项时间预算。

## 本轮已实现

### 程序解释时间预算

新增 `schedule_diagnostics.py`，纯读取当前版本，复用时间校验器已保存结果，无模型或地图请求。输出游览、用餐、已知交通、逐段缓冲、预约/餐次等待、每日可用时间和超时，以及最长已核实路段。

缺失交通时不返回剩余时间；预约冲突即使总时长未超限也不返回可用余量。未核验天只显示未核验。24 小时更新提醒是产品规则，独立于缓存 TTL；缺查询时间也明确提醒，不伪造时间戳。它不是未来交通预测、营业时间验证或预约保证。

`GET /api/trips/{id}/checks` 保留原 `issues`，新增 `version` 和 `days`。沿用归属检查，不修改行程/版本；前端拒绝应用已过期请求的诊断结果，编辑草稿期间隐藏已保存版本的预算。

### 让结论跟随当前状态

每日卡片在时间线前显示简洁预算条和最长路段提示，直接连接已有“调整方案”或“重新校验”。结果页显示旧交通更新提醒。

整体建议首先由当前时间线确定：时间冲突、交通不完整，或按已知条件通过。原模型建议仍保留为折叠内容，并标明可能未反映后续修改。诊断服务暂不可用时仍可查看原时间线。

### 修复使用同一份数值证据

冲突修复提示新增同一服务的时间预算与最长路段信息，保留两次修复上限、结构化 patch 和独立重新校验。不靠额外模型调用生成解释，也不自动删除用户已有地点。提示要求不能用编造交通、删除餐饮或擅自延长窗口来掩盖冲突；最终仍由现有校验链验收。

## 后续顺序

1. 让初次规划更容易可行：固定样例评估后，引入分区域分日和交通预算预筛，用户已有行程仅在明确要求时跨日调整。
2. 编辑重算进一步缩小到受影响路段，分别量化冷缓存、热缓存与地图限流下的耗时。
3. 结构化方案比较：展示保留了什么、改了什么、各自的时间代价，再采用并保存版本。
4. 待实际移动使用需求明确后再做 PWA 和离线同步，保留幂等键和版本冲突处理；当前离线手册已满足基础离线阅读。

本轮不宣称首次生成可行率或速度已提高；提示调整是否提高修复成功率，需要多次真实样例评测。

## 本轮验证

- 后端 104 项测试通过（原 96 项，新增 8 项），覆盖分项合计、等待时间、预约迟到、缺失交通、时间戳、只读版本与归属，以及修复提示使用相同预算。
- 前端 TypeScript 和生产构建通过，原有首页主包 851 KB 的体积提示仍在，本轮未改善首页包体。
- 浏览器读取原北京三日行程 v3，第一天显示超出 94 分钟；第二天显示按已知条件剩余 56 分钟；第三天超出 38 分钟，均与旧时间线一致。
- 18 段历史交通均触发超过 24 小时提醒；整体建议显示第 1、3 天冲突，原生成建议折叠保留。
- 未提交用户行程版本、未重新查询用户坐标、未发起真实模型生成；当前校验验证的是诊断正确性，不证明修复成功率提升。
