# 性能与体验调研：开源旅行 Agent 与提速方案

调研日期：2026-10-09 至 10-10。本文补充 [KERNEL_RESEARCH_2026-10-09.md](KERNEL_RESEARCH_2026-10-09.md) 和 [NEXT_ITERATION_DESIGN.md](NEXT_ITERATION_DESIGN.md)。已调研过的 LangGraph、Deep Agents、GPT Researcher、Pydantic AI、OR-Tools、ITINERA、TravelPlanner、AdventureLog、Vercel Chatbot 不再重复。

星数取自调研当天的 GitHub 页面或 `gh api`，之后会变化。

调研时 WebSearch 不可用，候选项目改从 GitHub 搜索接口和直接抓取的页面获得，n8n 和小红书类规划器的覆盖不完整。

标记说明：**[V]** 表示读过 README 或代码树；**[I]** 表示只看了仓库描述，或属于推断。

本文第一部分是外部项目调研，第二部分是本项目的代码瓶颈分析，第三部分是采纳情况。本轮实际落地的内容和实测数据见 [ARCHITECTURE.md](ARCHITECTURE.md) 与 README。

## 一、外部项目

### 旅行规划类

| 项目 | 星数 | 做什么 | 可借鉴 | 核实 |
| --- | --- | --- | --- | --- |
| [liketrek/TREK](https://github.com/liketrek/TREK) | 14.7k | 自托管、实时协作的旅行规划器 | 见下方列表 | [V] |
| [datawhalechina/hello-agents](https://github.com/datawhalechina/hello-agents) 第 13 章及 Extra12 | 82.2k | 中文智能体教程；第 13 章是本项目的起点（景点/天气/酒店子 Agent + 高德 MCP） | Extra12 的“冻结评测集 + bad case”：预算合计错、餐饮重复、酒店晚数错，这些可以直接写成校验规则 | [V] |
| [itskovacs/trip](https://github.com/itskovacs/trip) | 1.9k | 极简的地点收藏 + 行程规划 | 先把地点收进地图收藏池、按类别筛选，再排进每天 | [V] |
| [JourniOne-Planning-Skills](https://github.com/JourniOne-ai/JourniOne-Planning-Skills) | 619 | 从灵感到逐日路线，再到可分享的旅行手册 | 先定路线、后定日期和订票；手册视图与地图视图切换；酒店比价覆盖携程、飞猪、美团 | [V] |
| [travel-plan-viz](https://github.com/zexuanw958-svg/travel-plan-viz) | 401 | 把行程导出为单文件、可离线的手机网页 | 编号站点 + 虚线路线；高德/Apple 地图导航深链；GCJ-02 转 WGS-84；从出发日倒推订票提醒；生成后机械校验 | [V] |
| [jianhao-travel-planner](https://github.com/awangwang123/jianhao-travel-planner) | 354 | 多源交叉验证的路书工作流 | 11 项机械验收清单；出发前按当日预报做最终判断；区分挂牌价和实付价 | [V] |
| [marco-polo.skill](https://github.com/megumi-ben/marco-polo.skill) | 199 | 结合高德和小红书生成中文旅行手册 | 以住处为中心聚类，减少回头路；夜景放天黑后、美食街放饭点；预约清单写明“订什么、何时订” | [V] |
| travel-concierge（Google ADK 示例，现位于 [gcp-getting-started-lab-jp](https://github.com/GoogleCloudPlatform/gcp-getting-started-lab-jp)） | — | 根 Agent + 6 个子 Agent，覆盖灵感、规划、预订、行前、行中、行后 | 按旅行生命周期拆分；自带评测集 | [V] 结构，[I] 细节 |
| [TripStar-Java](https://github.com/LeeFly-cn/TripStar-Java) | 61 | 小红书笔记多模态理解 + 高德 | 粘贴链接或截图，多模态模型抽取景点后用高德补全；用 WebSocket 推送阶段进度 | [V] |
| [travel-guidebook](https://github.com/geekjourneyx/travel-guidebook) | 92 | 交通、美食、景点、文化 4 个 Agent 并行，合并成 PDF 手册 | 并行检索；高德不可用时降级，并明确标注 | [V] |
| [ChinaTravel](https://github.com/LAMDA-NeSy/ChinaTravel) | 133 | 中文旅行规划基准 | 可执行的硬约束 DSL；“生成 → 符号校验 → 修正”循环；2000 条查询可直接用作回归评测集 | [V] |
| [nirbar1985/ai-travel-agent](https://github.com/nirbar1985/ai-travel-agent) | 804 | LangGraph 航班 + 酒店助手 | 按任务选择不同模型；关键动作前人工确认 | [V] |
| [azure-ai-travel-agents](https://github.com/Azure-Samples/azure-ai-travel-agents) | 483 | 多 Agent + MCP 参考架构 | 单独的需求理解 Agent | [V] |
| [wanderlog-mcp](https://github.com/shaikhspeare/wanderlog-mcp) | 147 | 用对话编辑 Wanderlog 行程（33 个工具） | 工具粒度的参考：增删移地点、交通段、费用、清单、未排期列表 | [V] |

TREK 可借鉴的点：
- 跨天拖拽和撤销；单日自动排序时锁定站点和酒店锚点不动。
- 16 天天气预报，超出预报窗口时显示历史同期气候。
- 预算分账、打包清单模板。
- PWA 离线使用，PDF/GPX/ICS 导出，只读公开分享。

已失效或价值低的候选：
- crewAI-examples 里的 trip_planner 已删除。
- adk-samples 已改名为 adk-recipes，里面不再有 travel-concierge。
- Microsoft ai-agents-for-beginners 只把旅行当教学例子。
- OpenAI Agents SDK 没有旅行示例。
- awesome-travel-agent 类清单和名为 Voyage 的仓库基本都是 0 星。

### 提速与交互类

| 项目 / 文档 | 星数 | 结论 | 核实 |
| --- | --- | --- | --- |
| [LangGraph streaming](https://docs.langchain.com/oss/python/langgraph/streaming) | 官方文档 | 有 updates/messages/custom 等 7 种模式；节点里可以用 `get_stream_writer()` 推送自定义进度 | [V] |
| [AG-UI](https://github.com/ag-ui-protocol/ag-ui) | 16.4k | Agent 与前端之间的事件协议，有官方 LangGraph 适配；文档没有说明是否支持 Vue | [V] |
| [Vercel AI SDK](https://github.com/vercel/ai) | 27.2k | `@ai-sdk/vue` 提供 useChat 和 useObject；后端可以用 FastAPI 按它的协议输出 SSE | [V] |
| [CopilotKit](https://github.com/CopilotKit/CopilotKit) | 37.9k | Vue 支持标注为 coming soon | [V] |
| [assistant-ui](https://github.com/assistant-ui/assistant-ui) | 12.5k | 只支持 React | [V] |
| [agent-chat-ui](https://github.com/langchain-ai/agent-chat-ui) | 3.2k | Next.js 实现；内部调用可以关闭 token 流 | [V] |
| DeepSeek 上下文缓存 | 官方文档 | 默认开启；前缀**完全一致**才命中；usage 返回 `prompt_cache_hit_tokens`。命中约便宜 50 倍，这个价格比来自摘要转述，需要人工核对 | [V] / 价格待核对 |
| DeepSeek JSON mode | 官方文档 | `response_format=json_object` 偶尔返回空内容，需要准备重试 | [V] |
| [LiteLLM](https://github.com/BerriAI/litellm) | 60.4k | 跨供应商路由、fallback 和缓存 | [V] |
| [Langfuse](https://github.com/langfuse/langfuse) | 35.6k | MIT 许可，可自托管；提供 LangChain 回调 | [V] |
| [Opik](https://github.com/comet-ml/opik) / [Phoenix](https://github.com/Arize-ai/phoenix) | 22.5k / 11.8k | Opik 是 Apache-2.0；Phoenix 是 ELv2、基于 OTel | [V] |
| [GPTCache](https://github.com/zilliztech/GPTCache) | 8.2k | 维护者已声明不再适配新模型 | [V] |
| [partial-json-parser](https://github.com/promplate/partial-json-parser-js) | 246 / 140 | 可以解析半截 JSON，用于边生成边渲染 | [V] |

## 二、本项目瓶颈（2026-10-09 代码）

以下耗时都是根据代码推算的，当时没有实测；本轮实测见第三部分。

1. **最终调用写出完整行程 JSON**：地址、坐标、餐厅都由模型逐字写出，随后又被服务端用高德数据覆盖或重新挑选。3 天行程估计要 4–6k 输出 token，1–3 分钟；每次修复还会整份重写。这是最大的耗时来源。
2. **没有任何流式或进度推送**：`/trip/plan` 是一个同步长请求，最长等 300 秒。首页只显示一句固定文案加秒数，刷新页面结果就丢失。
3. **高德路线查询**：
   - 全进程共用一把锁，在锁里 sleep，1 QPS 串行；`Semaphore(3)` 因此没有实际作用。
   - 每次请求都新建 HTTP 连接。
   - 缓存只保留 15 分钟。
   - 3 天行程至少约 21 秒，最坏情况 1–2.5 分钟。
4. **重复和多余的轮次**：
   - temperature 设为 0.7。
   - 最终轮切换成不带工具的模型，前缀缓存失效。
   - 提示词没有要求并行搜索，模型往往一轮只调一个工具。
   - `LLM_TIMEOUT` 在代码里没有被读取，模型请求没有超时。
5. **事件循环里的同步 SQLite**：路线缓存每次查询都执行一次建表；进度更新、仓储读写都直接在 async 路由里执行。
6. **前端**：
   - 生成后有一个人为的 500ms 等待，结果页加载完版本记录才初始化地图。
   - 地图每次刷新都销毁重建，并弹出“地图加载成功”提示。
   - 入口包 1.06 MB。
   - 配图用中文名搜 Pexels，基本搜不到，最慢一张约 60 秒，而且会退回到无关的通用风景图。

## 三、采纳情况

### 本轮已实现

- **精简输出**：模型只写 `poi_id`、停留时长和理由，服务端按 `poi_id` 回填名称、地址和坐标；新建行程不再让模型挑餐厅。
- **局部修复**：新建行程出现时间冲突时，修复改用 patch，只修改冲突日期，不再整份重写。
- **新建行程走后台任务**：用 SSE 推送阶段进度（搜索关键词、写到第几天、已核实 x/y 段），支持取消，刷新后能接回进度。对话和优化任务也从轮询改为 SSE。
- **模型调用设置**：
  - 同一轮并行发出多个搜索。
  - 最终轮保留同一套工具（`tool_choice="none"`），保持前缀一致。
  - temperature 降到 0.3，设置超时和重试上限。
  - 新增可选的快速模型，供轻任务使用。
- **高德查询**：
  - 限速改为预约时段，不再持锁等待。
  - 共享 HTTP 连接。
  - 地点搜索缓存 6 小时，路线缓存时长可配置，默认 6 小时。
- **SQLite**：开启 WAL，建表只做一次，进度和路线缓存的读写移出事件循环。
- **观测**：每次模型调用记录耗时、输入、缓存命中和输出；可选接入 Langfuse。
- **前端**：
  - 去掉 500ms 等待；版本记录和地图并行加载；地图只更新标记。
  - vendor 分包。
  - 配图优先用高德 POI 照片，不再退回通用风景图。

### 第二批已实现（2026-10-10）

- **拖拽编辑 + 局部重排**（参考 TREK）：拖拽排序、拖到日期标签跨日移动、锁定、草稿逐步撤销；保存后可以自动用 OR-Tools 优化变动的日期（锁定项不动），合并成一个版本。
- **天气感知替换**（参考 TREK、jianhao）：按高德分类判断室内户外；预报有雨雪的日期，为户外景点给出附近室内候选，由用户选择后进入草稿。尚未实现历史同期气候，超出预报范围时只提示临近出发再检查。

### 第三批已实现（2026-10-10）

- **规则校验扩充 + 评测脚本**（参考 hello-agents Extra12 和 ChinaTravel）：新增 `plan_checks`，以及离线/在线两种评测模式。用例是自写的 12 个中文查询，没有直接导入 ChinaTravel 数据集。
- **预约提醒、住宿汇总与打包清单**（参考 marco-polo、travel-plan-viz、TREK）：新增结果页“出行准备”面板。预算目前只在规则校验中与用户写明的数字对比，没有单独做预算面板。

### 下一阶段（按投入产出排序）

1. **确定性校验扩充 + 评测集**（hello-agents Extra12、ChinaTravel）：加入预算复算、餐饮去重、酒店晚数、坐标离群检查；用 ChinaTravel 的查询做离线回归。
4. **预约提醒、预算和打包清单**（marco-polo、travel-plan-viz、TREK）。
5. **离线出行手册**（travel-plan-viz、TREK）：单文件 HTML，带编号路线和导航深链；ICS 日历。
6. **小红书链接或截图导入**（TripStar）、**只读分享链接**（TREK）。
7. **路线校验进一步提速**：确认账户配额后调高 `AMAP_ROUTE_QPS`；或先显示行程，再在后台补齐真实路线并推送更新。

### 不采纳

- **迁移到 React 系 UI**（CopilotKit、assistant-ui）：自定义 SSE 已经够用，事件结构以后可以向 AG-UI 靠拢。
- **语义缓存（GPTCache 等）**：不同日期、不同偏好的旅行需求彼此很像，容易命中错误的结果；GPTCache 也已停止维护。只做精确匹配缓存。
- **JSON mode**：DeepSeek 文档提示它偶尔会返回空内容，而且最终轮为了保持缓存前缀仍绑定着工具；现在已改为精简格式、低 temperature 并保留现有解析，暂不启用。
