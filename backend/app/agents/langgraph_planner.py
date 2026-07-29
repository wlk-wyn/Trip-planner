"""
LangGraph 多智能体旅行规划系统
ReAct 架构: Agent ←→ Tools 循环，观察-推理-行动，直到完成任务

Graph (P0 优化后 - 并行搜索):
START ─┬─→ AttractionReAct ─┐
       ├─→ WeatherReAct    ─┼─→ Planner → END
       └─→ HotelReAct      ┘
       每个 ReAct 节点内部: agent ⇄ tools (条件循环)
       三个搜索节点并行执行，全部完成后进入规划节点
"""

import os
import json
import re
from typing import TypedDict, List, Optional, Annotated, Literal
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition
from langgraph.checkpoint.memory import MemorySaver
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage, AIMessage, BaseMessage, ToolMessage
from langchain_mcp_adapters.client import MultiServerMCPClient
from ..config import get_settings
from ..models.schemas import TripRequest, TripPlan, DayPlan, Attraction, Meal, WeatherInfo, Location, Hotel
from ..services.cache_service import cache, CACHE_TTL_WEATHER, CACHE_TTL_ATTRACTION, CACHE_TTL_HOTEL


# ============ 酒店特殊要求关键词 ============

HOTEL_SPECIAL_KEYWORDS = [
    "地铁", "近地铁", "地铁口", "地铁站",
    "市中心", "城中心", "商圈", "步行街",
    "五星", "四星级", "豪华", "奢华", "高端",
    "民宿", "青旅", "客栈", "公寓",
    "江景", "海景", "湖景", "山景",
    "亲子", "儿童", "带娃",
    "早餐", "含早", "双早",
    "停车", "免费停车",
    "泳池", "健身房", "SPA",
    "安静", "隔音", "高层", "阳台",
    "品牌", "希尔顿", "万豪", "洲际", "如家", "汉庭", "7天", "锦江之星", "全季", "亚朵",
]


def _has_hotel_special_requirements(
    free_text_input: str,
    accommodation: str,
    preferences: List[str],
) -> bool:
    """判断用户是否对酒店有特殊要求"""
    text = (free_text_input or "").strip().lower()
    acc = (accommodation or "").strip()
    prefs = " ".join(preferences or []).lower()

    if any(kw.lower() in text for kw in HOTEL_SPECIAL_KEYWORDS):
        return True
    if any(kw.lower() in prefs for kw in HOTEL_SPECIAL_KEYWORDS):
        return True
    return False


# ============ State ============

def _str_reducer(old: str, new: str) -> str:
    """字符串 reducer: 并行分支合并时，非空新值覆盖旧值"""
    return new if new else old


class PlannerState(TypedDict):
    """顶层状态: 4个阶段的输入输出"""
    # 输入
    city: str
    start_date: str
    end_date: str
    travel_days: int
    transportation: str
    accommodation: str
    preferences: List[str]
    free_text_input: str
    reference_content: str
    reference_mode: str
    # 中间结果 (使用 reducer 支持并行分支合并)
    attractions_info: Annotated[str, _str_reducer]
    weather_info: Annotated[str, _str_reducer]
    hotels_info: Annotated[str, _str_reducer]
    # ReAct消息 (每个阶段独立的消息历史)
    attraction_messages: Annotated[List[BaseMessage], add_messages]
    weather_messages: Annotated[List[BaseMessage], add_messages]
    hotel_messages: Annotated[List[BaseMessage], add_messages]
    # 输出
    trip_plan_json: str
    error: str


# ============ System Prompts ============

ATTRACTION_SYSTEM = """你是景点搜索专家。你的任务是为用户搜索指定城市的景点。

**工作流程 (ReAct循环):**
1. 分析用户需求 → 决定搜索什么关键词
2. 调用高德地图搜索工具 → 观察返回结果
3. 如果结果不够 → 换关键词继续搜索
4. 如果结果充足 → 整理输出，包含每个景点的名称、地址、坐标

你可以多次调用工具，直到收集到足够的景点信息。"""

WEATHER_SYSTEM = """你是天气查询专家。你的任务是为用户查询指定城市的天气。

**工作流程 (ReAct循环):**
1. 调用天气工具查询指定城市
2. 如果缺少某些日期的天气 → 继续查询
3. 整理输出完整的天气预报"""

HOTEL_SYSTEM = """你是酒店推荐专家。你的任务是为用户搜索指定城市的酒店。

**工作流程 (ReAct循环):**
1. 根据用户偏好(经济型/舒适型/豪华型)搜索酒店
2. 如果搜索结果不理想 → 尝试不同关键词(如"快捷酒店""民宿""宾馆")
3. 整理输出，包含酒店名称、地址、坐标、价格"""

PLANNER_SYSTEM = """你是行程规划专家。根据收集到的景点、天气、酒店信息生成旅行计划JSON。

**JSON格式（严格按照此结构，每个字段都必须填写真实数据）:**
```json
{
  "start_date": "YYYY-MM-DD",
  "end_date": "YYYY-MM-DD",
  "travel_days": 3,
  "overall_suggestions": "总体建议",
  "days": [{
    "day": 1,
    "date": "YYYY-MM-DD",
    "weather": "天气简述",
    "accommodation": {"name": "酒店名", "address": "地址", "coordinate": [lng, lat], "price_range": "300-500元"},
    "activities": [
      {"time": "09:00-11:30", "spot": "景点名", "address": "地址", "coordinates": [lng, lat], "duration": 150, "description": "景点描述", "category": "类别", "ticket_price": 60},
      {"time": "14:00-16:00", "spot": "景点名", "address": "地址", "coordinates": [lng, lat], "duration": 120, "description": "景点描述", "category": "类别", "ticket_price": 0}
    ],
    "meals": [
      {"type": "breakfast", "restaurant": "餐厅名", "address": "地址", "coordinates": [lng, lat], "recommended_dishes": ["招牌菜1","招牌菜2"], "description": "推荐理由", "avg_cost": 30},
      {"type": "lunch", "restaurant": "餐厅名", "address": "地址", "coordinates": [lng, lat], "recommended_dishes": ["招牌菜"], "description": "推荐理由", "avg_cost": 60},
      {"type": "dinner", "restaurant": "餐厅名", "address": "地址", "coordinates": [lng, lat], "recommended_dishes": ["招牌菜"], "description": "推荐理由", "avg_cost": 90}
    ]
  }]
}
```

**关键要求:**
- 每个meal的restaurant必须是具体餐厅名称(字符串),不是对象
- recommended_dishes必须是数组,包含1-3个真实菜名
- 坐标(coordinates/coordinate)用[lng, lat]数组格式
- 每天必须包含完整的早中晚三餐
- 直接返回JSON,不要包wrapper,不要用驼峰命名"""


