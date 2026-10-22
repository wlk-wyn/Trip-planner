# Trip Planner 下一阶段改进设计

调研日期：2026-10-08。依据：上一个对话「熟悉 Trip Planner 项目」及当前工作区源码。本文保留设计方案；第一轮实施结果见下方记录。

## 第一轮实施记录（2026-10-08）

已在本地实现独立攻略来源、地点卡片、必去／可选／忽略、整张截图 OCR、后续对话读取原攻略、SQLite 多行程保存、手动／DIY／Agent 版本提交、修改摘要、撤销恢复和旧行程迁移。明确指定修改某一天时，程序保留其他日期；POI 查询和匹配保留 ID 并区分同名分店。

验证：23 项后端离线测试、前端类型检查与生产构建通过。浏览器使用明确标注的离线示例验收了来源添加、地点优先级、手动保存、撤销、刷新及后端重启后的读取；390px 布局无横向溢出。临时验收页面已移除，保留一份标为“界面验收示例”的本地旅行以便查看版本界面。

运行中的本地前后端已加载最终代码。未部署、未调用真实模型完成端到端联调。SSE、运行任务断线恢复、时间轴与路线可行性、费用新模型、账号及跨设备同步仍在后续阶段；当前仅恢复已保存结果。

以下“现状”和接口草案描述设计制定时的起点，实际新增接口与运行方式以 README 为准。

## 目标与现状

下一版目标：用户把收藏的攻略交给 Agent，得到可核实、可调整、可恢复的旅行计划。

已完成并应继续保留：

- 首页一句话理解需求，缺少城市或日期时追问，详细表单可选。
- 主输入框旁的小红书参考入口：正文、链接、截图裁剪与 OCR。
- LangGraph 工具调用和校验修复；新增地点必须经过高德查询。
- 结果页对话、手动编辑、DIY、地图交通路线、图片和 PDF 导出。

上一轮本地改动尚未部署。本次未复测运行中的服务或线上版本；这里描述的是源码状态。

源码中影响下一版的缺口：

| 当前实现 | 用户影响 | 改进 |
| --- | --- | --- |
| `TripRequest.reference_content` 是合并后的纯文本；URL/OCR 追加标签但没有独立来源对象 | 难以删除单篇攻略、查看地点来自哪篇笔记 | 来源对象与地点卡片 |
| `TravelAgent.run()` 修改行程时排除了 `reference_content` | 后续调整无法直接读取原攻略正文 | 在行程会话中保存来源和已确认约束 |
| `workflow.compile()` 未接持久化；前端保存到 `sessionStorage` | 关闭浏览器会话后难以继续，多份行程会覆盖当前槽位 | 按行程 ID 保存状态和版本 |
| 对话更新直接替换完整计划 | 不容易看出改了什么、撤销误改 | 差异摘要、版本快照、撤销 |
| 地图交通时间主要在结果页计算；模型只有搜索地点和天气工具 | 有真实地点不等于一天内走得完 | 生成阶段的路线与时间校验 |
| 地点证据主要按名称索引；高德解析未保留 POI ID | 同名地点和分店难以可靠区分 | 用 POI ID 管理已核实地点 |
| 费用缺证据时被设为 0 | 数据层无法区分未知价格和免费 | nullable 费用和来源状态 |

## GitHub 参考与取舍

星数为调研时 GitHub 页面显示的约数，随时间变化。通用 Agent 项目与旅行项目分开评估；功能相似的小型 demo 不作为架构依据。

