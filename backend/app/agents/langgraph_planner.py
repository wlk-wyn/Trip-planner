"""
LangGraph 多智能体旅行规划系统
ReAct 架构: Agent ←→ Tools 循环，观察-推理-行动，直到完成任务

Graph (P0 优化后 - 并行搜索):
START ─┬─→ AttractionReAct ─┐
       ├─→ WeatherReAct    ─┼─→ Planner → END
       └─→ HotelReAct      ┘
       每个 ReAct 节点内部: agent ⇄ tools (条件循环)
       三个搜索节点并行执行，全部完成后进入规划节点

P5 优化: 使用 MCPConnectionManager 管理连接池
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
from ..config import get_settings
from ..models.schemas import TripRequest, TripPlan, DayPlan, Attraction, Meal, WeatherInfo, Location, Hotel
from ..models.llm_output import LLMTripPlan, LLMDayPlan, LLMAttraction, LLMMeal, LLMHotel, LLMCoordinates
from ..services.cache_service import cache, CACHE_TTL_WEATHER, CACHE_TTL_ATTRACTION, CACHE_TTL_HOTEL
from ..services.mcp_manager import get_mcp_manager


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


def _extract_attraction_coords(attractions_text: str, max_points: int = 6) -> list:
    """从景点搜索结果文本中提取坐标点列表
    
    返回 [(longitude, latitude), ...]，最多取 max_points 个。
    兼容多种格式：
    - "坐标: 120.148, 30.248"
    - "location: 120.148,30.248"  
    - "longitude: 120.148, latitude: 30.248"
    - JSON: {"location": {"lng": 120.148, "lat": 30.248}}
    - JSON: {"longitude": 120.148, "latitude": 30.248}
    """
    if not attractions_text:
        return []

    coords = []
    text = attractions_text

    # 模式1: longitude/latitude 命名对
    for m in re.finditer(
        r'longitude["\s:]+([0-9.]+)["\s,]+latitude["\s:]+([0-9.]+)',
        text, re.IGNORECASE
    ):
        coords.append((float(m.group(1)), float(m.group(2))))

    # 模式2: location: lng,lat 或 坐标: lng,lat
    if len(coords) < 3:
        for m in re.finditer(
            r'(?:坐标|location|坐标)["\s:]+([0-9.]+)\s*[,，]\s*([0-9.]+)',
            text, re.IGNORECASE
        ):
            coords.append((float(m.group(1)), float(m.group(2))))

    # 模式3: JSON location 嵌套对象
    if len(coords) < 3:
        for m in re.finditer(
            r'"location"\s*:\s*\{[^}]*"lng"\s*:\s*([0-9.]+)[^}]*"lat"\s*:\s*([0-9.]+)',
            text, re.IGNORECASE
        ):
            coords.append((float(m.group(1)), float(m.group(2))))

        for m in re.finditer(
            r'"location"\s*:\s*\{[^}]*"longitude"\s*:\s*([0-9.]+)[^}]*"latitude"\s*:\s*([0-9.]+)',
            text, re.IGNORECASE
        ):
            coords.append((float(m.group(1)), float(m.group(2))))

    # 去重（坐标相近视为同一地点）
    unique = []
    for lng, lat in coords:
        if not any(abs(lng - u_lng) < 0.01 and abs(lat - u_lat) < 0.01 for u_lng, u_lat in unique):
            unique.append((lng, lat))

    return unique[:max_points]


def _compute_center(coords: list) -> tuple:
    """计算多个坐标点的几何中心"""
    if not coords:
        return (0.0, 0.0)
    lngs = [c[0] for c in coords]
    lats = [c[1] for c in coords]
    return (sum(lngs) / len(lngs), sum(lats) / len(lats))


def _guess_city_radius(city: str) -> int:
    """根据城市规模推测合理的酒店搜索半径（米）"""
    large_cities = {"北京", "上海", "广州", "深圳", "成都", "杭州", "武汉", "西安", "南京", "重庆", "天津"}
    if city in large_cities:
        return 8000  # 8km
    return 5000  # 5km


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

**时间安排规则（必须严格遵守）:**
- 早餐: 08:00-09:30
- 上午景点活动: 09:30-12:00（至少1-2个景点）
- 午餐: 12:00-14:00
- 下午景点活动: 14:00-17:30（至少1-2个景点）
- 晚餐: 18:00-20:00
⚠️ 严禁午饭后直接安排晚饭！午饭(12:00-14:00)和晚饭(18:00-20:00)之间必须有下午景点活动！

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
      {"time": "09:30-11:30", "spot": "景点名", "address": "地址", "coordinates": [lng, lat], "duration": 120, "description": "景点描述", "category": "类别", "ticket_price": 60},
      {"time": "14:00-16:00", "spot": "景点名", "address": "地址", "coordinates": [lng, lat], "duration": 120, "description": "景点描述", "category": "类别", "ticket_price": 0},
      {"time": "16:30-17:30", "spot": "景点名", "address": "地址", "coordinates": [lng, lat], "duration": 60, "description": "景点描述", "category": "类别", "ticket_price": 0}
    ],
    "meals": [
      {"type": "breakfast", "restaurant": "餐厅名", "address": "地址", "coordinates": [lng, lat], "recommended_dishes": ["招牌菜1","招牌菜2"], "description": "推荐理由", "estimated_cost": 30, "image_url": "可选，如无则留空"},
      {"type": "lunch", "restaurant": "餐厅名", "address": "地址", "coordinates": [lng, lat], "recommended_dishes": ["招牌菜"], "description": "推荐理由", "estimated_cost": 60, "image_url": "可选，如无则留空"},
      {"type": "dinner", "restaurant": "餐厅名", "address": "地址", "coordinates": [lng, lat], "recommended_dishes": ["招牌菜"], "description": "推荐理由", "estimated_cost": 90, "image_url": "可选，如无则留空"}
    ]
  }]
}
```

**关键要求:**
- 每天至少3个活动：上午1-2个景点 + 下午1-2个景点
- activities数组中的每个活动必须有time字段，且时间必须合理分布在上午和下午
- 每个meal的restaurant必须是具体餐厅名称(字符串),不是对象
- recommended_dishes必须是数组,包含1-3个真实菜名
- 坐标(coordinates/coordinate)用[lng, lat]数组格式
- 每天必须包含完整的早中晚三餐
- 上午活动时间: 09:00-12:00，下午活动时间: 14:00-17:30
- 直接返回JSON,不要包wrapper,不要用驼峰命名"""