# ============ LangGraph ReAct Planner ============

class LangGraphTripPlanner:
    """基于 LangGraph ReAct 的多智能体旅行规划器"""

    def __init__(self):
        settings = get_settings()
        self.llm = ChatOpenAI(
            model=os.getenv("LLM_MODEL_ID", "deepseek-chat"),
            api_key=os.getenv("LLM_API_KEY"),
            base_url=os.getenv("LLM_BASE_URL", "https://api.deepseek.com"),
            temperature=0.7
        )
        self.mcp_client = None
        self.tools = []
        self._initialized = False

    async def _ensure_initialized(self):
        if self._initialized:
            return
        settings = get_settings()
        try:
            self.mcp_client = MultiServerMCPClient({
                "amap": {
                    "command": "uvx",
                    "args": ["amap-mcp-server"],
                    "env": {"AMAP_MAPS_API_KEY": settings.amap_api_key},
                    "transport": "stdio"
                }
            })
            self.tools = await self.mcp_client.get_tools()
            self._tool_index = {getattr(t, "name", str(t)): t for t in self.tools}
            self._initialized = True
            print(f"✅ LangGraph MCP 工具: {len(self.tools)} 个")
        except Exception as e:
            print(f"⚠️ MCP 加载失败: {e}")
            self.tools = []
            self._tool_index = {}
            self._initialized = True

    def _get_tool(self, name: str):
        """按名称获取MCP工具"""
        return self._tool_index.get(name)

    async def _call_tool_direct(self, tool_name: str, arguments: dict) -> str:
        """直接调用MCP工具，返回结果字符串（不走ReAct循环）"""
        tool = self._get_tool(tool_name)
        if tool is None:
            print(f"   ⚠️ 未找到工具: {tool_name}，回退到ReAct")
            return ""
        try:
            result = await tool.ainvoke(arguments)
            # Tool调用结果可能是ToolMessage或str
            if isinstance(result, ToolMessage):
                content = result.content
            elif isinstance(result, str):
                content = result
            elif isinstance(result, BaseMessage):
                content = getattr(result, "content", "")
            else:
                content = str(result)
            # content可能是list[dict]（多模态），提取text
            if isinstance(content, list):
                parts = []
                for part in content:
                    if isinstance(part, dict) and "text" in part:
                        parts.append(str(part["text"]))
                    elif isinstance(part, str):
                        parts.append(part)
                    else:
                        parts.append(str(part))
                content = "\n".join(parts)
            return str(content) if content is not None else ""
        except Exception as e:
            print(f"   ⚠️ 工具 {tool_name} 调用失败: {e}，回退到ReAct")
            return ""

    # ============ ReAct 搜索节点 (内部循环) ============

    def _make_search_subgraph(self, system_prompt: str, result_key: str):
        """
        创建 ReAct 搜索子图: agent ⇄ tools 循环
        使用 ToolNode + tools_condition 实现标准 ReAct 模式

        ┌─────────┐    has tool_calls    ┌───────┐
        │  agent  │ ─────────────────→   │ tools │
        │ (LLM)   │ ←─────────────────   │       │
        └─────────┘    return result      └───────┘
             │
        no tool_calls → 提取结果到 state[result_key]
        """
        # 子图状态
        class SubState(TypedDict):
            messages: Annotated[List[BaseMessage], add_messages]

        llm_with_tools = self.llm.bind_tools(self.tools) if self.tools else self.llm

        def agent_node(state: SubState) -> dict:
            response = llm_with_tools.invoke(
                [SystemMessage(content=system_prompt)] + state["messages"]
            )
            return {"messages": [response]}

        workflow = StateGraph(SubState)
        workflow.add_node("agent", agent_node)

        if self.tools:
            workflow.add_node("tools", ToolNode(self.tools))
            workflow.add_edge(START, "agent")
            workflow.add_conditional_edges(
                "agent",
                tools_condition,  # 内置: 有 tool_calls → "tools", 否则 → END
            )
            workflow.add_edge("tools", "agent")  # 工具结果返回agent继续推理
        else:
            workflow.add_edge(START, "agent")
            workflow.add_edge("agent", END)

        return workflow.compile()

    async def _run_react_search(self, subgraph, initial_message: str) -> str:
        """运行 ReAct 子图，提取最终文本结果"""
        try:
            result = await subgraph.ainvoke({"messages": [HumanMessage(content=initial_message)]})
            messages = result.get("messages", [])
            # 提取最后一条 AI 消息的内容作为结果
            for msg in reversed(messages):
                if isinstance(msg, AIMessage) and msg.content:
                    return msg.content
            return str(messages[-1].content) if messages else ""
        except Exception as e:
            print(f"   ReAct 搜索异常: {e}")
            return ""

    # ============ 顶层 Graph Nodes ============

    async def _attraction_node(self, state: PlannerState) -> dict:
        print("📍 [ReAct] 搜索景点...")
        await self._ensure_initialized()

        preferences = state.get("preferences", [])
        keywords = preferences[0] if preferences else "景点"
        city = state["city"]

        # 缓存检查: 缓存 key 包含城市+偏好+参考模式
        ref_mode = state.get("reference_mode", "")
        ref_content = state.get("reference_content", "")
        cache_key = f"attractions:{city}:{','.join(preferences)}:{ref_mode}:{ref_content[:100] if ref_content else ''}"
        cached = cache.get(cache_key)
        if cached:
            print(f"   ✅ 景点缓存命中，跳过搜索")
            return {"attractions_info": cached}

        query = f"请搜索{city}的{keywords}相关景点。对每个景点记录名称、地址、坐标。"

        # 严格/混合模式: 追加攻略中的具体地名
        if ref_mode in ("strict", "hybrid") and ref_content:
            names = _extract_place_names(ref_content, city)
            if names:
                query += f"\n请额外搜索以下攻略提到的地点: {', '.join(names[:5])}"

        subgraph = self._make_search_subgraph(ATTRACTION_SYSTEM, "attractions_info")
        result = await self._run_react_search(subgraph, query)
        print(f"   景点搜索完成 ({len(result)} 字符)")
        result = result[:3000]
        cache.set(cache_key, result, CACHE_TTL_ATTRACTION)
        return {"attractions_info": result}

    async def _weather_node(self, state: PlannerState) -> dict:
        print("🌤️  查询天气...")
        await self._ensure_initialized()

        city = state["city"]
        start_date = state["start_date"]
        end_date = state["end_date"]

        cache_key = f"weather:{city}:{start_date}:{end_date}"
        cached = cache.get(cache_key)
        if cached:
            print(f"   ✅ 天气缓存命中，跳过查询")
            return {"weather_info": cached}

        # P2 优化: 直接调用 maps_weather 工具，省去 LLM 推理环节
        result = ""
        tool_output = await self._call_tool_direct("maps_weather", {"city": city})
        if tool_output.strip():
            print(f"   [Direct] 天气API直接调用成功")
            # 包装一下，明确告诉规划节点这段天气对应的日期范围
            result = (
                f"{city} 天气情况（时间范围: {start_date} 至 {end_date}）\n"
                f"原始天气数据:\n{tool_output}"
            )
        else:
            # 兜底: 直接调用失败，回退到 ReAct 模式
            print(f"   [ReAct] 直接调用失败，回退ReAct模式...")
            query = f"请查询{city}从{start_date}到{end_date}每天的天气"
            subgraph = self._make_search_subgraph(WEATHER_SYSTEM, "weather_info")
            result = await self._run_react_search(subgraph, query)

        print(f"   天气查询完成 ({len(result)} 字符)")
        result = result[:2000]
        cache.set(cache_key, result, CACHE_TTL_WEATHER)
        return {"weather_info": result}

    async def _hotel_node(self, state: PlannerState) -> dict:
        print("🏨 搜索酒店...")
        await self._ensure_initialized()

        city = state["city"]
        acc = state["accommodation"]
        free_text = state.get("free_text_input", "") or ""
        prefs = state.get("preferences", []) or []

        cache_key = f"hotel:{city}:{acc}"
        cached = cache.get(cache_key)
        if cached:
            print(f"   ✅ 酒店缓存命中，跳过搜索")
            return {"hotels_info": cached}

        # P2 优化: 判断是否需要特殊处理
        need_react = _has_hotel_special_requirements(free_text, acc, prefs)
        result = ""

        if not need_react:
            # 无特殊要求: 直接调用 maps_text_search
            print(f"   [Direct] 无特殊酒店要求，直接API搜索...")
            keywords = f"{city} {acc}"
            tool_output = await self._call_tool_direct(
                "maps_text_search",
                {"keywords": keywords, "city": city, "citylimit": True},
            )
            if tool_output.strip():
                print(f"   [Direct] 酒店API直接调用成功")
                # 包装一下，保持与ReAct输出格式语义一致
                result = (
                    f"{city} {acc} 酒店推荐（普通筛选）\n"
                    f"搜索关键词: {keywords}\n"
                    f"搜索结果:\n{tool_output}"
                )
            else:
                print(f"   ⚠️ 直接搜索为空，尝试扩大关键词 fallback...")
                for alt_kw in [f"{city} 酒店", f"{city} 快捷酒店 宾馆 民宿"]:
                    fallback = await self._call_tool_direct(
                        "maps_text_search",
                        {"keywords": alt_kw, "city": city, "citylimit": True},
                    )
                    if fallback.strip():
                        print(f"   [Fallback] 关键词'{alt_kw}'搜索成功")
                        result = (
                            f"{city} {acc} 酒店推荐（普通筛选，已扩大关键词范围到 '{alt_kw}'）\n"
                            f"搜索结果:\n{fallback}"
                        )
                        break

        if not result:
            # 有特殊要求 OR 直接调用失败 → 走 ReAct 模式
            if need_react:
                print(f"   [ReAct] 检测到特殊酒店要求，使用智能推理模式")
            else:
                print(f"   [ReAct] 直接搜索均为空，回退智能模式...")
            query = f"请搜索{city}的{acc}酒店。如果搜索结果少，尝试'快捷酒店''宾馆''民宿'等关键词。记录名称、地址、坐标、价格。"
            if state.get("reference_mode") in ("strict", "hybrid") and state.get("reference_content"):
                query += f"\n攻略参考: {state['reference_content'][:500]}"
            subgraph = self._make_search_subgraph(HOTEL_SYSTEM, "hotels_info")
            result = await self._run_react_search(subgraph, query)

        print(f"   酒店搜索完成 ({len(result)} 字符)")
        result = result[:2000]
        cache.set(cache_key, result, CACHE_TTL_HOTEL)
        return {"hotels_info": result}

    async def _planner_node(self, state: PlannerState) -> dict:
        print("📋 生成行程计划...")

        is_strict = state.get("reference_mode") == "strict"
        is_hybrid = state.get("reference_mode") == "hybrid"

        query = f"""请根据以下信息生成{state['city']}的{state['travel_days']}天旅行计划:

**基本信息:**
- 城市: {state['city']} | 日期: {state['start_date']} ~ {state['end_date']} | {state['travel_days']}天
- 交通: {state['transportation']} | 住宿: {state['accommodation']}
- 偏好: {', '.join(state.get('preferences', []))}

**景点:** {state.get('attractions_info', '')[:2000]}
**天气:** {state.get('weather_info', '')[:1500]}
**酒店:** {state.get('hotels_info', '')[:1500]}

每天2-3个景点, 地理就近排列, 每餐推荐具体餐厅(名称+地址+坐标+招牌菜)。返回完整JSON。"""
        if state.get("free_text_input"):
            query += f"\n额外要求: {state['free_text_input']}"

        ref = state.get("reference_content", "")
        if ref and ref.strip():
            if is_strict:
                query += f"\n⚠️ 严格模式: 直接采用攻略内容制定行程\n{ref}"
            elif is_hybrid:
                query += f"\n🔗 混合模式: 攻略为主,AI补充\n{ref}"
            else:
                query += f"\n💡 灵感模式: 参考风格,不直接用具体地点\n{ref}"

        response = await self.llm.ainvoke([
            SystemMessage(content=PLANNER_SYSTEM),
            HumanMessage(content=query)
        ])
        print(f"   行程生成完成 ({len(response.content)} 字符)")
        return {"trip_plan_json": response.content}

    # ============ 主 Graph ============

    async def get_alternatives_async(self, city: str, day_index: int, replace_type: str,
                                      current_name: str, current_category: str = "", context: str = "") -> list:
        """获取AI优化的备选项(使用LangGraph LLM + MCP工具)"""
        await self._ensure_initialized()
        print(f"🔄 [LangGraph] 搜索备选{replace_type}: {city} (排除'{current_name}')")

        if replace_type == "attraction":
            keyword = current_category or "景点"
            system = "你是景点搜索专家。请搜索城市景点，为每个结果返回名称、地址、坐标、类型。"
            query = f"请搜索{city}的{keyword}景点（排除{current_name}），返回5个最佳备选。格式: [{{\"name\":\"景点名\",\"address\":\"地址\",\"location\":{{\"longitude\":x,\"latitude\":y}},\"reason\":\"推荐理由\"}}]"
        elif replace_type == "meal":
            system = "你是美食搜索专家。请搜索城市餐厅，为每个结果返回名称、地址、坐标、招牌菜。"
            query = f"请搜索{city}的特色餐厅或美食（排除{current_name}），返回5个最佳备选。格式: [{{\"name\":\"餐厅名\",\"address\":\"地址\",\"location\":{{\"longitude\":x,\"latitude\":y}},\"recommended_dish\":\"招牌菜\",\"estimated_cost\":人均价格,\"reason\":\"推荐理由\"}}]"
        else:
            return []

        if context:
            query += f"\n行程上下文: {context}"

        try:
            llm_with_tools = self.llm.bind_tools(self.tools) if self.tools else self.llm
            messages = [SystemMessage(content=system), HumanMessage(content=query)]

            # ReAct循环: 搜索直到满意
            max_rounds = 3
            for _ in range(max_rounds):
                response = await llm_with_tools.ainvoke(messages)
                messages.append(response)

                if hasattr(response, 'tool_calls') and response.tool_calls:
                    for tc in response.tool_calls:
                        tool = next((t for t in self.tools if t.name == tc["name"]), None)
                        if tool:
                            result = await tool.ainvoke(tc["args"])
                            messages.append(ToolMessage(content=str(result), tool_call_id=tc["id"]))
                else:
                    break  # 无更多工具调用，搜索完成

            # 解析结果
            result_text = messages[-1].content if hasattr(messages[-1], 'content') else str(messages[-1])
            return _parse_alt_json(result_text, city, replace_type)

        except Exception as e:
            print(f"⚠️ 备选搜索失败: {e}")
            return []

    async def search_poi_async(self, keyword: str, city: str) -> list:
        """搜索POI用于DIY添加"""
        await self._ensure_initialized()
        query = f"请搜索{city}的'{keyword}'，返回匹配结果。格式: [{{\"name\":\"名称\",\"address\":\"地址\",\"location\":{{\"longitude\":x,\"latitude\":y}},\"type\":\"景点或餐厅\"}}]"
        messages = [SystemMessage(content="搜索POI，返回JSON数组"), HumanMessage(content=query)]

        llm_with_tools = self.llm.bind_tools(self.tools) if self.tools else self.llm
        for _ in range(3):
            resp = await llm_with_tools.ainvoke(messages)
            messages.append(resp)
            if hasattr(resp, 'tool_calls') and resp.tool_calls:
                for tc in resp.tool_calls:
                    tool = next((t for t in self.tools if t.name == tc["name"]), None)
                    if tool:
                        result = await tool.ainvoke(tc["args"])
                        messages.append(ToolMessage(content=str(result), tool_call_id=tc["id"]))
            else:
                break
        return _parse_alt_json(messages[-1].content, city, "attraction")

    async def reoptimize_async(self, day_points: list) -> list:
        """对途径点按地理位置重新排序(最近邻算法)"""
        import math

        def haversine(p1, p2):
            lat1, lng1 = p1["lat"], p1["lng"]
            lat2, lng2 = p2["lat"], p2["lng"]
            R = 6371
            dlat = math.radians(lat2 - lat1)
            dlng = math.radians(lng2 - lng1)
            a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1))*math.cos(math.radians(lat2))*math.sin(dlng/2)**2
            return R * 2 * math.asin(math.sqrt(a))

        if len(day_points) <= 2:
            return day_points

        # 最近邻贪心: 从第一个点开始, 每次找最近的未访问点
        remaining = list(day_points)
        optimized = [remaining.pop(0)]  # 保持第一个点不变(通常是酒店或出发点)

        while remaining:
            last = optimized[-1]
            # 找最近的点
            nearest_idx = min(range(len(remaining)),
                              key=lambda i: haversine(last, remaining[i]))
            optimized.append(remaining.pop(nearest_idx))

        # 计算总距离变化
        old_dist = sum(haversine(day_points[i], day_points[i+1]) for i in range(len(day_points)-1))
        new_dist = sum(haversine(optimized[i], optimized[i+1]) for i in range(len(optimized)-1))
        print(f"   🗺️ 路线优化: {old_dist:.1f}km → {new_dist:.1f}km (节省{old_dist-new_dist:.1f}km)")

        return optimized

    def _build_graph(self):
        """构建主 StateGraph: 3个并行搜索 + 1个规划节点
        
        优化: 景点/天气/酒店三个搜索节点并行执行，
        全部完成后进入规划节点，预计提速 40%+
        """
        workflow = StateGraph(PlannerState)

        workflow.add_node("search_attractions", self._attraction_node)
        workflow.add_node("search_weather", self._weather_node)
        workflow.add_node("search_hotels", self._hotel_node)
        workflow.add_node("generate_plan", self._planner_node)

        # 并行 fan-out: START 同时启动三个搜索节点
        workflow.add_edge(START, "search_attractions")
        workflow.add_edge(START, "search_weather")
        workflow.add_edge(START, "search_hotels")

        # fan-in: 三个搜索节点全部完成后，进入规划节点
        workflow.add_edge("search_attractions", "generate_plan")
        workflow.add_edge("search_weather", "generate_plan")
        workflow.add_edge("search_hotels", "generate_plan")

        workflow.add_edge("generate_plan", END)

        return workflow.compile()

    # ============ 公共接口 ============

    async def plan_trip_async(self, request: TripRequest) -> TripPlan:
        print(f"\n{'='*60}")
        print(f"🚀 [LangGraph ReAct] {request.city} | {request.travel_days}天")
        print(f"{'='*60}")

        # 优化: 预先初始化 MCP 客户端，避免并行节点并发初始化导致竞态条件
        await self._ensure_initialized()

        graph = self._build_graph()
        state: PlannerState = {
            "city": request.city,
            "start_date": request.start_date,
            "end_date": request.end_date,
            "travel_days": request.travel_days,
            "transportation": request.transportation,
            "accommodation": request.accommodation,
            "preferences": request.preferences,
            "free_text_input": request.free_text_input or "",
            "reference_content": request.reference_content or "",
            "reference_mode": request.reference_mode or "flexible",
            "attractions_info": "", "weather_info": "", "hotels_info": "",
            "attraction_messages": [], "weather_messages": [], "hotel_messages": [],
            "trip_plan_json": "", "error": "",
        }

        try:
            result = await graph.ainvoke(state)
            trip_plan = _parse_plan_json(result.get("trip_plan_json", ""), request)
            print("✅ [LangGraph ReAct] 规划完成!")
            return trip_plan
        except Exception as e:
            print(f"❌ 失败: {e}")
            import traceback
            traceback.print_exc()
            return _create_fallback_plan(request)


