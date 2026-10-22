# GitHub 学习：行程编辑、方案比较与真实进度

调研日期：2026-10-16。通过 GitHub 连接器实时读取公开仓库元信息、README 和下列源码。Star 数为查询时快照，不代表功能质量或性能。本轮只新增研究文档，以下设计尚未实现。

## 1. 本轮参考项目与源码

| 项目 | Star | 本轮关注点 | 当前项目可借鉴的部分 |
| --- | ---: | --- | --- |
| [TREK](https://github.com/liketrek/TREK) | 14,798 | 地点详情、每日编辑 | 在上下文中编辑一个地点，操作后保留定位和焦点 |
| [assistant-ui](https://github.com/assistant-ui/assistant-ui) | 12,564 | Vue 组件与运行时分离 | 展示组件只消费状态，工具结果采用专用卡片 |
| [AG-UI](https://github.com/ag-ui-protocol/ag-ui) | 16,605 | 运行、步骤、状态事件 | 明确当前阶段、错误、取消和结束，避免靠文字猜进度 |
| [Agent Chat UI](https://github.com/langchain-ai/agent-chat-ui) | 3,219 | 人工接受、编辑、拒绝 | 建议先预览，再由用户采用；它的领域相关性高于 Star 排名 |
| [Open WebUI](https://github.com/open-webui/open-webui) | 155,827 | 结构化追问 | 对存在歧义的调整给出有说明的少量选项 |

### TREK：编辑一个对象，保留操作上下文

读取 [PlaceInspector.tsx](https://github.com/liketrek/TREK/blob/main/client/src/components/Planner/PlaceInspector.tsx) 和 [DayReorderPopup.tsx](https://github.com/liketrek/TREK/blob/main/client/src/components/Planner/DayReorderPopup.tsx)。前者以选中地点为中心提供详情、编辑、日期归属；后者把整日排序放在专门弹层，并在删除确认取消后恢复列表位置和焦点。整日删除前还展示关联内容的影响。

适配：在日程卡片上选择景点、餐厅或酒店后打开同一个详情面板；只展示当前类型需要的字段。编辑结束返回原来的日期和滚动位置。保留现有手动排序、锁定、草稿撤销能力，移动端继续提供上下移动按钮。

TREK 为 AGPL-3.0 项目，借鉴交互思路并独立实现；直接引入代码需另行评估许可证义务。

### assistant-ui：已有 Vue 支持，但不急于替换我们的运行时

读取 [Vue README](https://github.com/assistant-ui/assistant-ui/blob/main/packages/vue/README.md)。当前仓库确实提供 Vue 3.5+ bindings、composables 和无样式 primitives，包括取消、分支和工具结果组件。README 的默认 AI SDK 接法还安装 React 作为内部运行时依赖；自定义 RuntimeAdapter 则说明了 standalone shim 路径。

适配：先学习状态和显示分离、按工具类型渲染卡片的方式。现有 Vue 3.5 + FastAPI + 自有 SSE 能继续使用。暂不为增加聊天组件引入另一套状态与流协议；也不把整个产品改回通用聊天界面。

### AG-UI：把工作阶段变成有类型的数据

读取 [schemas.ts 入口](https://github.com/ag-ui-protocol/ag-ui/blob/main/sdks/typescript/packages/core/src/schemas.ts) 及 [生成的事件 schema](https://github.com/ag-ui-protocol/ag-ui/blob/main/sdks/typescript/packages/core/src/generated/schemas.ts)。源码区分 RunStarted、RunFinished、RunError、StepStarted、StepFinished、StateSnapshot、StateDelta 等事件，运行事件包含 threadId/runId，并提供运行结束结果类型。

适配：在现有 SSE 上逐步补充稳定的阶段标识与步骤状态；不要求迁移协议。运行 ID、版本、重连、取消语义需要由本项目端到端定义，不能仅靠使用事件名称获得可靠性。

### Agent Chat UI：人工决策是一种明确的状态

读取 [use-interrupted-actions.tsx](https://github.com/langchain-ai/agent-chat-ui/blob/main/src/components/thread/agent-inbox/hooks/use-interrupted-actions.tsx)，它根据中断内容构造 approve/edit/reject 决策，再通过 resume 继续工作。另读 generic-interrupt 和 tool-call-table：通用兜底会展示对象表格，这不适合直接给旅行用户看。

适配：采用“看建议 → 查看变化 → 采用 / 继续修改 / 保留原行程”。用地点名、日期、停留时间和交通耗时呈现差异。用户采用后的成功提示应等待服务端真正保存成功，而非仅提交动作。

### Open WebUI：有歧义时用少量选项追问

读取 [AskUserCard.svelte](https://github.com/open-webui/open-webui/blob/main/src/lib/components/chat/AskUserCard.svelte)，其问题包含 id、标题、选项说明和自定义回答，答案按问题 ID 保存。源码中选择选项可能直接进入下一题并在末题提交，因此不能把这种行为照搬到行程修改的最终采用。

适配：“第二天轻松一点”可追问“减少景点 / 保留景点并缩短停留”；“加一家饭店”且位置明确时直接检索推荐，避免每次都重复问卷。追问取消保留原计划，选择偏好后仍先预览行程改动。

## 2. 与当前代码的差距

- `ConflictFixPanel.vue` 已能列出校验后的方案，但主要显示标题、说明和采用按钮，缺少可扫描的前后对比。`conflict_fix.py` 已返回候选完整 day，因此展示差异不必额外请求模型。
- `Result.vue` 已有草稿撤销、历史版本、采用后服务端校验；这些应复用。冲突修复的候选预览可以先接入，不能误认为 Agent 的所有调整已支持预览：`planning_runs.py` 的调整任务目前会直接提交结果。
- `DayArrange.vue` 集中处理景点排序，餐饮、住宿分散在其他编辑区域。下一步应统一编辑入口与布局，仍按不同类型校验各自字段。
- `planning_runs.py` 已有持久任务、阶段文字、查询次数、真实 elapsed_seconds 和取消。`saved_trips.py` 已发送 SSE 运行快照。本轮建议补充结构化步骤、完成数量和恢复语义，不重复重建已有任务系统。
- 当前通知使用进程内 Event，代码明确面向单 worker。若将来部署多 worker，需要外部通知机制或可靠的数据库轮询恢复；不能把引入 AG-UI 等同于解决多进程任务问题。

## 3. 建议实施顺序

### 第一批：调整方案的前后对比

先升级现有冲突修复面板，范围小且用户收益直接。

1. 每张方案卡展示新增、移除、顺序变化、停留时间变化；按稳定 item_id 比较，不按名称或数组位置猜测。
2. 显示调整前后当天结束时间、超时分钟和交通耗时；没有经过有效校验的值显示缺失原因，不填 0，也不把移除的停留时间当作节省的总耗时。
3. 桌面可查看原方案与候选方案；手机采用上下对比。地图预览候选路线时保持“预览中”标记，关闭即可回到原方案。
4. 采用时仍走现有保存、版本校验与重算；预览绑定 base_version，期间原行程变化则重新生成候选。失败保留预览和原计划。

验收：仅预览不会生成版本；采用一次只产生一个版本；撤销恢复原值；过期候选不会覆盖新行程；缺失交通数据不会显示为已确认节省。

### 第二批：统一地点编辑与替换

建立一个地点详情面板，景点显示日期、停留、预约、锁定，餐厅显示餐次和位置，酒店显示入住归属和位置。搜索、我的手动填写、询问助手共用入口，但区分经过地图确认的地点与尚无可靠坐标的自填地点。

替换时先展示受影响的行程段和时间变化；使用现有草稿及撤销，不给每一个输入框单独触发模型请求。移动端优先抽屉与显式按钮，关闭后恢复触发元素焦点。

验收：景点、餐厅、酒店都能独立新增或替换；未保存操作可撤销；锁定和预约约束继续有效；替换后受影响交通必须重新校验。

### 第三批：结构化进度与必要追问

在现有运行快照上增加 stage_code、步骤状态与有明确分母的完成数量，展示“理解需求 → 确认地点 → 安排每天 → 校验交通 → 保存”。真实总工作量未知时显示阶段和已用时间，不编造百分比。动态修复可能回到交通校验，界面应允许这种状态变化。

重连时读取服务端当前快照，再接续事件；终态不可被迟到事件覆盖。若以后增加事件序号，应明确其持久化和恢复规则。对必须由用户决定的歧义增加可取消的问题卡，答案后生成候选，采用才保存。

验收：刷新与断线可恢复正在运行的任务；取消后迟到结果不提交；失败显示已完成阶段和具体可重试操作；追问期间不静默覆盖原行程。

## 4. 实施边界

优先采用现有组件、服务与版本机制，先改善结果的可理解性，再扩展交互。上述顺序是结合源码和当前项目作出的设计判断，并非这些仓库对本项目性能提升的实验证据。本轮未安装依赖、未改应用代码、未修改已有旅行数据，也未执行应用测试。