# ============ LangGraph ReAct Planner ============

class LangGraphTripPlanner:
    """基于 LangGraph ReAct 的多智能体旅行规划器

    P5优化: 使用 MCPConnectionManager 管理连接池
    """

    def __init__(self):
        settings = get_settings()
        self.llm = ChatOpenAI(
            model=os.getenv("LLM_MODEL_ID", "deepseek-chat"),
            api_key=os.getenv("LLM_API_KEY"),
            base_url=os.getenv("LLM_BASE_URL", "https://api.deepseek.com"),
            temperature=0.7
        )
        # P5: 使用全局 MCP 管理器
        self._mcp_manager = get_mcp_manager()

    async def _ensure_initialized(self):
        """确保 MCP 连接可用 (P5优化: 使用连接管理器)"""
        await self._mcp_manager.ensure_connected()

    def _get_tool(self, name: str):
        """按名称获取MCP工具 (P5优化: 从管理器获取)"""
        return self._mcp_manager.get_tool(name)

    async def _call_tool_direct(self, tool_name: str, arguments: dict) -> str:
        """直接调用MCP工具，返回结果字符串（不走ReAct循环）

        P5优化: 添加请求记录和错误跟踪
        """
        tool = self._get_tool(tool_name)
        if tool is None:
            print(f"   ⚠️ 未找到工具: {tool_name}，回退到ReAct")
            self._mcp_manager.record_request(success=False)
            return ""
        try:
            result = await tool.ainvoke(arguments)
            self._mcp_manager.record_request(success=True)
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
            self._mcp_manager.record_request(success=False)
            return ""

    # ============ ReAct 搜索节点 (内部循环) ============

    def _make_search_subgraph(self, system_prompt: str, result_key: str, tool_names: list = None):
        """
        创建 ReAct 搜索子图: agent ⇄ tools 循环
        使用 ToolNode + tools_condition 实现标准 ReAct 模式

        Args:
            system_prompt: 系统提示词
            result_key: 结果键名
            tool_names: 限制可用工具名列表，None 则使用全部工具
        """
        # 子图状态
        class SubState(TypedDict):
            messages: Annotated[List[BaseMessage], add_messages]

        if tool_names:
            tools = [t for t in self._mcp_manager.get_all_tools() if t.name in tool_names]
            print(f"   [轻量ReAct] 仅加载 {len(tools)} 个工具: {tool_names}")
        else:
            tools = self._mcp_manager.get_all_tools()
        llm_with_tools = self.llm.bind_tools(tools) if tools else self.llm

        async def agent_node(state: SubState) -> dict:
            response = await llm_with_tools.ainvoke(
                [SystemMessage(content=system_prompt)] + state["messages"]
            )
            return {"messages": [response]}

        workflow = StateGraph(SubState)
        workflow.add_node("agent", agent_node)

        if tools:
            workflow.add_node("tools", ToolNode(tools))
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
        """运行 ReAct 子图，提取最终文本结果

        结果提取优先级:
        1. 最后一条不含 tool_calls 的 AIMessage.content（LLM 总结）
        2. 所有 ToolMessage.content 拼接（工具返回的原始数据）
        3. 最后一条消息的 content
        """
        try:
            result = await subgraph.ainvoke(
                {"messages": [HumanMessage(content=initial_message)]},
                {"recursion_limit": 12}
            )
            messages = result.get("messages", [])
            print(f"   [调试] ReAct消息数: {len(messages)}")
            for i, msg in enumerate(messages):
                msg_type = type(msg).__name__
                has_tc = bool(getattr(msg, "tool_calls", None))
                content_preview = str(getattr(msg, "content", ""))[:150]
                print(f"      msg[{i}] {msg_type} tool_calls={has_tc} content={content_preview}")

            # 1. 优先: 最后一条不含 tool_calls 且有 content 的 AIMessage
            for msg in reversed(messages):
                if isinstance(msg, AIMessage) and msg.content and not getattr(msg, "tool_calls", None):
                    print(f"   [调试] 提取AIMessage总结, 长度: {len(msg.content)}")
                    return msg.content

            # 2. 兜底: 拼接所有 ToolMessage 的内容（工具返回的原始数据）
            tool_contents = []
            for msg in messages:
                if isinstance(msg, ToolMessage) and msg.content:
                    content = str(msg.content)
                    if content and content != "[]":
                        tool_contents.append(content)
            if tool_contents:
                combined = "\n".join(tool_contents)
                print(f"   [调试] 无AIMessage总结，使用ToolMessage拼接, 长度: {len(combined)}")
                return combined

            # 3. 最后兜底: 最后一条消息
            if messages:
                last_content = str(getattr(messages[-1], "content", ""))
                print(f"   [调试] 使用最后一条消息, 长度: {len(last_content)}")
                return last_content
            return ""
        except Exception as e:
            import traceback
            print(f"   ❌ ReAct 搜索异常: {e}")
            traceback.print_exc()
            return ""

    # ============ 顶层 Graph Nodes ============

    async def _attraction_node(self, state: PlannerState) -> dict:
        print("📍 [轻量ReAct] 搜索景点...")
        await self._ensure_initialized()

        preferences = state.get("preferences", [])
        keywords = preferences[0] if preferences else "景点"
        city = state["city"]

        # 缓存检查
        ref_mode = state.get("reference_mode", "")
        ref_content = state.get("reference_content", "")
        cache_key = f"attractions:{city}:{','.join(preferences)}:{ref_mode}:{ref_content[:100] if ref_content else ''}"
        cached = cache.get(cache_key)
        if cached:
            print(f"   ✅ 景点缓存命中，跳过搜索")
            return {"attractions_info": cached}

        # 轻量 ReAct: 只给景点相关工具，缩小决策空间
        ATTRACTION_TOOLS = ["maps_text_search", "maps_around_search", "maps_search_detail"]
        query = f"请搜索{city}的{keywords}相关景点。对每个景点记录名称、地址、坐标。"

        # 严格/混合模式: 追加攻略中的具体地名
        if ref_mode in ("strict", "hybrid") and ref_content:
            names = _extract_place_names(ref_content, city)
            if names:
                query += f"\n请额外搜索以下攻略提到的地点: {', '.join(names[:5])}"

        subgraph = self._make_search_subgraph(ATTRACTION_SYSTEM, "attractions_info", ATTRACTION_TOOLS)
        result = await self._run_react_search(subgraph, query)
        print(f"   [调试] ReAct结果长度: {len(result)}, 前300字符: {result[:300]}")

        # 兜底: 轻量 ReAct 失败或结果为空，回退直接 API
        if not result or len(result) < 100:
            print(f"   [兜底] ReAct结果不足，回退直接API调用...")
            tool_output = await self._call_tool_direct("maps_text_search", {
                "keywords": keywords,
                "city": city,
                "citylimit": "true",
            })
            print(f"   [调试] 直接API结果长度: {len(tool_output)}, 前300字符: {tool_output[:300]}")
            if tool_output.strip():
                result = f"{city} {keywords}景点搜索结果:\n{tool_output}"

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
            subgraph = self._make_search_subgraph(WEATHER_SYSTEM, "weather_info", ["maps_weather"])
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
        attractions_info = state.get("attractions_info", "") or ""

        cache_key = f"hotel:{city}:{acc}"
        cached = cache.get(cache_key)
        if cached:
            print(f"   ✅ 酒店缓存命中，跳过搜索")
            return {"hotels_info": cached}

        # P2 优化: 判断是否需要特殊处理
        need_react = _has_hotel_special_requirements(free_text, acc, prefs)

        # 方案A: 从景点结果提取坐标，搜索景点周边酒店
        attraction_coords = _extract_attraction_coords(attractions_info)
        search_mode = "city-wide"
        center_lng, center_lat = 0.0, 0.0
        search_radius = _guess_city_radius(city)

        if attraction_coords:
            center_lng, center_lat = _compute_center(attraction_coords)
            search_mode = "around-attractions"
            print(f"   📍 景点坐标提取成功: {len(attraction_coords)} 个景点")
            print(f"   📍 景点群中心: ({center_lng:.4f}, {center_lat:.4f})")
            print(f"   📍 搜索半径: {search_radius}m")
        else:
            print(f"   ⚠️ 未从景点结果中提取到坐标，回退全城市搜索")

        result = ""

        if not need_react:
            # 无特殊要求: 直接API搜索
            if search_mode == "around-attractions":
                print(f"   [Direct] 景点周边搜索 {acc} 酒店...")
                keywords = acc
                # maps_around_search 以中心点+半径搜索
                location_str = f"{center_lng},{center_lat}"
                tool_output = await self._call_tool_direct(
                    "maps_around_search",
                    {
                        "keywords": keywords,
                        "location": location_str,
                        "radius": search_radius,
                        "types": "宾馆酒店",
                    },
                )
                if tool_output.strip():
                    print(f"   [Direct] 周边搜索成功")
                    result = (
                        f"{city} {acc} 酒店推荐（景点周边{search_radius}m范围）\n"
                        f"中心点坐标: {center_lng:.4f}, {center_lat:.4f}\n"
                        f"搜索结果:\n{tool_output}"
                    )
                else:
                    # 周边搜索无结果，尝试扩大半径或降级
                    print(f"   ⚠️ 周边搜索无结果，尝试扩大半径到 10km...")
                    tool_output = await self._call_tool_direct(
                        "maps_around_search",
                        {
                            "keywords": keywords,
                            "location": location_str,
                            "radius": 10000,
                            "types": "宾馆酒店",
                        },
                    )
                    if tool_output.strip():
                        print(f"   [Direct] 扩大半径搜索成功")
                        result = (
                            f"{city} {acc} 酒店推荐（景点周边10km范围）\n"
                            f"中心点坐标: {center_lng:.4f}, {center_lat:.4f}\n"
                            f"搜索结果:\n{tool_output}"
                        )
                    else:
                        # 最终降级: 全城市搜索
                        print(f"   ⚠️ 扩大半径仍无结果，降级全城市搜索...")
                        tool_output = await self._call_tool_direct(
                            "maps_text_search",
                            {"keywords": f"{city} {acc}", "city": city, "citylimit": True},
                        )
                        if tool_output.strip():
                            result = (
                                f"{city} {acc} 酒店推荐（全城市降级搜索）\n"
                                f"搜索结果:\n{tool_output}"
                            )
            else:
                # 无坐标可用: 全城市搜索
                print(f"   [Direct] 无坐标可用，全城市搜索 {acc} 酒店...")
                keywords = f"{city} {acc}"
                tool_output = await self._call_tool_direct(
                    "maps_text_search",
                    {"keywords": keywords, "city": city, "citylimit": True},
                )
                if tool_output.strip():
                    result = (
                        f"{city} {acc} 酒店推荐（全城市搜索）\n"
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
                                f"{city} {acc} 酒店推荐（扩大关键词: '{alt_kw}'）\n"
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
            if search_mode == "around-attractions":
                query += f"\n优先搜索以下坐标附近的酒店: ({center_lng:.4f}, {center_lat:.4f})，半径{search_radius}米内。"
            if state.get("reference_mode") in ("strict", "hybrid") and state.get("reference_content"):
                query += f"\n攻略参考: {state['reference_content'][:500]}"
            subgraph = self._make_search_subgraph(HOTEL_SYSTEM, "hotels_info", ["maps_text_search", "maps_around_search"])
            result = await self._run_react_search(subgraph, query)

        print(f"   酒店搜索完成 ({len(result)} 字符) [模式: {search_mode}]")
        result = result[:2500]
        cache.set(cache_key, result, CACHE_TTL_HOTEL)
        return {"hotels_info": result}

    async def _planner_node(self, state: PlannerState) -> dict:
        """生成行程计划 (P3: 先JSON后结构化)

        优先生成纯JSON(速度更快)，验证失败时回退到结构化输出。
        使用 asyncio.to_thread 在生成期间发送心跳进度，防止SSE超时。
        """
        print("📋 生成行程计划 (P3 JSON优先)...")

        # 调试: 打印传入LLM的景点/天气/酒店信息预览
        attr_info = state.get('attractions_info', '')
        weather_info = state.get('weather_info', '')
        hotels_info = state.get('hotels_info', '')
        print(f"   [调试] 景点信息长度: {len(attr_info)}, 前200字符: {attr_info[:200]}")
        print(f"   [调试] 天气信息长度: {len(weather_info)}, 前200字符: {weather_info[:200]}")
        print(f"   [调试] 酒店信息长度: {len(hotels_info)}, 前200字符: {hotels_info[:200]}")

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

注意: 
- 酒店是基于景点坐标进行的周边搜索，优先选择与景点群地理就近的酒店
- 每天上午1-2个景点(09:30-12:00)，下午1-2个景点(14:00-17:30)
- 每餐推荐具体餐厅(名称+地址+坐标+招牌菜)
- 严禁午饭后直接安排晚饭，午饭(12:00-14:00)和晚饭(18:00-20:00)之间必须有下午景点活动"""

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

        # P3: 优先生成纯JSON(更快)，结构化输出作为兜底
        import asyncio
        try:
            # 启动心跳任务，在LLM生成期间定期发送进度
            heartbeat_stop = asyncio.Event()

            async def heartbeat():
                while not heartbeat_stop.is_set():
                    await asyncio.sleep(10)
                    print("   💓 LLM生成中...")

            heartbeat_task = asyncio.create_task(heartbeat())

            # 先用普通JSON生成(速度更快)
            response = await self.llm.ainvoke([
                SystemMessage(content=PLANNER_SYSTEM),
                HumanMessage(content=query)
            ])
            heartbeat_stop.set()
            await heartbeat_task
            raw_content = response.content if isinstance(response.content, str) else str(response.content)
            print(f"   ✅ JSON生成完成 ({len(raw_content)} 字符)")

            # 尝试验证是否符合LLMTripPlan结构
            try:
                import json
                raw_data = json.loads(raw_content)
                llm_output = LLMTripPlan(**raw_data)
                trip_plan_json = llm_output.model_dump_json(indent=2)
                print(f"   ✅ JSON结构验证通过: {len(llm_output.days)}天")
                return {"trip_plan_json": trip_plan_json}
            except Exception as parse_err:
                print(f"   ⚠️ JSON解析/验证失败({parse_err})，回退到结构化输出...")
                # 回退: 使用结构化输出
                heartbeat_stop2 = asyncio.Event()
                async def heartbeat2():
                    while not heartbeat_stop2.is_set():
                        await asyncio.sleep(10)
                        print("   💓 结构化输出生成中...")
                heartbeat2_task = asyncio.create_task(heartbeat2())

                structured_llm = self.llm.with_structured_output(LLMTripPlan)
                response2 = await structured_llm.ainvoke([
                    SystemMessage(content=PLANNER_SYSTEM),
                    HumanMessage(content=query)
                ])
                heartbeat_stop2.set()
                await heartbeat2_task
                print(f"   ✅ 结构化输出生成完成: {len(response2.days)}天")
                trip_plan_json = response2.model_dump_json(indent=2)
                return {"trip_plan_json": trip_plan_json}

        except Exception as e:
            print(f"   ❌ 行程生成失败: {e}")
            raise

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
            tools = self._mcp_manager.get_all_tools()
            llm_with_tools = self.llm.bind_tools(tools) if tools else self.llm
            messages = [SystemMessage(content=system), HumanMessage(content=query)]

            # ReAct循环: 搜索直到满意
            max_rounds = 3
            for _ in range(max_rounds):
                response = await llm_with_tools.ainvoke(messages)
                messages.append(response)

                if hasattr(response, 'tool_calls') and response.tool_calls:
                    for tc in response.tool_calls:
                        tool = next((t for t in tools if t.name == tc["name"]), None)
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

        tools = self._mcp_manager.get_all_tools()
        llm_with_tools = self.llm.bind_tools(tools) if tools else self.llm
        for _ in range(3):
            resp = await llm_with_tools.ainvoke(messages)
            messages.append(resp)
            if hasattr(resp, 'tool_calls') and resp.tool_calls:
                for tc in resp.tool_calls:
                    tool = next((t for t in tools if t.name == tc["name"]), None)
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
        """构建主 StateGraph: 景点+天气并行 → 酒店依赖景点 → 规划节点
        
        方案A优化: 酒店搜索依赖景点坐标，使用周边搜索提升酒店匹配度。
        景点和天气仍保持并行以最大化效率。
        
        流程:
        START ─┬─→ search_attractions ─┬─→ search_hotels ─┐
               └─→ search_weather      ─┼─→ generate_plan → END
                                        └─────────────────┘
        """
        workflow = StateGraph(PlannerState)

        workflow.add_node("search_attractions", self._attraction_node)
        workflow.add_node("search_weather", self._weather_node)
        workflow.add_node("search_hotels", self._hotel_node)
        workflow.add_node("generate_plan", self._planner_node)

        # 第一阶段: 景点和天气并行
        workflow.add_edge(START, "search_attractions")
        workflow.add_edge(START, "search_weather")

        # 第二阶段: 酒店依赖景点坐标
        workflow.add_edge("search_attractions", "search_hotels")

        # 第三阶段: 规划节点等待天气+酒店完成
        workflow.add_edge("search_weather", "generate_plan")
        workflow.add_edge("search_hotels", "generate_plan")

        workflow.add_edge("generate_plan", END)

        return workflow.compile()

    # ============ 公共接口 ============

    async def plan_trip_stream(
        self, 
        request: TripRequest, 
        progress_callback=None
    ) -> TripPlan:
        """带进度回调的流式规划方法 (P4: SSE支持)

        使用后台定时器模拟进度更新，同时执行实际规划。
        
        Args:
            request: 旅行请求
            progress_callback: 进度回调函数 async def callback(progress: int, message: str)

        Returns:
            TripPlan 旅行计划
        """
        import asyncio
        
        print(f"\n{'='*60}")
        print(f"🚀 [LangGraph ReAct Stream] {request.city} | {request.travel_days}天")
        print(f"{'='*60}")

        async def emit_progress(percent: int, message: str):
            if progress_callback:
                await progress_callback(percent, message)
            print(f"   📊 [{percent}%] {message}")

        # 进度状态
        progress_state = {"current": 0, "completed": False}
        
        # 后台定时器，定期发送进度
        async def progress_timer():
            steps = [
                (5, "正在初始化..."),
                (15, "正在初始化..."),
                (25, "🔍 正在搜索景点..."),
                (35, "🔍 正在搜索景点..."),
                (45, "🌤️ 正在查询天气..."),
                (55, "🌤️ 正在查询天气..."),
                (65, "🏨 正在搜索酒店..."),
                (75, "🏨 正在搜索酒店..."),
                (85, "📋 正在生成行程计划..."),
                (92, "📋 正在生成行程计划..."),
            ]
            
            for percent, message in steps:
                if progress_state["completed"]:
                    break
                if percent > progress_state["current"]:
                    progress_state["current"] = percent
                    await emit_progress(percent, message)
                await asyncio.sleep(2)

        # 启动进度定时器
        timer_task = asyncio.create_task(progress_timer())

        try:
            # 先发送初始进度
            await emit_progress(5, "正在初始化...")
            
            # 执行实际规划（复用已有的plan_trip_async逻辑）
            trip_plan = await self.plan_trip_async(request)
            
            # 标记完成，停止定时器
            progress_state["completed"] = True
            timer_task.cancel()
            
            # 发送完成进度
            await emit_progress(100, "✅ 规划完成!")
            print("✅ [LangGraph ReAct Stream] 规划完成!")
            
            return trip_plan
            
        except Exception as e:
            progress_state["completed"] = True
            timer_task.cancel()
            print(f"❌ 失败: {e}")
            import traceback
            traceback.print_exc()
            await emit_progress(100, f"规划失败: {str(e)}")
            return _create_fallback_plan(request)

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
            result = await graph.ainvoke(state, {"recursion_limit": 30})
            trip_plan_json = result.get("trip_plan_json", "")
            
            # P3: 优先尝试使用结构化输出转换
            trip_plan = None
            try:
                import json as _json
                raw_data = _json.loads(trip_plan_json)
                # 检查是否符合LLMTripPlan结构
                if "city" in raw_data and "days" in raw_data:
                    llm_output = LLMTripPlan(**raw_data)
                    trip_plan = _convert_llm_to_tripplan(llm_output, request)
                    print("✅ [P3结构化输出] 转换成功!")
                else:
                    print("⚠️ 数据结构不符合LLMTripPlan，使用原解析器")
            except Exception as e:
                print(f"⚠️ 结构化转换失败({e})，使用原解析器")
            
            # 如果结构化转换失败，使用原解析器
            if trip_plan is None:
                trip_plan = _parse_plan_json(trip_plan_json, request)
            
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


# ============ P3: 结构化输出转换函数 ============

def _convert_llm_to_tripplan(llm_output: LLMTripPlan, request: TripRequest) -> TripPlan:
    """将LLM结构化输出转换为系统内部TripPlan模型

    P3优化: 使用with_structured_output后，LLM直接返回符合Schema的数据，
    无需复杂的JSON解析和兜底处理。
    """
    print(f"   📊 转换LLM结构化输出: {len(llm_output.days)}天")

    # 转换酒店
    hotel = None
    if llm_output.hotel:
        hotel = Hotel(
            name=llm_output.hotel.name,
            address=llm_output.hotel.address,
            location=Location(
                longitude=llm_output.hotel.coordinates.longitude,
                latitude=llm_output.hotel.coordinates.latitude
            ),
            price_range=llm_output.hotel.price_range,
            rating=llm_output.hotel.rating,
            type=request.accommodation,
        )

    # 转换每天的行程
    days = []
    for day_data in llm_output.days:
        # 转换景点
        attractions = []
        for attr in day_data.attractions:
            attractions.append(Attraction(
                name=attr.name,
                address=attr.address,
                location=Location(
                    longitude=attr.coordinates.longitude,
                    latitude=attr.coordinates.latitude
                ),
                visit_duration=attr.duration_minutes,
                description=attr.description,
                category=attr.category,
                ticket_price=attr.ticket_price,
                rating=attr.rating,
                time=attr.time,
                time_period=_infer_time_period(attr.time),
            ))

        # 转换餐饮
        meals = []
        for meal in day_data.meals:
            meals.append(Meal(
                type=meal.meal_type,
                name=meal.restaurant,
                restaurant=meal.restaurant,
                address=meal.address,
                location=Location(
                    longitude=meal.coordinates.longitude,
                    latitude=meal.coordinates.latitude
                ),
                description=meal.description,
                recommended_dish=meal.recommended_dishes[0] if meal.recommended_dishes else None,
                estimated_cost=meal.estimated_cost,
                image_url=meal.image_url,
            ))

        # 计算预算
        total_attractions = sum(a.ticket_price for a in attractions)
        total_meals = sum(m.estimated_cost for m in meals)

        days.append(DayPlan(
            date=day_data.date,
            day_index=day_data.day_index,
            description=day_data.description,
            transportation=request.transportation,
            accommodation=request.accommodation,
            hotel=hotel if day_data.day_index == len(llm_output.days) - 1 else None,
            attractions=attractions,
            meals=meals,
        ))

    # 计算总预算
    total_hotel = 0
    if hotel:
        try:
            price_str = hotel.price_range
            import re
            prices = re.findall(r'\d+', price_str)
            if prices:
                total_hotel = int(prices[0]) * len(days)
        except (ValueError, IndexError):
            pass

    total_attractions = sum(
        sum(a.ticket_price for a in day.attractions)
        for day in days
    )
    total_meals = sum(
        sum(m.estimated_cost for m in day.meals)
        for day in days
    )
    total = total_attractions + total_hotel + total_meals

    return TripPlan(
        city=llm_output.city,
        start_date=llm_output.start_date,
        end_date=llm_output.end_date,
        days=days,
        overall_suggestions=llm_output.overall_suggestions,
        budget={
            "total_attractions": total_attractions,
            "total_hotels": total_hotel,
            "total_meals": total_meals,
            "total_transportation": 0,
            "total": total,
        },
    )


def _infer_time_period(time_str: str) -> str:
    """根据时间字符串推断时段(复用已有的逻辑)"""
    if not time_str:
        return ""
    import re
    match = re.match(r'(\d{1,2}):(\d{2})', time_str)
    if match:
        hour = int(match.group(1))
        if hour < 12:
            return "morning"
        elif hour < 18:
            return "afternoon"
        else:
            return "evening"
    return ""


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

        # ---- days 智能别名映射 ----
        # 优先使用 days 字段（如果存在且结构正确）
        days_candidates = ["days", "daily_plan", "daily_plans", "dailyPlans", "daily_schedule", "dayPlans"]
        
        days_found = False
        for alt in days_candidates:
            if alt in data:
                candidate = data[alt]
                # 验证结构：检查是否为有效的天数数组
                if isinstance(candidate, list) and len(candidate) > 0:
                    first_item = candidate[0]
                    if isinstance(first_item, dict):
                        # 检查是否包含天的特征字段
                        day_indicators = {"date", "day", "attractions", "meals", "activities", 
                                        "accommodation", "description", "transportation"}
                        item_keys = set(first_item.keys())
                        # 如果有超过2个天的特征字段，认为这是天的数组
                        if len(item_keys & day_indicators) >= 2:
                            data["days"] = data.pop(alt)
                            days_found = True
                            print(f"   📅 使用 '{alt}' 作为天数数组 ({len(candidate)}天)")
                            break
                print(f"   ⚠️ '{alt}' 存在但结构不是天数数组，跳过")
                # 移除无效的候选字段
                if alt != "days":
                    data.pop(alt)
                    
        # 如果找不到有效的days，尝试从itinerary/schedule中提取
        if not days_found:
            print(f"   ⚠️ 未找到有效天数数组，尝试从行程项中提取...")
            # itinerary可能是行程项数组，需要按天分组
            for src in ("itinerary", "schedule", "activities"):
                if src in data and isinstance(data[src], list) and len(data[src]) > 0:
                    items = data[src]
                    # 检查items是否已经按天分组（嵌套结构）
                    first_item = items[0] if items else {}
                    if isinstance(first_item, dict) and "attractions" in first_item:
                        # 这可能是嵌套结构，直接作为days
                        data["days"] = items
                        print(f"   📅 从 '{src}' 提取天数数组 ({len(items)}天)")
                        days_found = True
                        break
                    else:
                        # 这是扁平结构（行程项列表），需要特殊处理
                        # 创建一个汇总天来包含所有项目
                        print(f"   ⚠️ '{src}' 是扁平行程项列表，需要特殊处理")
                        # 将所有行程项放在第一天
                        all_attrs = []
                        all_meals = []
                        for item in items:
                            if isinstance(item, dict):
                                # 简单归类：有restaurant/food相关的作为meal，其他作为attraction
                                item_text = str(item.get("activity", "")) + str(item.get("name", "")) + str(item.get("type", ""))
                                if any(kw in item_text.lower() for kw in ["restaurant", "food", "meal", "dinner", "lunch", "breakfast", "餐", "食"]):
                                    all_meals.append(item)
                                else:
                                    all_attrs.append(item)
                        
                        # 创建days数组
                        days_list = []
                        for day_idx in range(request.travel_days):
                            day_data = {
                                "date": "",
                                "description": f"第{day_idx+1}天行程",
                                "attractions": all_attrs if day_idx == 0 else [],
                                "meals": all_meals if day_idx == 0 else []
                            }
                            days_list.append(day_data)
                        
                        data["days"] = days_list
                        print(f"   📅 创建{len(days_list)}天行程（从扁平列表）")
                        days_found = True
                        break
                if days_found:
                    break

        # ---- 规范化每个 day ----
        raw_days = data.get("days", [])
        print(f"   📋 原始天数: {len(raw_days)}, 期望天数: {request.travel_days}")
        
        # 如果days数量超过期望数量，可能是LLM错误地将行程项当作天数
        # 需要进行验证和修正
        days_to_process = raw_days
        
        # 检查每个raw_day是否像一个"天"而不是一个"行程项"
        valid_days = []
        invalid_items = []
        for idx, raw_day in enumerate(raw_days):
            if not isinstance(raw_day, dict):
                invalid_items.append(raw_day)
                continue
                
            # 检查是否有天的特征字段
            day_indicators = {"date", "day", "attractions", "meals", "activities", 
                            "accommodation", "description", "transportation", "weather"}
            item_keys = set(raw_day.keys())
            match_count = len(item_keys & day_indicators)
            
            # 如果只有0-1个特征字段，可能不是天而是行程项
            if match_count < 2:
                print(f"   ⚠️ 第{idx}项特征字段过少({match_count}个),可能是行程项而非天数")
                invalid_items.append(raw_day)
            else:
                valid_days.append(raw_day)
        
        # 如果发现无效项，尝试将它们合并到有效天数中
        if invalid_items:
            print(f"   🔄 发现{len(invalid_items)}个无效项，尝试合并到有效天数中...")
            if valid_days:
                # 将无效项中的景点/餐厅信息合并到第一个有效天
                for item in invalid_items:
                    if isinstance(item, dict):
                        first_valid = valid_days[0]
                        # 尝试提取景点和餐饮
                        attrs = _find_item_list(item)
                        meals = _find_meal_list(item)
                        if attrs:
                            existing_attrs = first_valid.get("attractions", [])
                            first_valid["attractions"] = existing_attrs + attrs
                        if meals:
                            existing_meals = first_valid.get("meals", [])
                            first_valid["meals"] = existing_meals + meals
                days_to_process = valid_days
            else:
                # 如果没有有效天，将所有项合并为一个天
                print(f"   ⚠️ 没有有效天数，将所有项合并为第一天")
                all_attrs = []
                all_meals = []
                for item in raw_days:
                    if isinstance(item, dict):
                        attrs = _find_item_list(item)
                        meals = _find_meal_list(item)
                        if attrs:
                            all_attrs.extend(attrs)
                        if meals:
                            all_meals.extend(meals)
                merged_day = {
                    "date": "",
                    "description": "行程汇总",
                    "attractions": all_attrs,
                    "meals": all_meals
                }
                days_to_process = [merged_day]
        
        # 如果天数不足，复制最后一天或创建占位天
        if len(days_to_process) < request.travel_days:
            print(f"   ⚠️ 天数不足: {len(days_to_process)} < {request.travel_days}, 补充天数")
            while len(days_to_process) < request.travel_days:
                if days_to_process:
                    # 复制最后一天作为模板
                    template = dict(days_to_process[-1])
                    template["attractions"] = []
                    template["meals"] = []
                    days_to_process.append(template)
                else:
                    days_to_process.append({"date": "", "description": f"第{len(days_to_process)+1}天"})
        
        # 如果天数过多，截断到期望数量
        if len(days_to_process) > request.travel_days:
            print(f"   ⚠️ 天数过多: {len(days_to_process)} > {request.travel_days}, 截断到{request.travel_days}天")
            # 保留前N天，将多出的天的内容合并到最后一天
            extra_days = days_to_process[request.travel_days:]
            days_to_process = days_to_process[:request.travel_days]
            # 将额外天的景点/餐厅合并到最后一天
            if extra_days and days_to_process:
                last_day = days_to_process[-1]
                for extra in extra_days:
                    if isinstance(extra, dict):
                        attrs = _find_item_list(extra)
                        meals = _find_meal_list(extra)
                        if attrs:
                            existing = last_day.get("attractions", [])
                            last_day["attractions"] = existing + attrs
                        if meals:
                            existing = last_day.get("meals", [])
                            last_day["meals"] = existing + meals
        
        normalized_days = []
        for i, raw_day in enumerate(days_to_process):
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
                "day_index": i,
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

        # 最终验证：确保day_index唯一且连续
        day_indices = [d["day_index"] for d in normalized_days]
        print(f"   ✅ 最终天数: {len(normalized_days)}, day_indices: {day_indices}")
        
        # 如果有重复的day_index，重新分配
        if len(set(day_indices)) != len(normalized_days):
            print(f"   ⚠️ 检测到重复的day_index，重新分配...")
            for i, day in enumerate(normalized_days):
                day["day_index"] = i
            print(f"   ✅ 修正后day_indices: {[d['day_index'] for d in normalized_days]}")

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
    """规范化day_index: 直接用循环索引，不信任LLM返回的day_index
    
    LLM返回的day_index经常不一致(0-based/1-based混用或缺失)，
    而days数组本身已经按顺序排列好，所以直接用循环索引fallback。
    """
    return fallback

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
    dur_raw = a.get("visit_duration", a.get("visitDuration", a.get("duration", 120)))
    dur = _parse_duration(dur_raw)

    # 提取活动时间段
    time_str = a.get("time", a.get("time_range", a.get("schedule", "")))
    
    # 推断时段: 根据时间字符串判断是上午还是下午
    time_period = a.get("time_period", "")
    if not time_period and time_str:
        time_period = _infer_time_period(time_str)

    return {
        "name": name,
        "address": a.get("address", a.get("addr", a.get("location", ""))),
        "location": loc,
        "visit_duration": dur,
        "description": a.get("description", a.get("desc", a.get("detail", ""))),
        "category": a.get("category", a.get("type", "景点")),
        "ticket_price": int(a.get("ticket_price", a.get("ticketPrice", a.get("price", a.get("cost", 0))) or 0)),
        "rating": a.get("rating", a.get("rate")),
        "image_url": a.get("image_url", a.get("imageUrl")),
        "time": time_str,
        "time_period": time_period
    }


def _infer_time_period(time_str: str) -> str:
    """根据时间字符串推断时段
    
    上午: 9:00-12:00
    下午: 12:00-18:00
    晚上: 18:00之后
    """
    if not time_str:
        return ""
    
    import re
    # 匹配 "HH:MM" 或 "HH:MM-HH:MM"
    match = re.match(r'(\d{1,2}):(\d{2})', time_str)
    if match:
        hour = int(match.group(1))
        if hour < 12:
            return "morning"
        elif hour < 18:
            return "afternoon"
        else:
            return "evening"
    
    return ""


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