# ============ 辅助函数 ============

def _extract_place_names(content: str, city: str) -> List[str]:
    patterns = [r'【(.+?)】', r'「(.+?)」', r'"(.+?)"', r'《(.+?)》']
    names = []
    for p in patterns:
        names.extend([n.strip() for n in re.findall(p, content[:2000]) if 2 <= len(n.strip()) <= 20])
    seen = set()
    result = []
    for n in names:
        if n not in seen and n != city:
            seen.add(n)
            result.append(n)
    return result[:5]


def _parse_plan_json(response: str, request: TripRequest) -> TripPlan:
    try:
        # 提取 JSON 文本
        if "```json" in response:
            s = response.find("```json") + 7
            e = response.find("```", s)
            json_str = response[s:e].strip()
        elif "```" in response:
            s = response.find("```") + 3
            e = response.find("```", s)
            json_str = response[s:e].strip()
        elif "{" in response:
            json_str = response[response.find("{"):response.rfind("}") + 1]
        else:
            raise ValueError("未找到JSON")

        data = json.loads(json_str)
        print(f"   🔍 LLM返回顶层keys: {list(data.keys())}")

        # 处理多层嵌套
        for wrapper in ("trip", "tripPlan", "trip_plan", "plan", "data", "result"):
            if wrapper in data and isinstance(data[wrapper], dict):
                data = data[wrapper]
                break

        # ---- 顶层字段映射 ----
        if "city" not in data:
            data["city"] = data.get("destination", request.city)
        if "start_date" not in data:
            data["start_date"] = data.get("startDate", request.start_date)
        if "end_date" not in data:
            data["end_date"] = data.get("endDate", request.end_date)
        if "travel_days" not in data:
            data["travel_days"] = data.get("travelDays", request.travel_days)
        if "overall_suggestions" not in data:
            data["overall_suggestions"] = data.get(
                "overallSuggestions",
                data.get("summary", data.get("overall_summary", f"祝您在{request.city}旅途愉快!"))
            )
        if "weather_info" not in data:
            data["weather_info"] = data.get("weatherInfo", data.get("weather", []))
        if "budget" not in data:
            data["budget"] = data.get("totalBudget", data.get("budgets", data.get("cost")))

        # ---- days 别名映射 ----
        for alt in ("days", "daily_plan", "daily_plans", "dailyPlans", "itinerary", "schedule", "daily_schedule", "dayPlans"):
            if alt in data:
                data["days"] = data.pop(alt)
                break

        # ---- 规范化每个 day ----
        normalized_days = []
        for i, raw_day in enumerate(data.get("days", [])):
            if i == 0:
                print(f"   🔍 Day0 raw keys: {list(raw_day.keys())}")
                # 打印所有list类型value的key
                list_keys = [k for k, v in raw_day.items() if isinstance(v, list)]
                print(f"   🔍 Day0 list keys: {list_keys}")
                for lk in list_keys:
                    sample = raw_day[lk][0] if raw_day[lk] else "EMPTY"
                    print(f"   🔍   {lk}[0] type={type(sample).__name__}, keys={list(sample.keys()) if isinstance(sample, dict) else 'N/A'}")

            # 智能发现: 搜索raw_day中所有可能包含景点/餐饮/酒店的key
            found_attrs = _find_item_list(raw_day)
            found_meals = _find_meal_list(raw_day)
            found_hotel = _find_hotel_in_day(raw_day)

            # accommodation可能是dict(LLM把酒店放在这里了)
            acc_value = raw_day.get("accommodation", raw_day.get("accommodation_type", request.accommodation))
            if isinstance(acc_value, dict):
                if not found_hotel:
                    found_hotel = acc_value
                acc_str = acc_value.get("name", acc_value.get("hotel_name", request.accommodation))
            else:
                acc_str = str(acc_value) if acc_value else request.accommodation

            day = {
                "date": raw_day.get("date", raw_day.get("day_date", "")),
                "day_index": _normalize_day_index(raw_day, i),
                "description": raw_day.get("description", raw_day.get("summary", raw_day.get("desc", raw_day.get("overview", "")))),
                "transportation": raw_day.get("transportation", raw_day.get("transport", request.transportation)),
                "accommodation": acc_str,
                "route_notes": raw_day.get("route_notes", raw_day.get("routeNotes", raw_day.get("route_note", ""))),
                "hotel": _normalize_hotel(found_hotel) if found_hotel else None,
                "attractions": [_normalize_attraction(a) for a in found_attrs if isinstance(a, dict)],
                "meals": [_normalize_meal(m, i) for m in found_meals if isinstance(m, dict)],
            }

            # 兜底餐饮
            if not day["meals"]:
                for mi, mt in enumerate(["breakfast", "lunch", "dinner"]):
                    day["meals"].append({"type": mt, "name": f"第{i+1}天{'早午晚'[mi]}餐", "description": "当地美食"})

            normalized_days.append(day)

        data["days"] = normalized_days

        trip = TripPlan(**data)
        # 调试: 打印第一天数据
        if trip.days:
            d0 = trip.days[0]
            print(f"   📋 Day0: attractions={len(d0.attractions)}, meals={len(d0.meals)}, hotel={d0.hotel.name if d0.hotel else 'None'}")
            if d0.attractions:
                a0 = d0.attractions[0]
                print(f"   📋 Attr0: name='{a0.name}', addr='{a0.address[:30] if a0.address else ''}', dur={a0.visit_duration}")
            if d0.meals:
                m0 = d0.meals[0]
                print(f"   📋 Meal0: type='{m0.type}', name='{m0.name}', restaurant='{m0.restaurant}'")
        return trip
    except Exception as e:
        print(f"⚠️ JSON解析失败: {e}")
        import traceback; traceback.print_exc()
        return _create_fallback_plan(request)