| 项目 | 页面星数 | 已核实的参考点 | 对本项目的应用 |
| --- | --- | --- | --- |
| [AdventureLog](https://github.com/seanmorley15/AdventureLog) | 3.8k | 地点管理、地图、多日行程、笔记、分享和协作 | 分开管理攻略地点收藏与已排好的每日行程，建立“我的旅行” |
| [Vercel Chatbot](https://github.com/vercel/chatbot) | 21.0k | README 明确包含结构化输出、工具调用、动态聊天界面和数据持久化 | 对话围绕当前行程持续开展；保存聊天和附件，不迁移到 React/Next.js |
| [LangGraph](https://github.com/langchain-ai/langgraph) | 42.8k | 状态持久化、恢复、流式执行等基础能力 | 在已有图上接入会话与任务状态，保留有边界的 Agent 循环 |
| [GPT Researcher](https://github.com/assafelovic/gpt-researcher) | 29.9k | 汇总资料时保留来源，报告带引用，提供任务进度 | 攻略原文 → 结构化候选 → 地图核实 → 行程中可追溯的引用 |
| [ITINERA](https://github.com/YihongT/ITINERA) | 68，专项补充 | 自然语言需求分解、候选 POI、空间聚类和顺序优化 | 同区域分日，再计算交通与时间可行性 |

ITINERA 不属于高星项目，保留它是因为空间路线设计与本项目直接相关。以上采用交互与工程思路；本方案没有复制项目源码。

参考依据：

- [Vercel Chatbot README](https://github.com/vercel/chatbot/blob/main/README.md)
- [GPT Researcher README](https://github.com/assafelovic/gpt-researcher/blob/master/README.md)
- [ITINERA README](https://github.com/YihongT/ITINERA/blob/main/README.md)
- [LangGraph 持久化说明](https://docs.langchain.com/oss/python/langgraph/persistence)：checkpointer 保存单个会话图状态，store 保存跨会话资料；二者不能代替产品行程版本模型。

## 推荐交互

### 1. 攻略变成可操作的地点卡片

用户输入：“杭州两天，参考这两篇小红书，想轻松一些。”

1. 正文、链接、截图均进入同一附件区，每篇来源独立显示处理状态。
2. 截图默认识别整张，裁剪作为可选操作；识别结果可修正。链接失败时保留附件并提示粘贴正文。
3. 自动抽出地点、偏好和注意事项。地点卡片显示“西湖博物馆 · 来源：攻略 1 · 地图已匹配”。
4. 每张卡片可设为“必去／可选／忽略”；重名、城市冲突或多个分店时才追问。
5. 未操作的候选默认可选，不增加一轮必须完成的表单。
6. 结果中显示攻略地点的采用情况；未安排的必去项必须说明冲突，不能静默丢掉。

用户文案使用“按攻略安排／攻略为主／仅参考风格”，对应现有 strict、hybrid、flexible。

事实需要分开核实：地图匹配只能证明地点信息，不能证明原文中的营业时间、价格、预约规则有效。攻略摘录显示来源，缺少事实证据时显示待确认。

### 2. 对话修改有差异和撤销

桌面结果页建议采用：左侧每日行程、右侧地图，Agent 对话抽屉可展开；手机用“行程／地图”切换和底部对话入口。保留现有编辑和导出。

用户说“第二天下午轻松一点”，Agent 返回简短摘要，例如：“移除一个可选地点，保留博物馆；第二天路线已重新检查。”仅有可靠路线计算时才显示节省的具体分钟数。

普通调整仍自动应用，并提供“查看修改／撤销”。日期、城市变化进入新方案分支，保留原计划。锁定的地点、时间和预算约束由程序校验，不能仅依赖模型提示词。

手动编辑、DIY 和 Agent 修改统一走版本提交；不能只有聊天可撤销。未受影响的天和用户备注保持不变。先做完整快照和确定性比较，再考虑局部 patch，避免过早增加合并复杂度。

### 3. 计划能实际执行

顺序：先确定候选地点，再按区域分日，使用真实交通查询校验游览时间、交通、用餐及缓冲是否超出当天可用时间。

- 增加统一 `ScheduleItem` 时间轴，承载景点、餐饮、住宿与交通，减少前端重新拼装顺序带来的偏差。
- 用户固定的预约时间和必去地点优先；无法同时满足时明确给出冲突和备选。
- 查询交通失败时标记“交通时间未核实”，不能用直线距离估算冒充真实查询。
- 开放时间无证据时标记待确认；不阻止草稿生成，但不宣称全部校验通过。
- 超时后优先调整可选项，并说明原因，禁止无提示地删除必去地点。

## 数据与接口草案

这些均为拟新增模型和接口，不是当前已实现的能力。

| 对象 | 关键字段 |
| --- | --- |
| TripSession | id、request、constraints、source_ids、current_version、schema_version |
| ReferenceSource | id、type、title、url、text、status、error、created_at |
| ReferenceCandidate | id、source_ids、original_name、matched_poi_id、match_status、priority |
| PlaceEvidence | poi_id、city、name、address、location、provider、checked_at |
| ScheduleItem | id、kind、place_id、start_time、duration_minutes、locked、source_ids |
| RouteSegment | from_id、to_id、mode、duration_minutes、distance_meters、status、checked_at |
| PlanVersion | trip_id、version、parent_version、plan_snapshot、change_summary、origin |
| PlanningRun | id、trip_id、status、stage、last_event_id、error |
| CostItem | amount 或 null、currency、status、source_id；status 区分已核实／估算／未知 |

建议接口：

- `POST /api/trips` 与 `GET /api/trips/{id}`：创建、读取持久行程。
- `POST /api/trips/{id}/sources`：添加来源；处理返回任务 ID。
- `PATCH /api/trips/{id}/candidates/{id}`：设置必去、可选或忽略。
- `POST /api/trips/{id}/chat`：引用服务端当前版本，携带 `base_version`，避免并发覆盖。
- `GET /api/trips/{id}/versions` 与 `POST /api/trips/{id}/restore`：读取历史；恢复操作创建新版本。
- `GET /api/runs/{id}/events`：SSE 传递理解需求、核实地点、排路线、校验、完成或失败等事件。只输出工作状态，不输出内部推理。

保留现有 `/api/trip/plan`、`/chat` 作为过渡入口；不要一次替换所有调用方。

持久化先使用本地 SQLite 保存行程、来源和版本，再根据后端部署环境确定长期数据库。LangGraph checkpointer 可保存执行状态；行程版本单独管理。若只用浏览器存储，只能承诺同一设备恢复，不能宣传跨设备同步。云端多人使用前需有用户归属和访问控制。

## 迭代顺序与验收

### A：先做攻略可用与修改可恢复

- 来源独立保存，提取地点并以 POI ID 匹配，支持必去／可选／忽略。
- 对话沿用来源及用户已确认约束。
- 保存多个行程、版本摘要、一键撤销；统一覆盖 Agent、手动和 DIY 修改。
- 接入处理阶段反馈；刷新后可恢复已保存结果，未完成任务显示真实状态。

验收：两篇含重复地点的攻略能去重并保留双来源；同名分店不会自动串店；必去项未排入时有说明；一次调整只改变目标日；撤销能精确恢复；关闭浏览器后能重新打开保存的行程。

### B：补路线、时间和费用可信度

- 统一时间轴、服务端交通查询、区域分日、固定预约和每日时间上限。
- 建立冲突检查与地图故障降级。
- 未知费用改用 null，免费与未知分别展示；总额标明已知部分和缺项。

验收：用固定地点与交通查询样例检测跨区折返、一天超时、固定预约冲突；查询失败不会显示为已核实；锁定项不会被优化删除；未知费用不会显示成免费或完整总预算。

### C：再完善长期使用

- 我的旅行、可查看并修改的偏好记忆、分享只读链接、同行协作。
- 取消与断线恢复、POI/路线缓存、前端体积优化、运行指标。
- 若上线，核实当前托管配置并单独部署前后端，执行线上端到端验收。

## 实现落点

- `backend/app/models/schemas.py`：来源、候选、约束、时间轴、费用、版本模型。
- `backend/app/agents/travel_agent.py`：携带持久上下文；严格校验锁定约束、必去覆盖和局部修改边界。
- `backend/app/services/amap_rest.py`：保留 POI ID，增加交通查询与缓存接口。
- 建议新增 `reference_service.py`、`schedule_validator.py`、`trip_repository.py`，将来源处理、可行性校验、数据保存与模型决策分开。
- `frontend/src/views/Home.vue`：附件和候选卡片；`Result.vue`：版本、撤销、时间轴和对话抽屉。
- 建议拆出 `ReferencePanel`、`AgentPanel`、`DayTimeline`，逐步缩减大型页面组件。

本轮优先 A。保留 Vue + FastAPI + LangGraph，单 Agent 调用可靠工具即可。是否引入更多 Agent 应由具体质量和延迟瓶颈决定。

测试采用离线工具样例和模型替身，重点验证来源保留、约束、版本、时间冲突及故障恢复；最终再做真实接口联调。上一对话未完成的“带攻略完整生成”联调应单独跟踪，不能把历史构建通过视为当前链路已验收。

## 2026-10-08 第二阶段实现记录

已实现每日时间轴、高德服务端交通查询和短期缓存、固定预约、日程窗口／缓冲编辑、预约晚到及全天超时校验、故障标记、旧行程版本化重校验、未知费用与免费区分。Agent复用校验并有限修复；不能修复时保留有标记的草稿。手动／DIY保存同样校验，地图交通摘要沿用相同结果。

没有把地图直线连接当作导航路线；没有把未查询到的交通当作零耗时。没有上线开放时间数据库、未来公交班次预测或自动区域分日。这些范围仍需后续迭代；真实高德路线和模型端到端联调仍单独跟踪。
