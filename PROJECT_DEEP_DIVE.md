# 智能旅行规划助手 — 项目深度解析

> 面试准备文档，帮助你吃透项目每一个设计决策和技术细节。

---

## 目录

1. [项目定位与设计出发点](#1-项目定位与设计出发点)
2. [核心功能全景](#2-核心功能全景)
3. [技术架构深潜](#3-技术架构深潜)
4. [从 HelloAgents 迁移到 LangGraph](#4-从-helloagents-迁移到-langgraph)
5. [ReAct 模式详解](#5-react-模式详解)
6. [MCP 协议集成](#6-mcp-协议集成)
7. [LLM 输出解析的鲁棒性设计](#7-llm-输出解析的鲁棒性设计)
8. [路线优化算法](#8-路线优化算法)
9. [参考来源融合引擎](#9-参考来源融合引擎)
10. [前后端数据流](#10-前后端数据流)
11. [面试高频问题](#11-面试高频问题)
12. [相关知识点速查](#12-相关知识点速查)

---

## 1. 项目定位与设计出发点

### 一句话描述

基于 LangGraph StateGraph + ReAct 模式的多智能体旅行规划系统，通过 MCP 协议实时接入高德地图数据，自动生成包含路线优化和餐饮推荐的完整多日行程。

### 设计出发点

| 痛点 | 解决方案 |
|------|---------|
| 传统旅行规划需要手动查攻略、拼路线 | LLM + 高德 API 自动整合景点/天气/酒店 |
| 单次 LLM 调用信息不足、幻觉多 | ReAct 循环让 Agent 多轮搜索直到满意 |
| 小红书攻略内容无法直接利用 | URL 提取 + OCR + 三种融合模式 |
| LLM 输出格式极不稳定 | 四级兜底解析器，自动适配 30+ 种字段变体 |
| 手动编排 Agent 逻辑繁琐 | LangGraph StateGraph 声明式图编排 |

### 与传统 RAG 的区别

```
RAG:  用户问题 → 向量检索文档 → 拼接文档到prompt → LLM回答
本项目: 用户需求 → Agent推理 → 调用MCP工具(高德API) → 观察结果 → 继续推理 → 生成计划
```

本项目是 **Tool-use Agent** 模式，不是 RAG。RAG 检索静态文档，Tool-use 调用实时 API。

---

## 2. 核心功能全景

### 2.1 智能行程生成

```
用户输入: 天津, 2026-05-30 ~ 06-02, 4天, 偏好: 自然风光+艺术+美食
         ↓
搜索Agent(ReAct): 调用高德POI搜索 → 找到天塔湖、水上公园、海河湿地...
天气Agent(ReAct): 调用高德天气 → 5/30晴35°C, 5/31阴35°C...
酒店Agent(ReAct): 调用高德搜索 → 经济型酒店列表
         ↓
规划Agent: 综合所有信息 → 生成4天详细行程JSON
         ↓
前端展示: 概览地图 + 每日迷你地图 + 路线详情 + 餐饮推荐
```

### 2.2 参考来源融合（三种模式）

| 模式 | 行为 | 适用场景 |
|------|------|---------|
| 💡 AI自主规划 | 攻略仅作风格参考，AI独立搜索规划 | 没有攻略，完全依赖AI |
| 🔗 小红书为主+AI补充 | 攻略地点为骨架，AI填补交通/天气/时间 | 有部分攻略但不够完整 |
| 📋 严格按攻略 | 正则提取攻略【地名】，逐项搜索并直接采用 | 有完整详细攻略 |

### 2.3 DIY 调整

- **替换**：点击🔄，AI搜索同路线3-5个备选，含推荐理由
- **添加**：搜索或手动输入景点/餐厅
- **删除**：移除不想要的项
- **优化路线**：最近邻算法重排当天途径点

### 2.4 路线可视化

- **概览地图**：全行程景点标记（绿色） + 路线连线
- **每日迷你地图**：酒店→早餐→景点→午餐→晚餐 完整动线
- **路线详情**：🚌 公交/地铁线路名 + ⏱️耗时 + 📏距离 + 上/下车站

---

## 3. 技术架构深潜

### 3.1 整体架构图

```
┌─────────────────────────────────────────────────┐
│                    Frontend                       │
│  Vue 3 + TypeScript + Ant Design Vue             │
│  高德 JS API (地图渲染)                           │
│  Axios (HTTP通信)                                 │
└────────────────────┬────────────────────────────┘
                     │ HTTP/REST
┌────────────────────▼────────────────────────────┐
│                  FastAPI 后端                     │
│  /api/trip/plan        → LangGraph Planner       │
│  /api/trip/alternatives → 备选项搜索              │
│  /api/trip/extract-url  → URL内容提取              │
│  /api/trip/extract-image → 截图OCR                │
│  /api/poi/photo         → Pexels图片搜索          │
└────────────────────┬────────────────────────────┘
                     │
┌────────────────────▼────────────────────────────┐
│              LangGraph Agent 层                   │
│  ┌──────────────────────────────────────────┐   │
│  │  StateGraph: PlannerState                 │   │
│  │                                           │   │
│  │  START                                    │   │
│  │    ↓                                      │   │
│  │  [ReAct] search_attractions               │   │
│  │    ↓                                      │   │
│  │  [ReAct] search_weather                   │   │
│  │    ↓                                      │   │
│  │  [ReAct] search_hotels                    │   │
│  │    ↓                                      │   │
│  │  generate_plan (纯LLM)                    │   │
│  │    ↓                                      │   │
│  │  END                                      │   │
│  └──────────────────────────────────────────┘   │
└────────────────────┬────────────────────────────┘
                     │ MCP协议 (stdio)
┌────────────────────▼────────────────────────────┐
│         高德地图 MCP Server                       │
│  16个工具: POI搜索、天气查询、路线规划等            │
└─────────────────────────────────────────────────┘
```

### 3.2 技术栈选型理由

| 技术 | 选型理由 |
|------|---------|
| **LangGraph** | 声明式图编排，`tools_condition` 原生支持 ReAct 循环，比手动 while 循环更可靠 |
| **DeepSeek-chat** | 性价比高，中文能力强，兼容 OpenAI SDK |
| **MCP 协议** | 标准化工具接入，amap-mcp-server 开箱即用，无需手写高德 API 调用 |
| **FastAPI** | 异步原生支持，Pydantic v2 类型校验，自动生成 API 文档 |
| **Vue 3 + Ant Design Vue** | 组件库成熟，响应式数据绑定，适合表单+列表类页面 |
| **Pexels** | 免费额度够用（200req/hr），图片质量高于 Unsplash（实测） |

---

## 4. 从 HelloAgents 迁移到 LangGraph

### 为什么从 HelloAgents 迁移到 LangGraph？

| 维度 | HelloAgents | LangGraph |
|------|-------------|-----------|
| Agent 定义 | `SimpleAgent(name, llm, system_prompt)` | `StateGraph` + 自定义 node 函数 |
| 编排方式 | 手动 `agent.run()` 顺序调用 | `add_edge()` 声明式图 |
| 工具调用 | `agent.add_tool(MCPTool)` 黑盒 | `llm.bind_tools(tools)` + `ToolNode` 透明可控 |
| 循环结构 | 不支持 | `tools_condition` 条件边实现 ReAct |
| 状态管理 | 无，手动传参 | `TypedDict` 在节点间自动流转 |
| 可观测性 | 有限 | LangSmith/LangFuse 深度集成 |

迁移后 API 接口不变，前端零改动，且彻底移除了 HelloAgents 依赖（解决 openai 版本冲突）。

### 知识点：StateGraph 是什么？

StateGraph 是 LangGraph 的核心抽象：
- **State**：一个 `TypedDict`，定义所有节点共享的数据结构
- **Node**：一个 Python 函数，接收 State，返回 `dict`（部分 State 更新）
- **Edge**：连接节点的有向边（普通边）或条件边（根据返回值路由）

```python
class PlannerState(TypedDict):
    city: str           # 输入
    attractions_info: str  # search_attractions 产出
    weather_info: str      # search_weather 产出
    trip_plan_json: str    # generate_plan 产出

# 声明式编排
workflow = StateGraph(PlannerState)
workflow.add_node("search_attractions", search_attractions_node)
workflow.add_node("search_weather", search_weather_node)
workflow.add_edge(START, "search_attractions")
workflow.add_edge("search_attractions", "search_weather")
# ...
graph = workflow.compile()
result = await graph.ainvoke(initial_state)
```

---

## 5. ReAct 模式详解

### 什么是 ReAct？

**Re**asoning + **Act**ing = 推理和行动交替进行。

```
传统 LLM 调用:  问题 → LLM → 回答 (一次性)
ReAct 模式:     问题 → 推理 → 行动 → 观察 → 推理 → 行动 → ... → 回答
```

### 本项目中的 ReAct 实现

每个搜索子图内部是一个标准 ReAct 循环：

```python
def _make_search_subgraph(self, system_prompt, result_key):
    class SubState(TypedDict):
        messages: Annotated[List[BaseMessage], add_messages]

    llm_with_tools = self.llm.bind_tools(self.tools)

    workflow = StateGraph(SubState)
    workflow.add_node("agent", agent_node)
    workflow.add_node("tools", ToolNode(self.tools))

    workflow.add_edge(START, "agent")
    workflow.add_conditional_edges(
        "agent",
        tools_condition,  # ← 关键: LangGraph内置条件判断
        # 有tool_calls → "tools" → "agent" (循环)
        # 无tool_calls → END
    )
    workflow.add_edge("tools", "agent")  # 工具结果返回agent继续推理

    return workflow.compile()
```

### ReAct 实际运行示例

```
第1轮:
  Agent推理: "需要搜索杭州景点"
  Agent行动: 调用 maps_text_search(keywords="杭州景点")
  观察结果: [西湖, 雷峰塔, 灵隐寺...] (3个结果)

第2轮:
  Agent推理: "只有3个，不够。试试'杭州公园'"
  Agent行动: 调用 maps_text_search(keywords="杭州公园")
  观察结果: [西溪湿地, 太子湾公园...] (2个结果)

第3轮:
  Agent推理: "现在有5个了，加上攻略提到的断桥残雪，够了"
  无更多tool_calls → END
```

### 知识点：`tools_condition` 原理

```python
def tools_condition(state):
    """检查最后一条消息是否有 tool_calls"""
    messages = state["messages"]
    last_message = messages[-1]
    if hasattr(last_message, "tool_calls") and last_message.tool_calls:
        return "tools"  # 需要执行工具
    return END          # 结束循环
```

这是 ReAct 循环的**终止条件**——当 Agent 认为足够了，它就不再调用工具，循环自然结束。

---

## 6. MCP 协议集成

### 什么是 MCP？

**M**odel **C**ontext **P**rotocol — Anthropic 提出的标准化协议，让 LLM 以统一方式接入外部工具和数据源。

```
传统方式:  每个API单独写适配代码
MCP方式:   MCP Server统一暴露工具 → LLM通过标准协议调用
```

### 本项目 MCP 集成方式

```python
from langchain_mcp_adapters.client import MultiServerMCPClient

# 以stdio传输方式启动高德MCP Server
client = MultiServerMCPClient({
    "amap": {
        "command": "uvx",           # 使用uvx运行
        "args": ["amap-mcp-server"], # 高德MCP Server包名
        "env": {"AMAP_MAPS_API_KEY": api_key},
        "transport": "stdio"         # 通过标准输入输出通信
    }
})

# 获取16个工具
tools = await client.get_tools()

# 绑定到LLM
llm_with_tools = llm.bind_tools(tools)
```

### 可用的高德 MCP 工具

| 工具类别 | 具体工具 |
|---------|---------|
| POI搜索 | `maps_text_search`, `maps_around_search`, `maps_detail` |
| 天气 | `maps_weather` |
| 路线 | `maps_direction_walking`, `maps_direction_driving`, `maps_direction_transit_integrated` |
| 地理 | `maps_geo`, `maps_regeocode`, `maps_ip_location` |

### 知识点：MCP Transport 类型

| Transport | 方式 | 适用场景 |
|-----------|------|---------|
| `stdio` | 标准输入输出 | 本地进程通信（本项目使用） |
| `sse` | Server-Sent Events | 远程 HTTP 服务 |
| `streamable-http` | HTTP 流 | 远程服务（新标准） |

### 知识点：高德 Key 类型

| Key 类型 | 用途 | 本项目中位置 |
|----------|------|------------|
| Web服务 API Key | 服务端调用（搜索、天气、路线） | `backend/.env` → `AMAP_API_KEY` |
| Web端 JS API Key | 前端地图渲染 | `frontend/.env` → `VITE_AMAP_WEB_JS_KEY` |

**Key 类型不匹配会导致 `USERKEY_PLAT_NOMATCH` 错误**——这是本项目中实际遇到并解决的问题。

---

## 7. LLM 输出解析的鲁棒性设计

### 为什么需要鲁棒解析？

DeepSeek-chat 等模型每次返回的 JSON 格式**极不稳定**：
- 有时包在 `{"trip": {...}}` 里
- 有时用 `itinerary` 存景点，有时用 `activities`
- 有时坐标是 `[lng, lat]` 数组，有时是 `{longitude, latitude}` 对象
- 有时景点名在 `name` 字段，有时在 `spot`，有时在 `activity`

### 四级兜底解析器

```
Level 1: 标准JSON解析
    ↓ 失败
Level 2: 字段别名映射 (30+字段变体)
    city / destination,  days / daily_plan / itinerary,
    spots / attractions / activities,  name / spot / activity / title...
    ↓ 失败
Level 3: 智能类型发现
    遍历day对象所有key → 自动找到含{name:...}结构的数组/字典
    ↓ 失败
Level 4: request自动补全
    city, start_date, end_date 等从原始请求填充
```

### 核心代码示例

```python
# 智能发现景点列表
def _find_item_list(raw_day: dict) -> list:
    # 1. 已知key优先
    for k in ("attractions", "spots", "itinerary", "activities", ...):
        if k in raw_day and isinstance(raw_day[k], list):
            return raw_day[k]

    # 2. 嵌套pois展开: [{time_slot: "上午", pois: [...]}] → [...]
    for k in raw_day:
        if isinstance(raw_day[k], list) and isinstance(raw_day[k][0], dict):
            if "pois" in raw_day[k][0]:
                # 展开嵌套...

    # 3. 时间段字典: {morning: {spot:...}, afternoon: {spot:...}}
    for k in ("morning", "afternoon", "evening"):
        if k in raw_day and isinstance(raw_day[k], dict):
            ...

    # 4. 遍历所有dict value
    for k, v in raw_day.items():
        if isinstance(v, dict) and (v.get("name") or v.get("spot")):
            ...
```

### 知识点：为什么 LLM 输出不稳定？

- LLM 是**概率生成**，不是确定性程序
- 每次生成时温度（temperature=0.7）引入随机性
- 训练数据中 JSON 格式多样，模型没有统一的标准
- **解决方案**：要么用 `response_format={type: "json_object"}` 约束（部分模型支持），要么做好鲁棒解析（本项目选择）

---

## 8. 路线优化算法

### 最近邻贪心算法（Nearest Neighbor）

```python
def reoptimize(day_points):
    """
    从第一个点开始，每次找距离当前点最近的未访问点
    时间复杂度: O(n²)
    """
    optimized = [day_points[0]]  # 保持起点不变
    remaining = day_points[1:]

    while remaining:
        last = optimized[-1]
        # 找最近的未访问点
        nearest_idx = min(range(len(remaining)),
                          key=lambda i: haversine(last, remaining[i]))
        optimized.append(remaining.pop(nearest_idx))

    return optimized
```

### Haversine 距离公式

```python
def haversine(lat1, lon1, lat2, lon2):
    """计算球面两点间的大圆距离（单位: km）"""
    R = 6371  # 地球半径
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat/2)² + cos(lat1) × cos(lat2) × sin(dlon/2)²
    return R × 2 × atan2(√a, √(1-a))
```

### 为什么不用真正的 TSP 求解？

- TSP（旅行商问题）是 NP-hard，精确解需要 O(n!)
- 最近邻贪心在 n≤10 时近似最优，且性能足够
- 旅行场景不需要绝对最优，近似最优即可

### 真实公交路线

前端使用高德 JS API 的 `AMap.Transfer` 获取实际公交/地铁线路：

```javascript
const transfer = new AMap.Transfer({ policy: AMap.TransferPolicy.LEAST_TIME })
transfer.search([lng1, lat1], [lng2, lat2], (status, result) => {
    // result.plans[0].routes[].transit.lines[]
    // → "地铁1号线" "公交52路"
})
```

---

## 9. 参考来源融合引擎

### 数据来源

| 来源 | 提取方式 | 技术 |
|------|---------|------|
| 小红书链接 | URL 抓取 | requests + BeautifulSoup + 多UA轮换 |
| 截图 | OCR 识别 | Tesseract + Pillow + Canvas 裁剪 |

### SPA 页面处理

小红书是 SPA（Single Page Application），内容由 JS 动态渲染。简单 HTTP 请求只能拿到空壳 HTML。

```python
# 检测SPA: 页面文本<200字符 → 可能是JS渲染页面
if len(content) < 200:
    return {"success": False, "message": "页面内容极少，可能是JS动态渲染。请手动粘贴内容"}
```

### 攻略内容注入

```python
# 严格模式
if is_strict:
    # 步骤1b: 正则提取【地名】，逐项搜索
    names = re.findall(r'【(.+?)】', reference_content)
    for name in names[:5]:
        search_with_tool(f"maps_text_search: keywords={name}, city={city}")

    # Planner prompt
    prompt += "攻略中提到的景点全部安排，餐厅直接采用，路线顺序不变"

# 混合模式
elif is_hybrid:
    prompt += "攻略地点为骨架，AI补充交通/天气/时间，可添加同路线景点"

# 灵感模式
else:
    prompt += "攻略仅作风格参考（如'攻略偏好老街'→搜索同类地点），不要直接采用具体地点"
```

---

## 10. 前后端数据流

### 请求流程

```
[Home.vue] 用户填写表单
    ↓ POST /api/trip/plan
[FastAPI] 接收 TripRequest
    ↓
[LangGraph] StateGraph.ainvoke()
    ├── search_attractions (ReAct) → 景点搜索结果
    ├── search_weather (ReAct)     → 天气信息
    ├── search_hotels (ReAct)      → 酒店推荐
    └── generate_plan (LLM)        → 行程JSON
    ↓
[_parse_plan_json] 四级兜底解析 → TripPlan (Pydantic)
    ↓
[FastAPI] 序列化为 JSON → {success: true, data: TripPlan}
    ↓
[Home.vue] sessionStorage.setItem('tripPlan', JSON.stringify(data))
    ↓ router.push('/result')
[Result.vue] sessionStorage.getItem('tripPlan') → 渲染
```

### 关键数据模型

```python
# 后端 Pydantic
class TripPlan(BaseModel):
    city: str
    start_date: str
    end_date: str
    days: List[DayPlan]           # 每日行程
    weather_info: List[WeatherInfo]
    overall_suggestions: str
    budget: Optional[Budget]

class DayPlan(BaseModel):
    day_index: int
    attractions: List[Attraction]  # 景点
    meals: List[Meal]              # 餐饮
    hotel: Optional[Hotel]         # 酒店
    route_notes: Optional[str]     # 路线优化说明
```

```typescript
// 前端 TypeScript (完全对齐后端)
interface TripPlan {
    city: string
    days: DayPlan[]
    weather_info: WeatherInfo[]
    budget?: Budget
}
```

### 为什么用 sessionStorage 而不是 Vuex/Pinia？

- 行程数据**不需要响应式共享**（只有一个页面使用）
- 跨页面传递（Home → Result），sessionStorage 最简单
- 刷新页面数据不丢失（用户体验）

---

## 11. 面试高频问题

### Q1: 为什么选择 LangGraph 而不是 LangChain Agent？

**答**: LangChain 的 `create_react_agent` 是高度封装的，定制性差。LangGraph 的 `StateGraph` 让我可以：
- 自定义 State 结构（4 阶段中间结果需要累积传递）
- 自定义每个节点的逻辑（搜索节点 vs 规划节点行为不同）
- 精细控制工具绑定（搜索节点绑工具，规划节点不绑）

### Q2: 如果高德 API 挂了怎么办？

**答**: 三级降级：
1. MCP 工具调用失败 → 纯 LLM 生成（兜底方案 `_create_fallback_plan`）
2. 纯 LLM 也失败 → 返回静态模板（城市名 + 占位景点）
3. 整个请求失败 → FastAPI 500 + 前端 error message

### Q3: 如何处理 LLM 输出不稳定？

**答**: 四级兜底解析器（见第 7 节）。核心是**不信任 LLM 输出，做好防御性编程**。同时前端也做了容错——meals 为空时自动生成默认三餐。

### Q4: 项目中最大的技术挑战是什么？

**答**: LLM JSON 格式的极端不稳定——每次生成都可能有不同的字段名和结构。从最初硬编码几个字段名，演进为智能类型发现的四级解析器。这个设计模式可以应用到任何需要解析 LLM 输出的场景。

### Q5: 为什么不用 RAG？

**答**: RAG 适合检索静态文档。旅行规划需要**实时数据**（天气、POI、路况），这些数据通过 MCP 工具调用获取更合适。不过参考来源功能本质上是简化版 RAG——从网页提取文本 → 注入 prompt。

### Q6: ReAct 循环会不会死循环？

**答**: 不会——`tools_condition` 是 LangGraph 内置条件判断，当 LLM 不再返回 `tool_calls` 时自然终止。另外实际使用中，LLM 通常 2-3 轮就会认为搜索足够。

### Q7: 你怎么知道 LangGraph 而不是继续用 HelloAgents？

**答**: HelloAgents 的 `SimpleAgent` 是黑盒，不支持循环，无法观察中间状态。LangGraph 的 StateGraph 让每一步都可控可观测，而且 `tools_condition` 原生支持 ReAct。迁移后前端零改动——证明 API 接口设计是合理的。

---

## 12. 相关知识点速查

### 12.1 AI Agent 核心概念

| 概念 | 解释 |
|------|------|
| **Agent** | 能自主使用工具、规划任务的 LLM 系统 |
| **Tool** | Agent 可调用的外部函数/API |
| **ReAct** | Reasoning + Acting 交替进行的 Agent 模式 |
| **MCP** | Model Context Protocol，标准化工具接入协议 |
| **StateGraph** | LangGraph 的图编排抽象，节点间通过 State 传递数据 |
| **ToolNode** | LangGraph 预置的工具执行节点 |
| **tools_condition** | LangGraph 内置条件边，检查是否有待执行的 tool_calls |

### 12.2 LLM 相关

| 概念 | 解释 |
|------|------|
| **Temperature** | 控制输出随机性（0=确定，1=最随机）。本项目用 0.7 |
| **System Prompt** | 设定 Agent 角色和行为的指令 |
| **Tool Calling / Function Calling** | LLM 输出 JSON 格式的工具调用请求 |
| **Token** | LLM 输入/输出的基本单位（约 0.75 个英文单词或 0.5 个中文字） |
| **Context Window** | LLM 单次能处理的最大 token 数 |

### 12.3 工程实践

| 概念 | 解释 |
|------|------|
| **Pydantic** | Python 数据校验库，本项目用于请求/响应模型类型校验 |
| **TypedDict** | Python 类型标注，LangGraph 用它定义 State 结构 |
| **async/await** | Python 异步编程，FastAPI + LangGraph 全链路异步 |
| **Vite** | 前端构建工具，比 Webpack 快 10-100 倍 |
| **sessionStorage** | 浏览器标签页级存储，关闭标签页即清除 |

---

## 附录：项目启动命令

```powershell
# 后端
cd backend
venv\Scripts\activate
uvicorn app.api.main:app --reload --host 0.0.0.0 --port 8000

# 前端
cd frontend
npm run dev

# 访问
http://localhost:5173       # 前端页面
http://localhost:8000/docs  # API文档
```

## 附录：关键文件索引

| 文件 | 作用 |
|------|------|
| `backend/app/agents/langgraph_planner.py` | LangGraph 引擎（核心） |
| `backend/app/api/routes/trip.py` | 主 API 路由（规划、SSE流式、URL提取、OCR） |
| `backend/app/services/image_service.py` | Pexels 图片搜索服务 |
| `backend/app/models/schemas.py` | Pydantic 数据模型 |
| `frontend/src/views/Home.vue` | 首页（表单 + 参考来源） |
| `frontend/src/views/Result.vue` | 结果页（行程展示 + DIY + 地图） |
| `frontend/src/types/index.ts` | TypeScript 类型定义 |