def _find_item_list(raw_day: dict) -> list:
    """智能发现景点列表"""
    for k in ("attractions", "spots", "sights", "sightseeing", "scenic_spots",
              "attraction_list", "scenic", "places", "points", "locations",
              "attractionList", "spotsList", "items", "itinerary", "activities",
              "schedule", "plan", "timeline", "route", "activity_list", "sights_list"):
        if k in raw_day and isinstance(raw_day[k], list) and len(raw_day[k]) > 0:
            items = raw_day[k]
            if isinstance(items[0], dict):
                first_keys = set(items[0].keys())
                for sub_key in ("pois", "items", "spots", "attractions", "points", "activities"):
                    if sub_key in first_keys:
                        unfolded = []
                        for it in items:
                            if isinstance(it.get(sub_key), list):
                                unfolded.extend(it[sub_key])
                        if unfolded:
                            return unfolded
            return items
    # 兜底1: 搜索任何包含景点特征的数组
    for k, v in raw_day.items():
        if isinstance(v, list) and len(v) > 0 and isinstance(v[0], dict):
            item_keys = set(v[0].keys())
            if item_keys & {"name", "activity", "title", "spot", "spot_name", "attraction_name", "sight"}:
                return v

    # 兜底2: LLM可能用 morning/afternoon/evening 等时间段字典
    time_slot_keys = ("morning", "afternoon", "evening", "am", "pm", "night",
                       "上午", "下午", "晚上", "早晨", "中午")
    attrs = []
    for k in time_slot_keys:
        if k in raw_day and isinstance(raw_day[k], dict):
            item = dict(raw_day[k])
            if not item.get("name"):
                item["name"] = item.get("spot", item.get("activity", item.get("title", k)))
            if "name" in item or "spot" in item or "activity" in item:
                attrs.append(item)
    if attrs:
        return attrs

    # 兜底3: 遍历所有dict value, 看是否像景点
    for k, v in raw_day.items():
        if isinstance(v, dict) and k not in ("hotel", "accommodation", "weather", "accommodation_detail"):
            if v.get("name") or v.get("spot") or v.get("activity") or v.get("title"):
                if k not in [a.get("_source_key") for a in attrs]:
                    v = dict(v)
                    v["_source_key"] = k
                    attrs.append(v)
    if attrs:
        return attrs

    return []

