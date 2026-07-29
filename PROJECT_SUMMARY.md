# 智能旅行规划助手 — 项目总结文档

> 截止 2026-06，项目代码约 5600 行（后端 2000+ 行 / 前端 3600+ 行），覆盖 15+ 功能点。

---

## 目录

- [1. 项目定位](#1-项目定位)
- [2. 技术架构一览](#2-技术架构一览)
- [3. 核心功能清单与实现](#3-核心功能清单与实现)
- [4. 后端 API 全景](#4-后端-api-全景)
- [5. 前端页面与组件](#5-前端页面与组件)
- [6. 文件结构总览](#6-文件结构总览)
- [7. 关键技术决策记录](#7-关键技术决策记录)

---

## 1. 项目定位

**智能旅行规划助手** 是一个基于 AI Agent 的全栈 Web 应用。用户输入目的地、日期、偏好，系统自动搜索实时数据（景点、天气、酒店），由 LLM 综合生成包含每日行程、路线优化、餐饮推荐、预算明细的完整多日旅行计划。

### 1.1 一句话总结各组件的职责

| 组件 | 职责 |
|------|------|
| **LangGraph Agent** | 编排 4 个 AI 节点：搜索景点 → 查天气 → 找酒店 → 生成计划 |
| **MCP 高德工具** | 16 个实时地理工具：POI 搜索、天气、驾车/步行/公交路线 |
| **FastAPI 后端** | 对外暴露 REST API，对内调度 Agent 引擎 |
| **Vue 3 前端** | 表单收集 → 提交 → 结果可视化（地图 + 卡片 + 路线详情） |

---

## 2. 技术架构一览

```
浏览器 (Vue 3)
    │  POST /api/trip/plan
    ▼
FastAPI (trip.py)
    │  PLANNER_ENGINE="langgraph"
    ▼
LangGraph StateGraph
    ├─ [ReAct] search_attractions ─── MCP ─── 高德 POI 搜索
    ├─ [ReAct] search_weather      ─── MCP ─── 高德 天气查询
    ├─ [ReAct] search_hotels       ─── MCP ─── 高德 酒店搜索
    └─ generate_plan                ─── LLM ─── DeepSeek-chat
    │
    ▼
_parse_plan_json (四级兜底解析)
    │
    ▼
TripPlan (Pydantic v2) → JSON Response
    │
    ▼
前端 sessionStorage → Result.vue 渲染
```

### 2.1 环境与依赖

| 层 | 语言/运行时 | 核心依赖 |
|----|-----------|---------|
| Agent 编排 | Python 3.12 | `langgraph`, `langchain`, `langchain-openai`, `langchain-mcp-adapters` |
| Web 服务 | Python 3.12 | `fastapi`, `uvicorn`, `pydantic` v2 |
| 图片搜索 | Python | `requests` (Pexels API) |
| URL 提取 | Python | `beautifulsoup4`, `requests` |
| OCR | Python | `pytesseract`, `Pillow` (需系统安装 Tesseract) |
| 前端 | TypeScript / Node 16+ | `vue` 3, `vue-router` 4, `ant-design-vue` 4, `axios`, `html2canvas`, `jspdf` |
| 地图 | JS | 高德 JS API 2.0 (`@amap/amap-jsapi-loader`) |
| LLM | API | DeepSeek-chat (via OpenAI-compatible SDK) |

---

## 3. 核心功能清单与实现

### 3.1 智能行程生成

**流程**：
```
用户输入 (城市/日期/偏好) → LangGraph 4 阶段流水线 → 完整 TripPlan JSON
```

**实现文件**：`backend/app/agents/langgraph_planner.py`（903 行）

**关键代码**：
- `plan_trip_async()` (L398)：入口方法，构建 StateGraph → `ainvoke()`
- `_attraction_node()` (L215)：ReAct 子图搜索景点
- `_weather_node()` (L233)：ReAct 子图查询天气
- `_hotel_node()` (L244)：ReAct 子图搜索酒店
- `_planner_node()` (L257)：纯 LLM 调用，综合所有信息生成 JSON

**效果**：返回包含 city、days[]（每个 day 含 attractions[]、meals[]、hotel）、weather_info[]、budget 的完整计划。

### 3.2 ReAct 循环搜索

**核心设计**：每个搜索节点不是一次性调用 LLM，而是封装为内部 ReAct 循环：

```
LLM 推理 → 调用工具 → 观察结果 → 不满意？换关键词再搜 → 满意 → 结束
```

**实现文件**：`langgraph_planner.py` 中的 `_make_search_subgraph()` (L162)

**关键代码**：
```python
workflow.add_node("agent", agent_node)
workflow.add_node("tools", ToolNode(self.tools))
workflow.add_conditional_edges("agent", tools_condition)  # 有 tool_calls → tools → agent
workflow.add_edge("tools", "agent")  # 工具结果返回 agent 继续推理
```

**实际效果**：Agent 搜"天津 自然风光"只有 3 个结果 → 自动换关键词"天津 公园"再搜 → 收集够 5 个才结束。

### 3.3 LLM 输出解析器（四级兜底）

**问题**：DeepSeek-chat 每次返回的 JSON 结构都不同——字段名、嵌套方式、坐标格式都不稳定。

**解决方案**：`langgraph_planner.py` 中的 `_parse_plan_json()` (L459) + 辅助函数

| 层级 | 策略 | 示例 |
|------|------|------|
| L1 | 标准 JSON 解析 | `json.loads()` |
| L2 | 40+ 字段别名 | `days`/`daily_plan`/`itinerary`, `attractions`/`activities`/`spots` |
| L3 | 智能类型发现 | 遍历 day 对象，自动匹配含 `{name:...}` 结构的值 |
| L4 | request 补全 | 缺失的 city/start_date 从原始请求填充 |

**关键辅助函数**：
- `_find_item_list()`：自动发现景点数组（支持 `activities`/`itinerary`/`morning`/`afternoon` 等 20+ 种 key）
- `_find_meal_list()`：自动发现餐饮数组（含 itinerary 中关键词检测）
- `_find_hotel_in_day()`：自动发现酒店（含 accommodation 为 dict 时的处理）
- `_normalize_location()`：坐标格式统一（`[lng,lat]` / `{longitude,latitude}` / `{lng,lat}` → 统一）
- `_parse_duration()`：时长解析（`120` / `"09:00-11:30"` → 分钟数）

### 3.4 每日路线可视化

**概览地图**：顶部显示全行程景点路径

**实现**：`Result.vue` 中的 `addAttractionMarkers()` + `drawRoutes()`
- 绿色标签标记景点（D1-1, D1-2...）
- 蓝色路线串联

**每日迷你地图**：每个 day 面板展开后显示完整动线

**实现**：`Result.vue` 中的 `initDayMiniMap()` (L1312)
- 橙色标记餐厅（☀️ 早餐 / 🌤️ 午餐 / 🌙 晚餐）
- 蓝色标记酒店（🏨）
- 路线详情表显示每段交通信息

**路线详情**：使用高德 `AMap.Transfer` 获取真实公交/地铁线路

**实现**：`Result.vue` 中的 `computeRealTransit()` (L1436)
- 🚌 公交段：显示具体线路名（如"地铁1号线 → 公交52路"）
- 🚗 驾车段：显示道路名
- 🚶 步行段（<1km）：直接步行
- API 失败兜底：Haversine 公式估算

### 3.5 参考来源融合（三种模式）

| 模式 | LLM 行为 | 搜索策略 |
|------|---------|---------|
| 💡 AI 自主 | 攻略仅作风格参考 | 通用搜索 |
| 🔗 小红书为主 + AI 补充 | 攻略地点为骨架，AI 补充 | 通用搜索 + **逐项搜攻略地名** |
| 📋 严格按攻略 | 完全照搬攻略 | 通用搜索 + **逐项搜攻略地名** |

**实现文件**：
- `backend/app/agents/langgraph_planner.py` → `plan_trip_async()` 中的模式判断
- `backend/app/agents/langgraph_planner.py` → `_build_reference_search_query()`：正则提取【地名】逐一搜索
- `frontend/src/views/Home.vue` → 参考来源 UI（URL 输入 + 截图上传 + 模式选择）

### 3.6 小红书 URL 内容提取

**流程**：用户粘贴链接 → `POST /api/trip/extract-url` → 后端抓取 → 返回纯文本

**实现文件**：`backend/app/api/routes/trip.py` (L107-220)

**技术细节**：
- 多 UA 轮换绕过基础反爬
- BeautifulSoup 提取正文（专用 CSS 选择器 + 通用模式）
- SPA 检测：提取字符 < 200 → 判定为 JS 动态渲染 → 提示手动粘贴
- 反爬检测：识别"访问验证""请完成安全验证"等拦截页面

**前端**：`Home.vue` → URL 列表（支持多个链接逐一提取）

### 3.7 截图 OCR 识别

**流程**：上传截图 → Canvas 框选区域 → `POST /api/trip/extract-image` → OCR

**实现文件**：
- `backend/app/api/routes/trip.py` (L222-284)：pytesseract 中文识别
- `frontend/src/views/Home.vue`：Canvas 裁剪工具（mousedown/mousemove/mouseup 绘制选择框）

**降级**：Tesseract 未安装 → 提示手动粘贴

### 3.8 DIY 调整

**功能入口**：结果页点击 **🔧 DIY调整**

| 操作 | 实现 |
|------|------|
| 替换景点/餐厅 | `POST /api/trip/alternatives` → LangGraph Agent 搜索 3-5 个备选 |
| 添加景点/餐厅 | `POST /api/trip/search-poi` → 搜索或手动输入 |
| 删除项 | 前端直接 splice |
| 移动排序 | 前端数组 swap |
| 智能优化路线 | `POST /api/trip/reoptimize` → 最近邻贪心重排 |

**实现文件**：
- `backend/app/api/routes/trip.py`：alternatives / search-poi / reoptimize 三个端点
- `frontend/src/views/Result.vue`：DIY 模式 UI + 模态框 + 交互逻辑

### 3.9 路线优化算法

**最近邻贪心**：从第一个点出发，每次选择最近的未访问点

**实现**：`langgraph_planner.py` → `reoptimize_async()` + 前端 `handleReoptimize()`

**距离计算**：Haversine 球面大圆距离公式

### 3.10 行程导出

| 格式 | 实现 |
|------|------|
| 导出为图片 | `html2canvas` 截图 → PNG 下载 |
| 导出为 PDF | `html2canvas` + `jsPDF` → PDF 分页下载 |

**实现**：`Result.vue` → `exportAsImage()` / `exportAsPDF()`

### 3.11 图片搜索

**三级降级**：

| 策略 | 说明 |
|------|------|
| Pexels API | 需配置 `PEXELS_API_KEY`，免费 200req/hr |
| 短查询 | 原始查询失败 → 截取前 8 字重试 |
| 通用关键词 | 景点 → "travel landmark"，餐厅 → "food restaurant" |

**兜底**：全部失败 → 渐变 SVG 占位图（带图标 + 名称 + 纹理背景）

**实现文件**：
- `backend/app/services/image_service.py`：Pexels API 封装
- `frontend/src/views/Result.vue`：`getAttractionImage()` / `getMealImage()` SVG 生成

### 3.12 前端特色功能

| 功能 | 实现位置 |
|------|---------|
| 日期联动计算天数 | `Home.vue` watch 监听 start_date/end_date |
| 偏好标签多选 | `a-checkbox-group`（历史文化/自然风光/美食/购物/艺术/休闲） |
| 进度条状态提示 | Loading 进度 + 文字状态轮换 |
| 行程卡片折叠 | `a-collapse` 多面板同时展开 |
| 侧边导航定位 | `a-affix` + `scrollIntoView` 锚点跳转 |
| 编辑模式 | 景点增删改 + 顺序调整 + sessionStorage 持久化 |

---

## 4. 后端 API 全景

### 4.1 全部端点

| 端点 | 方法 | 引擎 | 功能 |
|------|------|------|------|
| `/api/trip/plan` | POST | LangGraph | 生成旅行计划（主端点） |
| `/api/trip/alternatives` | POST | LangGraph | DIY 替换备选搜索 |
| `/api/trip/search-poi` | POST | LangGraph | DIY 手动添加搜索 |
| `/api/trip/reoptimize` | POST | LangGraph | 路线智能重排 |
| `/api/trip/extract-url` | POST | — | 小红书 URL 内容提取 |
| `/api/trip/extract-image` | POST | — | 截图 OCR 文字识别 |
| `/api/trip/health` | GET | — | 健康检查 |
| `/api/poi/photo` | GET | — | 景点/餐厅图片搜索 |
| `/api/poi/search` | GET | — | 通用 POI 搜索 |
| `/api/poi/detail/{id}` | GET | — | POI 详情 |
| `/api/map/poi` | GET | — | 地图 POI 搜索 |
| `/api/map/weather` | GET | — | 天气查询 |
| `/api/map/route` | POST | — | 路线规划 |
| `/health` | GET | — | 全局健康检查 |

### 4.2 双引擎切换

通过 `backend/.env` 中的 `PLANNER_ENGINE` 控制：

```python
# langgraph (默认) — 使用 LangGraph ReAct 引擎
PLANNER_ENGINE=langgraph

# helloagents (回退) — 使用 SimpleAgent 顺序流水线
PLANNER_ENGINE=helloagents
```

### 4.3 数据模型

核心模型定义在 `backend/app/models/schemas.py`：

```
TripRequest → TripPlanResponse { success, message, data: TripPlan }
TripPlan { city, start_date, end_date, days[], weather_info[], overall_suggestions, budget? }
DayPlan { day_index, attractions[], meals[], hotel?, route_notes? }
Attraction { name, address, location{ longitude, latitude }, visit_duration, description, ticket_price }
Meal { type, name, restaurant, address, location, recommended_dish, estimated_cost }
Hotel { name, address, location, price_range, rating, distance, type, estimated_cost }
Budget { total_attractions, total_hotels, total_meals, total_transportation, total }
```

---

## 5. 前端页面与组件

### 5.1 路由

| 路径 | 组件 | 功能 |
|------|------|------|
| `/` | `Home.vue` (1278 行) | 表单输入 + 参考来源 + 提交 |
| `/result` | `Result.vue` (2307 行) | 行程展示 + 地图 + DIY + 导出 |

### 5.2 数据流

```
Home.vue (表单提交)
    → api.ts generateTripPlan()
    → POST /api/trip/plan
    → sessionStorage.setItem('tripPlan', JSON.stringify(response.data))
    → router.push('/result')

Result.vue (onMounted)
    → sessionStorage.getItem('tripPlan')
    → JSON.parse → tripPlan ref
    → 加载图片 + 初始化地图 + 渲染所有卡片
```

### 5.3 组件树

```
App.vue
├── Home.vue (首页)
│   ├── 目的地与日期 (a-input / a-date-picker)
│   ├── 偏好设置 (a-select / a-checkbox-group)
│   ├── 额外要求 (a-textarea)
│   ├── 参考来源 (a-collapse)
│   │   ├── 小红书链接 (多 URL 输入 + 提取按钮)
│   │   ├── 截图上传 (a-upload + Canvas 裁剪)
│   │   └── 规划策略 (a-radio-group: 三种模式)
│   └── 提交按钮 + 进度条
│
└── Result.vue (结果页)
    ├── 页面头部 (返回/编辑/DIY/导出按钮)
    ├── 侧边导航 (a-affix + a-menu)
    ├── 主内容区
    │   ├── 行程概览卡片
    │   ├── 预算明细卡片
    │   ├── 景点规划地图 (高德 JS API)
    │   ├── 每日行程 (a-collapse)
    │   │   ├── 基本信息 (描述/路线优化/交通/住宿)
    │   │   ├── 景点安排 (a-card 列表)
    │   │   ├── 酒店推荐
    │   │   ├── 餐饮安排 (a-card 列表 + 美食图片)
    │   │   ├── 每日迷你地图 + 路线详情
    │   │   └── DIY 按钮 (替换/添加/优化)
    │   └── 天气信息卡片
    ├── 备选项模态框 (a-modal)
    ├── 添加搜索模态框 (a-modal)
    └── 回到顶部 (a-back-top)
```

---

## 6. 文件结构总览

```
Trip-planner/
├── backend/
│   ├── app/
│   │   ├── agents/
│   │   │   ├── langgraph_planner.py  ← LangGraph 引擎 (903行, 核心)
│   │   │   └── trip_planner_agent.py ← HelloAgents 引擎 (666行, 回退)
│   │   ├── api/
│   │   │   ├── main.py               ← FastAPI 应用入口
│   │   │   └── routes/
│   │   │       ├── trip.py           ← 主路由 (454行, 12个端点)
│   │   │       ├── poi.py            ← 图片/POI 路由
│   │   │       └── map.py            ← 地图服务路由
│   │   ├── services/
│   │   │   ├── image_service.py      ← Pexels 图片搜索
│   │   │   ├── amap_service.py       ← 高德封装 (备用)
│   │   │   └── llm_service.py        ← LLM 初始化
│   │   ├── models/
│   │   │   └── schemas.py            ← Pydantic 数据模型
│   │   └── config.py                 ← 配置管理
│   ├── .env                          ← 环境变量 (API Key 等)
│   └── requirements.txt
│
├── frontend/
│   ├── src/
│   │   ├── views/
│   │   │   ├── Home.vue              ← 首页 (1278行)
│   │   │   └── Result.vue            ← 结果页 (2307行)
│   │   ├── services/
│   │   │   └── api.ts                ← Axios 封装
│   │   ├── types/
│   │   │   └── index.ts              ← TypeScript 类型
│   │   ├── App.vue                   ← 根组件
│   │   └── main.ts                   ← Vue 入口 + Router
│   ├── .env                          ← 高德 JS Key 配置
│   └── package.json
│
├── PROJECT_DEEP_DIVE.md              ← 面试深度解析文档
├── PROJECT_SUMMARY.md                ← 本文档
└── README.md
```

---

## 7. 关键技术决策记录

| 决策 | 选择 | 原因 |
|------|------|------|
| Agent 框架 | LangGraph (替换 HelloAgents) | 声明式图编排 + 原生 ReAct + 可观测 |
| LLM | DeepSeek-chat | 中文强、性价比高、兼容 OpenAI SDK |
| 工具协议 | MCP (Model Context Protocol) | 标准化接入，amap-mcp-server 开箱即用 |
| 前后端通信 | 纯 REST | 实时性要求低，无需 WebSocket |
| 状态传递 | sessionStorage | 跨页面数据传递，够用不复杂 |
| 图片方案 | Pexels + SVG 兜底 | API 免费 + 无外部依赖兜底 |
| JSON 解析 | 四级兜底 | LLM 输出极不稳定，必须防御性编程 |
| 路线优化 | 最近邻贪心 | n≤10 时足够优，O(n²) 性能好 |
| 公交查询 | 前端 AMap.Transfer | 获取真实线路名，Haversine 兜底 |
| 双引擎 | 保留 HelloAgents | 环境变量切换，LangGraph 故障可回退 |

---

> 最后更新: 2026-06