def _find_meal_list(raw_day: dict) -> list:
    """智能发现餐饮列表"""
    for k in ("meals", "meal", "dining", "restaurants", "food", "foods",
              "meal_list", "dining_options", "restaurant_list", "mealList", "diningList"):
        if k in raw_day and isinstance(raw_day[k], list):
            return raw_day[k]
    # 也尝试从itinerary中提取食物相关项
    itinerary = raw_day.get("itinerary", raw_day.get("activities", raw_day.get("schedule", [])))
    if isinstance(itinerary, list):
        food_keywords = ["餐", "食", "吃", "饭", "菜", "馆", "厅", "breakfast", "lunch", "dinner", "food", "restaurant"]
        food_items = []
        for item in itinerary:
            if isinstance(item, dict):
                text = str(item.get("activity", "")) + str(item.get("description", "")) + str(item.get("name", ""))
                if any(kw in text for kw in food_keywords):
                    food_items.append(item)
        if food_items:
            return food_items
    return []

def _find_hotel_in_day(raw_day: dict) -> dict | None:
    """智能发现酒店"""
    for k in ("hotel", "accommodation", "accommodation_detail", "hotel_info",
              "stay", "lodging", "hotelInfo", "accommodationInfo"):
        if k in raw_day and isinstance(raw_day[k], dict) and "name" in raw_day[k]:
            return raw_day[k]
    return None

def _normalize_day_index(raw_day: dict, fallback: int) -> int:
    """规范化day_index: 支持1-based和0-based"""
    val = raw_day.get("day_index", raw_day.get("dayIndex", raw_day.get("day", raw_day.get("day_number", fallback + 1))))
    try:
        val = int(val)
    except (ValueError, TypeError):
        val = fallback
    if val >= 1:
        val -= 1
    return max(0, val)

def _normalize_hotel(h: dict) -> dict:
    """规范化酒店字段"""
    loc = _normalize_location(h.get("location", h.get("coordinate", h.get("coordinates", h.get("coord")))))
    return {
        "name": h.get("name", ""),
        "address": h.get("address", h.get("addr", "")),
        "location": loc,
        "price_range": str(h.get("price_range", h.get("priceRange", h.get("price", "")))),
        "rating": str(h.get("rating", h.get("rate", ""))),
        "distance": str(h.get("distance", h.get("dist", ""))),
        "type": h.get("type", h.get("category", "")),
        "estimated_cost": int(h.get("estimated_cost", h.get("estimatedCost", h.get("cost", 0))) or 0)
    }


def _normalize_attraction(a: dict) -> dict:
    """规范化景点字段"""
    name = a.get("name") or a.get("spot") or a.get("activity") or a.get("title") or a.get("sight") or ""
    loc = _normalize_location(a.get("location", a.get("coordinate", a.get("coordinates", a.get("coord")))))

    # visit_duration: 可能是整数(分钟)、字符串时间范围("09:00-11:30")、或其他
    dur_raw = a.get("visit_duration", a.get("visitDuration", a.get("duration", a.get("time", 120))))
    dur = _parse_duration(dur_raw)

    return {
        "name": name,
        "address": a.get("address", a.get("addr", a.get("location", ""))),
        "location": loc,
        "visit_duration": dur,
        "description": a.get("description", a.get("desc", a.get("detail", ""))),
        "category": a.get("category", a.get("type", "景点")),
        "ticket_price": int(a.get("ticket_price", a.get("ticketPrice", a.get("price", a.get("cost", 0)))) or 0),
        "rating": a.get("rating", a.get("rate")),
        "image_url": a.get("image_url", a.get("imageUrl"))
    }


def _parse_duration(val) -> int:
    """解析时长: int → 直接返回, '09:00-11:30' → 计算分钟差, 其他 → 默认120"""
    if isinstance(val, int):
        return val
    if isinstance(val, str):
        # 尝试直接转int
        try:
            return int(val)
        except ValueError:
            pass
        # 尝试解析时间范围 "HH:MM-HH:MM"
        import re as _re
        match = _re.match(r'(\d{1,2}):(\d{2})\s*[-–—to]+\s*(\d{1,2}):(\d{2})', val)
        if match:
            h1, m1, h2, m2 = int(match[1]), int(match[2]), int(match[3]), int(match[4])
            mins = (h2 * 60 + m2) - (h1 * 60 + m1)
            return max(mins, 30) if mins > 0 else 120
    return 120


def _normalize_meal(m: dict, default_day: int = 0) -> dict:
    """规范化餐饮字段"""
    # 推断餐类型
    meal_type = m.get("type", m.get("meal_type", m.get("mealType", "")))
    if not meal_type:
        name_lower = m.get("name", "").lower()
        if "早" in name_lower or "breakfast" in name_lower:
            meal_type = "breakfast"
        elif "午" in name_lower or "lunch" in name_lower:
            meal_type = "lunch"
        elif "晚" in name_lower or "dinner" in name_lower:
            meal_type = "dinner"
        else:
            meal_type = "lunch"

    loc = _normalize_location(m.get("location", m.get("coordinate", m.get("coordinates", m.get("coord")))))
    dish_name = m.get("name") or m.get("activity") or m.get("dish") or m.get("food") or ""
    # 推荐菜可能是列表或字符串
    rec_dish = m.get("recommended_dish", m.get("recommendedDish", m.get("specialty", m.get("recommended_dishes", ""))))
    if isinstance(rec_dish, list):
        rec_dish = "、".join(str(d) for d in rec_dish[:3])
    # 费用: avg_cost / price / cost
    cost = m.get("estimated_cost", m.get("estimatedCost", m.get("avg_cost", m.get("cost", m.get("price", 0)))))
    try:
        cost = int(cost) if cost else 0
    except (ValueError, TypeError):
        cost = 0

    # restaurant可能是dict {name, average_cost} 或 string
    rest_raw = m.get("restaurant", m.get("restaurant_name", m.get("restaurantName", "")))
    if isinstance(rest_raw, dict):
        rest_name = rest_raw.get("name", rest_raw.get("restaurant_name", ""))
        # 从dict中提取extra info
        if not m.get("address"):
            m["address"] = rest_raw.get("address", "")
        if not m.get("estimated_cost") and not m.get("avg_cost"):
            m["avg_cost"] = rest_raw.get("average_cost", rest_raw.get("avg_cost", 0))
    else:
        rest_name = str(rest_raw) if rest_raw else ""

    return {
        "type": meal_type,
        "name": dish_name,
        "restaurant": rest_name,
        "address": m.get("address", m.get("addr", m.get("location", ""))),
        "location": loc,
        "description": m.get("description", m.get("desc", "")),
        "recommended_dish": str(rec_dish),
        "estimated_cost": cost
    }


def _parse_alt_json(text: str, city: str, item_type: str) -> list:
    """解析备选项JSON，容错处理单引号等格式问题"""
    import json as _json
    try:
        # 提取JSON数组/对象
        if "[" in text and "]" in text:
            s, e = text.find("["), text.rfind("]") + 1
            raw = text[s:e]
        elif "{" in text:
            s, e = text.find("{"), text.rfind("}") + 1
            raw = text[s:e]
        else:
            raw = ""

        # 尝试标准解析
        try:
            parsed = _json.loads(raw)
        except _json.JSONDecodeError:
            # 修复常见问题: 单引号→双引号
            import re
            fixed = re.sub(r"'([^']*)':", r'"\1":', raw)  # 'key': → "key":
            fixed = re.sub(r":\s*'([^']*)'", r': "\1"', fixed)   # : 'value' → : "value"
            try:
                parsed = _json.loads(fixed)
            except _json.JSONDecodeError:
                # 最后尝试: 逐行提取name字段
                return _extract_alt_by_regex(text, city, item_type)

        # 标准化
        if isinstance(parsed, list):
            items = parsed
        elif isinstance(parsed, dict):
            items = parsed.get("alternatives", parsed.get("items", parsed.get("results", [parsed])))
            if isinstance(items, dict):
                items = [items]
        else:
            items = []

        result = []
        for item in items:
            if isinstance(item, dict):
                loc = _normalize_location(item.get("location", item.get("coordinate", item.get("coord"))))
                result.append({
                    "name": item.get("name", item.get("title", "")),
                    "address": item.get("address", item.get("addr", f"{city}市")),
                    "location": loc,
                    "category": item.get("category", item.get("type", "")),
                    "rating": item.get("rating", item.get("rate")),
                    "reason": item.get("reason", item.get("description", f"位于{city}，与行程路线契合")),
                    "estimated_cost": int(item.get("estimated_cost", item.get("price", item.get("cost", 0))) or 0),
                    "visit_duration": int(item.get("visit_duration", item.get("duration", 120)) or 120) if item_type == "attraction" else None,
                    "recommended_dish": item.get("recommended_dish", item.get("specialty", "")) if item_type == "meal" else None,
                })
        return result[:5] if result else []
    except Exception as e:
        print(f"⚠️ 备选JSON解析失败: {e}")
        return []

def _extract_alt_by_regex(text: str, city: str, item_type: str) -> list:
    """正则兜底提取备选项"""
    import re
    result = []
    # 匹配 "name": "xxx" 或 'name': 'xxx' 或 name: xxx
    names = re.findall(r'''["']?name["']?\s*[:=]\s*["']([^"']+)["']''', text)
    addresses = re.findall(r'''["']?address["']?\s*[:=]\s*["']([^"']+)["']''', text)
    reasons = re.findall(r'''["']?reason["']?\s*[:=]\s*["']([^"']+)["']''', text)
    for i, name in enumerate(names[:5]):
        result.append({
            "name": name,
            "address": addresses[i] if i < len(addresses) else f"{city}市",
            "location": {"longitude": 116.4 + i * 0.01, "latitude": 39.9 + i * 0.01},
            "reason": reasons[i] if i < len(reasons) else f"位于{city}，与行程路线契合",
            "estimated_cost": 0,
            "visit_duration": 120 if item_type == "attraction" else None,
            "recommended_dish": "" if item_type == "meal" else None,
        })
    return result

def _normalize_location(loc) -> dict:
    """规范化坐标: [lng, lat] 或 {lng/lat} 或 {longitude/latitude}"""
    if loc is None:
        return {"longitude": 116.4, "latitude": 39.9}
    if isinstance(loc, (list, tuple)) and len(loc) >= 2:
        return {"longitude": float(loc[0]), "latitude": float(loc[1])}
    if isinstance(loc, dict):
        lng = loc.get("longitude", loc.get("lng", loc.get("lon", loc.get("x", 116.4))))
        lat = loc.get("latitude", loc.get("lat", loc.get("y", 39.9)))
        return {"longitude": float(lng), "latitude": float(lat)}
    return {"longitude": 116.4, "latitude": 39.9}


def _create_fallback_plan(request: TripRequest) -> TripPlan:
    from datetime import datetime, timedelta
    start = datetime.strptime(request.start_date, "%Y-%m-%d")
    days = []
    for i in range(request.travel_days):
        d = start + timedelta(days=i)
        days.append(DayPlan(
            date=d.strftime("%Y-%m-%d"), day_index=i,
            description=f"第{i+1}天行程",
            transportation=request.transportation, accommodation=request.accommodation,
            attractions=[Attraction(
                name=f"{request.city}景点{j+1}", address=f"{request.city}市",
                location=Location(longitude=116.4 + i * 0.01 + j * 0.005, latitude=39.9 + i * 0.01 + j * 0.005),
                visit_duration=120, description=f"{request.city}著名景点"
            ) for j in range(2)],
            meals=[Meal(type=t, name=f"第{i+1}天餐", description="推荐美食") for t in ['breakfast', 'lunch', 'dinner']]
        ))
    return TripPlan(city=request.city, start_date=request.start_date, end_date=request.end_date,
                    days=days, weather_info=[], overall_suggestions=f"欢迎来到{request.city}!")


_langgraph_planner: Optional[LangGraphTripPlanner] = None


def get_langgraph_planner() -> LangGraphTripPlanner:
    global _langgraph_planner
    if _langgraph_planner is None:
        _langgraph_planner = LangGraphTripPlanner()
    return _langgraph_planner
