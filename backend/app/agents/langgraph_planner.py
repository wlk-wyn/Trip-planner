"""
LangGraph 多智能体旅行规划系统
ReAct 架构: Agent ←→ Tools 循环，观察-推理-行动，直到完成任务
Graph: START → AttractionReAct → WeatherReAct → HotelReAct → Planner → END
       每个 ReAct 节点内部: agent ⇄ tools (条件循环)
"""

import os
import asyncio
import json
import math
import re
import shutil
import sys
from pathlib import Path
from datetime import date, timedelta
from typing import TypedDict, List, Optional, Annotated, Literal
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition
from langgraph.checkpoint.memory import MemorySaver
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage, AIMessage, BaseMessage, ToolMessage
from langchain_mcp_adapters.client import MultiServerMCPClient
from ..config import get_settings
from ..models.schemas import TripRequest, TripPlan, Location, Meal
from ..services.place_facts import apply_provider_facts, AMAP_MEAL_SOURCE, AMAP_HOTEL_SOURCE
from ..services.restaurant_candidates import automatic_restaurant, suitable_restaurant
from ..services.meal_service import meal_service
from ..services.amap_rest import get_forecast, search_pois, search_nearby_restaurants, hotel_candidates


# ============ State ============

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
    # 中间结果
    attractions_info: str
    weather_info: str
    hotels_info: str
    restaurants_info: str
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
    "accommodation": {"name": "候选酒店名", "address": "候选地址", "coordinate": [lng, lat], "price_range": ""},
    "activities": [
      {"time": "09:00-11:30", "spot": "景点名", "address": "地址", "coordinates": [lng, lat], "duration": 150, "description": "景点描述", "category": "类别", "ticket_price": 0},
      {"time": "14:00-16:00", "spot": "景点名", "address": "地址", "coordinates": [lng, lat], "duration": 120, "description": "景点描述", "category": "类别", "ticket_price": 0}
    ],
    "meals": [
      {"type": "breakfast", "restaurant": "餐厅名", "address": "地址", "coordinates": [lng, lat], "recommended_dishes": ["招牌菜1","招牌菜2"], "description": "推荐理由", "avg_cost": 0},
      {"type": "lunch", "restaurant": "餐厅名", "address": "地址", "coordinates": [lng, lat], "recommended_dishes": ["招牌菜"], "description": "推荐理由", "avg_cost": 0},
      {"type": "dinner", "restaurant": "餐厅名", "address": "地址", "coordinates": [lng, lat], "recommended_dishes": ["招牌菜"], "description": "推荐理由", "avg_cost": 0}
    ]
  }]
}
```

**关键要求:**
- 景点、餐厅、酒店只能从提供的高德地点候选中选择，名称、地址和坐标必须照候选值填写；不能凭记忆补造具体地址或坐标
- 未提供的价格、评分和营业信息保持空值或0，不要编造；没有对应日期预报时不要填写天气
- 每个meal的restaurant必须是具体餐厅名称(字符串),不是对象
- recommended_dishes必须是数组,包含1-3个真实菜名
- 坐标(coordinates/coordinate)用[lng, lat]数组格式
- 尽量安排早中晚三餐；餐厅候选不足时宁可留空，也不能编造地点
- 直接返回JSON,不要包wrapper,不要用驼峰命名"""


# ============ LangGraph ReAct Planner ============

def build_chat_model(model: str) -> ChatOpenAI:
    """Bounded timeout/retries so a stuck provider fails visibly instead of hanging the run."""
    return ChatOpenAI(
        model=model,
        api_key=os.getenv("LLM_API_KEY"),
        base_url=os.getenv("LLM_BASE_URL", "https://api.deepseek.com"),
        temperature=float(os.getenv("LLM_TEMPERATURE", "0.3")),
        timeout=float(os.getenv("LLM_TIMEOUT", "90")),
        max_retries=int(os.getenv("LLM_MAX_RETRIES", "1")),
        stream_usage=True,
    )


class LangGraphTripPlanner:
    """基于 LangGraph ReAct 的多智能体旅行规划器"""

    def __init__(self):
        settings = get_settings()
        self.llm = build_chat_model(os.getenv("LLM_MODEL_ID", "deepseek-chat"))
        # Light tasks (intake, reference extraction, recommendation ranking) may use a faster model.
        fast_model = os.getenv("LLM_FAST_MODEL_ID", "").strip()
        self.fast_llm = build_chat_model(fast_model) if fast_model else self.llm
        self.mcp_client = None
        self.tools = []
        self._initialized = False
        self._init_lock = asyncio.Lock()

    async def _ensure_initialized(self):
        if self._initialized:
            return
        async with self._init_lock:
            if self._initialized:
                return
            settings = get_settings()
            try:
                local_uvx = Path(sys.executable).with_name("uvx.exe" if os.name == "nt" else "uvx")
                uvx_command = str(local_uvx) if local_uvx.is_file() else shutil.which("uvx") or "uvx"
                self.mcp_client = MultiServerMCPClient({
                    "amap": {
                        "command": uvx_command,
                        "args": ["amap-mcp-server"],
                        "env": {"AMAP_MAPS_API_KEY": settings.amap_api_key},
                        "transport": "stdio"
                    }
                })
                self.tools = await self.mcp_client.get_tools()
                self._initialized = True
                print(f"✅ LangGraph MCP 工具: {len(self.tools)} 个")
            except Exception as e:
                print(f"⚠️ MCP 加载失败: {e}")
                self.tools = []
                self._initialized = True

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

        async def agent_node(state: SubState) -> dict:
            response = await llm_with_tools.ainvoke(
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
        print("📍 [高德] 搜索景点...")
        preferences = state.get("preferences", [])
        city = state["city"]
        keywords = ["景点"]
        if "历史文化" in preferences:
            keywords.append("博物馆")
        elif "自然风光" in preferences:
            keywords.append("风景名胜")

        if state.get("reference_mode") in ("strict", "hybrid"):
            keywords.extend(_extract_place_names(state.get("reference_content", ""), city)[:2])

        searches = await asyncio.gather(
            *(search_pois(city, keyword, 8) for keyword in keywords),
            return_exceptions=True,
        )
        candidates = []
        seen = set()
        for result in searches:
            if isinstance(result, Exception):
                print(f"   一项景点查询失败: {result}")
                continue
            for poi in result:
                if poi["name"] not in seen:
                    seen.add(poi["name"])
                    candidates.append(poi)
        if not candidates:
            raise RuntimeError("未能获得真实景点信息，请稍后重试")
        if state.get("reference_mode") != "strict":
            from ..services.attraction_candidates import select_candidates
            candidates, _ = select_candidates(candidates, keyword=state.get("reference_content") or "")
        print(f"   景点候选: {len(candidates)} 个")
        return {"attractions_info": json.dumps(candidates[:14], ensure_ascii=False)}

    async def _weather_node(self, state: PlannerState) -> dict:
        start = date.fromisoformat(state["start_date"])
        end = date.fromisoformat(state["end_date"])
        if start > date.today() + timedelta(days=3):
            return {"weather_info": "出行日期尚无可靠的短期天气预报，请勿编造天气。"}
        try:
            casts = await get_forecast(state["city"])
            matching = [cast for cast in casts if start <= date.fromisoformat(cast["date"]) <= end]
            return {"weather_info": json.dumps(matching, ensure_ascii=False) if matching else "所选日期暂无天气预报。"}
        except (RuntimeError, ValueError) as exc:
            print(f"   天气查询失败: {exc}")
            return {"weather_info": "天气服务暂不可用，请勿编造天气。"}

    async def _hotel_node(self, state: PlannerState) -> dict:
        print("🏨 [高德] 搜索酒店...")
        acc = state["accommodation"]
        keyword = "经济型酒店" if "经济" in acc else "豪华酒店" if "豪华" in acc else "酒店"
        try:
            candidates = await search_pois(state["city"], keyword, 8)
        except RuntimeError as exc:
            print(f"   酒店查询失败: {exc}")
            candidates = []
        return {"hotels_info": json.dumps(candidates, ensure_ascii=False)}

    async def _restaurant_node(self, state: PlannerState) -> dict:
        print("🍽️ [高德] 搜索餐厅...")
        searches = await asyncio.gather(
            search_pois(state["city"], "早餐", 6),
            search_pois(state["city"], "美食", 10),
            return_exceptions=True,
        )
        candidates = []
        seen = set()
        for result in searches:
            if isinstance(result, Exception):
                print(f"   一项餐厅查询失败: {result}")
                continue
            for poi in result:
                if poi["name"] not in seen:
                    seen.add(poi["name"])
                    candidates.append(poi)
        return {"restaurants_info": json.dumps(candidates[:14], ensure_ascii=False)}

    async def _planner_node(self, state: PlannerState) -> dict:
        print("📋 生成行程计划...")

        is_strict = state.get("reference_mode") == "strict"
        is_hybrid = state.get("reference_mode") == "hybrid"

        query = f"""请根据以下信息生成{state['city']}的{state['travel_days']}天旅行计划:

**基本信息:**
- 城市: {state['city']} | 日期: {state['start_date']} ~ {state['end_date']} | {state['travel_days']}天
- 交通: {state['transportation']} | 住宿: {state['accommodation']}
- 偏好: {', '.join(state.get('preferences', []))}

**高德景点候选:** {state.get('attractions_info', '')[:4000]}
**天气:** {state.get('weather_info', '')[:1500]}
**高德酒店候选:** {state.get('hotels_info', '')[:2200]}
**高德餐厅候选:** {state.get('restaurants_info', '')[:3300]}

每天2-3个景点，地理就近排列。只能从候选中选择地点并复制地址及坐标；价格未知则填0或空字符串。若餐厅候选不足，可以少推荐餐饮，不能编造店铺。返回完整JSON。"""
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
                                      current_name: str, current_category: str = "", context: str = "",
                                      current_location: Optional[dict] = None) -> list:
        """DIY 备选直接来自高德，不再等待 MCP/LLM 或返回臆造坐标。"""
        keyword = current_category or "景点" if replace_type == "attraction" else "酒店" if replace_type == "hotel" else "餐厅"
        try:
            anchor = Location(**current_location) if current_location else None
        except (TypeError, ValueError):
            anchor = None
        try:
            if replace_type in {"meal", "hotel"} and anchor:
                pois = await search_nearby_restaurants(anchor.longitude, anchor.latitude, category="100100" if replace_type == "hotel" else "050100")
            else:
                pois = await search_pois(city, keyword, 20)
                if not pois and replace_type == "attraction" and keyword != "景点":
                    pois = await search_pois(city, "景点", 20)
        except RuntimeError as exc:
            print(f"   备选查询失败: {exc}")
            return []
        if replace_type == "hotel":
            pois = hotel_candidates(pois)
        pois = [poi for poi in pois if poi["name"] != current_name and poi["address"]
                and poi["name"] not in context]
        if anchor:
            pois.sort(key=lambda poi: _distance_km(anchor, _poi_location(poi)))
            nearby = [poi for poi in pois if _distance_km(anchor, _poi_location(poi)) <= 5]
            if nearby:
                pois = nearby
        if replace_type == "attraction":
            from ..services.attraction_candidates import select_candidates
            pois, _ = select_candidates(pois)
        return [{**_poi_as_alternative(poi, anchor), "poi_id": poi["poi_id"]} for poi in pois[:5]]

    async def search_poi_async(self, keyword: str, city: str) -> list:
        """DIY 搜索直接返回真实地点。"""
        return [_poi_as_alternative(poi) for poi in await search_pois(city, keyword, 15)]

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
        """构建主 StateGraph: 并行查询真实地点，再生成行程。"""
        workflow = StateGraph(PlannerState)

        workflow.add_node("search_attractions", self._attraction_node)
        workflow.add_node("search_weather", self._weather_node)
        workflow.add_node("search_hotels", self._hotel_node)
        workflow.add_node("search_restaurants", self._restaurant_node)
        workflow.add_node("generate_plan", self._planner_node)

        # 各项资料彼此独立，并行搜索后一起进入规划节点
        workflow.add_edge(START, "search_attractions")
        workflow.add_edge(START, "search_weather")
        workflow.add_edge(START, "search_hotels")
        workflow.add_edge(START, "search_restaurants")
        workflow.add_edge(["search_attractions", "search_weather", "search_hotels", "search_restaurants"], "generate_plan")
        workflow.add_edge("generate_plan", END)

        return workflow.compile()

    # ============ 公共接口 ============

    async def plan_trip_async(self, request: TripRequest) -> TripPlan:
        print(f"\n{'='*60}")
        print(f"🚀 [LangGraph ReAct] {request.city} | {request.travel_days}天")
        print(f"{'='*60}")

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
            "attractions_info": "", "weather_info": "", "hotels_info": "", "restaurants_info": "",
            "attraction_messages": [], "weather_messages": [], "hotel_messages": [],
            "trip_plan_json": "", "error": "",
        }

        try:
            result = await graph.ainvoke(state)
            trip_plan = _parse_plan_json(result.get("trip_plan_json", ""), request)
            self._ground_selected_places(trip_plan, result)
            from ..services.meal_windows import protected_meal_ids
            await self._align_meals_to_route(trip_plan, protected_ids=protected_meal_ids(trip_plan, request), request=request)
            print("✅ [LangGraph ReAct] 规划完成!")
            return trip_plan
        except Exception as e:
            print(f"❌ 失败: {e}")
            import traceback
            traceback.print_exc()
            raise RuntimeError("规划服务暂时不可用，请稍后重试") from e

    def _ground_selected_places(self, trip: TripPlan, state: PlannerState) -> None:
        """用搜索结果覆盖模型写出的地点数据，拒绝未经查询的具体地点。"""
        from ..services.map_queries import grounded_weather
        trip.weather_info = grounded_weather(state,trip.start_date,trip.end_date)
        def index(key: str) -> dict:
            grouped = {}
            for poi in json.loads(state.get(key) or "[]"):
                if poi.get("name"):
                    grouped.setdefault(poi["name"], []).append(poi)
            return grouped

        def select(grouped, name, location=None, poi_id=""):
            candidates = grouped.get(name, [])
            if not candidates:
                return None
            if poi_id:
                selected = next((p for p in candidates if p.get("poi_id") == poi_id), None)
                if selected:
                    return selected
                raise ValueError(f"地点 ID 与候选不匹配：{name}")
            if location:
                return min(candidates, key=lambda p: _distance_km(location, _poi_location(p)))
            return candidates[0]

        attractions = index("attractions_info")
        hotels = index("hotels_info")
        restaurants = index("restaurants_info")

        for day in trip.days:
            grounded_attractions = []
            for attraction in day.attractions:
                poi = select(attractions, attraction.name, attraction.location, attraction.poi_id)
                if not poi:
                    print(f"   跳过未核实景点: {attraction.name}")
                    continue
                attraction.poi_id = poi.get("poi_id", "")
                attraction.identify_place()
                attraction.address = poi["address"]
                attraction.location = _poi_location(poi)
                attraction.ticket_price = None
                attraction.cost_status = "unknown"
                attraction.cost_source = ""
                attraction.open_hours, attraction.hours_evidence, attraction.price_evidence = None, None, None
                attraction.rating = None
                attraction.category = poi.get("category") or attraction.category
                apply_provider_facts(attraction, poi)
                grounded_attractions.append(attraction)
            if not grounded_attractions:
                raise ValueError("当天没有可核实的景点")
            day.attractions = grounded_attractions
            day.description = " → ".join(item.name for item in grounded_attractions)

            if day.hotel:
                poi = select(hotels, day.hotel.name, day.hotel.location, day.hotel.poi_id)
                if poi:
                    day.hotel.poi_id = poi.get("poi_id", "")
                    day.hotel.identify_place()
                    day.hotel.address = poi["address"]
                    day.hotel.location = _poi_location(poi)
                    day.hotel.price_range = ""
                    day.hotel.estimated_cost = None
                    day.hotel.cost_status = "unknown"
                    day.hotel.cost_source = ""
                    day.hotel.open_hours, day.hotel.hours_evidence, day.hotel.price_evidence = None, None, None
                    day.hotel.rating = ""
                    day.hotel.distance = ""
                    apply_provider_facts(day.hotel, poi)
                else:
                    print(f"   跳过未核实酒店: {day.hotel.name}")
                    day.hotel = None
                    day.accommodation = "住宿待选择"

            for meal in day.meals:
                poi = select(restaurants, meal.restaurant or meal.name, meal.location, meal.poi_id)
                if poi:
                    meal.poi_id = poi.get("poi_id", "")
                    meal.identify_place()
                    meal.restaurant = poi["name"]
                    meal.address = poi["address"]
                    meal.location = _poi_location(poi)
                    meal.estimated_cost = None
                    meal.cost_status = "unknown"
                    meal.cost_source = ""
                    meal.open_hours, meal.hours_evidence, meal.price_evidence = None, None, None
                    apply_provider_facts(meal, poi)
                else:
                    meal.location = None

    @staticmethod
    def _place_nearby_meals(pending, results, used_restaurants, limit_km, *, request=None):
        """Add the best nearby restaurant per slot; return the slots still unfilled."""
        unfilled = []
        for (day, meal_type, anchor), result in zip(pending, results):
            if isinstance(result, Exception):
                print(f"   第{day.day_index + 1}天{meal_type}附近餐厅查询失败: {result}")
                unfilled.append((day, meal_type, anchor))
                continue
            nearby = [poi for poi in result if poi["name"] and poi["name"] not in used_restaurants
                      and automatic_restaurant(poi, meal_type, visit_parent_ids={a.poi_id for a in day.attractions if a.poi_id})
                      and _distance_km(anchor, _poi_location(poi)) <= limit_km]
            assessed = [(poi, meal_service(poi,day,meal_type,request)) for poi in nearby]
            excluded = sum(state == 'excluded' for _,state in assessed)
            if excluded:
                from ..services.observability import record_event
                record_event('meal_service_exclusions', excluded)
            usable = [(poi,state) for poi,state in assessed if state != 'excluded']
            if not usable:
                unfilled.append((day, meal_type, anchor))
                continue
            # A modest uncertainty penalty cannot force a long detour merely
            # to obtain parseable hours. Actual arrival still needs validation.
            poi, _ = min(usable, key=lambda row: _restaurant_score(row[0], anchor, meal_type)
                         + (.25 if row[1] == 'unknown' else 0))
            used_restaurants.add(poi["name"])
            distance = round(_distance_km(anchor, _poi_location(poi)) * 1000)
            meal = Meal(
                type=meal_type, name="", restaurant=poi["name"], address=poi["address"], poi_id=poi.get("poi_id", ""),
                route_matched=True,
                location=_poi_location(poi),
                description=f"距当日游览点直线约{distance}米；菜品与营业时间请提前核实。",
                recommended_dish="", estimated_cost=None,
            )
            apply_provider_facts(meal, poi)
            day.meals.append(meal)
        return unfilled

    async def _align_meals_to_route(self, trip: TripPlan, repair=False, protected_ids=None, *, request=None) -> None:
        """只保留行程附近的餐厅，并用附近真实 POI 补足三餐。"""
        if request is not None:
            from ..services.meal_windows import protected_meal_ids
            protected_ids = set(protected_ids or ()) | protected_meal_ids(trip, request)
        pending = []
        original_meals = {(day.date, meal.type): meal for day in trip.days for meal in day.meals} if repair else {}
        used_restaurants = set()
        for day in trip.days:
            if not day.attractions:
                continue
            by_type = {meal.type: meal for meal in day.meals if meal.type in ("breakfast", "lunch", "dinner")}
            day.meals = [meal for meal in day.meals if meal.type == "snack"]
            anchors = {
                "breakfast": day.hotel.location if day.hotel and day.hotel.location else day.attractions[0].location,
                # Validator inserts lunch AFTER ceil(n/2) visits. Use that last
                # visited point rather than searching ahead at the next attraction.
                "lunch": day.attractions[(len(day.attractions) - 1) // 2].location,
                "dinner": day.attractions[-1].location,
            }
            for meal_type, anchor in anchors.items():
                current = by_type.get(meal_type)
                if current and (current.locked or current.cost_status == 'confirmed' or current.item_id in (protected_ids or set())):
                    day.meals.append(current)
                    used_restaurants.add(current.restaurant or current.name)
                    continue
                if repair and (not current or not current.route_matched or current.locked or current.item_id in (protected_ids or set())):
                    if current:
                        day.meals.append(current)
                        used_restaurants.add(current.restaurant or current.name)
                    continue
                if (current and current.location
                        and automatic_restaurant({'name':current.restaurant or current.name, 'category':current.category, 'open_hours':current.open_hours, 'parent_poi_id':current.parent_poi_id, 'coordinates':[current.location.longitude,current.location.latitude]}, meal_type, visit_parent_ids={a.poi_id for a in day.attractions if a.poi_id})
                        and meal_service(current,day,meal_type,request) != 'excluded'
                        and _distance_km(anchor, current.location) <= 2.5
                        and (current.restaurant or current.name) not in used_restaurants):
                    day.meals.append(current)
                    used_restaurants.add(current.restaurant or current.name)
                else:
                    pending.append((day, meal_type, anchor))

        if not pending:
            return

        semaphore = asyncio.Semaphore(2)

        async def find_nearby(meal_type: str, anchor: Location, radius: int = 1800) -> list:
            async with semaphore:
                for attempt in range(2):
                    try:
                        return await search_nearby_restaurants(
                            anchor.longitude, anchor.latitude,
                            "早餐" if meal_type == "breakfast" else "",
                            radius=radius,
                            category="050000" if meal_type == "breakfast" else "050100",
                        )
                    except RuntimeError:
                        if attempt:
                            raise
                        await asyncio.sleep(0.35)
            return []

        # A wider second pass only for slots the close search could not fill (e.g. scenic areas).
        for radius, limit_km in ((1800, 2.0), (3500, 3.5)):
            if not pending:
                break
            results = await asyncio.gather(*(
                find_nearby(meal_type, anchor, radius) for _, meal_type, anchor in pending
            ), return_exceptions=True)
            pending = self._place_nearby_meals(pending, results, used_restaurants, limit_km, request=request)

        if repair:
            for day, meal_type, _anchor in pending:
                original = original_meals.get((day.date, meal_type))
                if original and not any(meal.type == meal_type for meal in day.meals):
                    day.meals.append(original)
        order = {"breakfast": 0, "lunch": 1, "dinner": 2}
        for day in trip.days:
            day.meals.sort(key=lambda meal: order.get(meal.type, 3))

        # 模型写概览时尚未执行附近餐厅校正，移除可能与最终结果矛盾的餐饮信息。
        sentences = re.split(r"(?<=。)", trip.overall_suggestions)
        meal_claims = ("餐厅候选", "餐次未", "自行觅食", "未匹配到", "早餐店多集中")
        trip.overall_suggestions = "".join(
            sentence for sentence in sentences if not any(claim in sentence for claim in meal_claims)
        ).strip()
        if all(len(day.meals) == 3 for day in trip.days):
            trip.overall_suggestions += " 每天三餐已按当天行程附近的真实地点匹配，营业时间请出行前核实。"
        else:
            trip.overall_suggestions += " 部分餐次没有找到合适的附近餐厅，可在 DIY 模式中补充。"


# ============ 辅助函数 ============

def _poi_location(poi: dict) -> Location:
    longitude, latitude = poi["coordinates"]
    return Location(longitude=longitude, latitude=latitude)


def _poi_as_alternative(poi: dict, anchor: Optional[Location] = None) -> dict:
    distance = _distance_km(anchor, _poi_location(poi)) if anchor else None
    return {
        "name": poi["name"],
        "address": poi["address"],
        "location": _poi_location(poi).model_dump(),
        "category": poi.get("category", ""),
        "reason": (f"距原地点直线约{distance:.1f}公里；开放时间与消费请核实。"
                   if distance is not None else "高德地图地点候选；开放时间与消费请核实。"),
        # Restaurant average / hotel lowest price from Amap when published; otherwise unknown.
        "estimated_cost": poi.get("avg_cost") or poi.get("hotel_price"),
        "cost_source": AMAP_MEAL_SOURCE if poi.get("avg_cost") else AMAP_HOTEL_SOURCE if poi.get("hotel_price") else "",
        "open_hours": poi.get("open_hours"),
        "rating": poi.get("rating"),
    }


def _distance_km(first: Location, second: Location) -> float:
    lat1, lat2 = math.radians(first.latitude), math.radians(second.latitude)
    dlat = lat2 - lat1
    dlng = math.radians(second.longitude - first.longitude)
    value = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlng / 2) ** 2
    return 12742 * math.asin(min(1, math.sqrt(value)))


def _suitable_restaurant(name: str, meal_type: str) -> bool:
    return suitable_restaurant(name, meal_type)


def _restaurant_score(poi: dict, anchor: Location, meal_type: str) -> float:
    distance = _distance_km(anchor, _poi_location(poi))
    name = poi["name"]
    if meal_type == "breakfast":
        if any(word in name for word in ("早餐", "豆浆", "生煎", "烧麦", "面馆", "五芳斋")):
            distance -= 0.35
    elif any(word in name for word in ("杭帮菜", "浙菜", "家常菜", "饭店", "酒楼", "餐厅")):
        distance -= 0.45
    return distance

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

            attractions = [_normalize_attraction(a) for a in found_attrs if isinstance(a, dict)]
            meals = [_normalize_meal(m, i) for m in found_meals if isinstance(m, dict)]
            meals = [meal for meal in meals if meal["restaurant"].strip() or meal["name"].strip()]
            description = raw_day.get("description", raw_day.get("summary", raw_day.get("desc", raw_day.get("overview", ""))))
            if not description:
                description = " → ".join(attraction["name"] for attraction in attractions if attraction["name"])

            day = {
                "date": raw_day.get("date", raw_day.get("day_date", "")),
                # 列表顺序是最终行程顺序，避免把模型的 0-based/1-based 序号重复换算。
                "day_index": i,
                "description": description,
                "transportation": raw_day.get("transportation", raw_day.get("transport", request.transportation)),
                "accommodation": acc_str,
                "route_notes": raw_day.get("route_notes", raw_day.get("routeNotes", raw_day.get("route_note", ""))),
                "hotel": _normalize_hotel(found_hotel) if found_hotel else None,
                "attractions": attractions,
                "meals": meals,
            }

            normalized_days.append(day)

        data["days"] = normalized_days

        trip = TripPlan(**data)
        if len(trip.days) != request.travel_days or any(not day.attractions for day in trip.days):
            raise ValueError("生成的行程天数或景点信息不完整")
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
        raise ValueError("生成的行程格式不完整，请重试") from e


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

def _normalize_hotel(h: dict) -> dict:
    """规范化酒店字段"""
    loc = _normalize_location(h.get("location", h.get("coordinate", h.get("coordinates", h.get("coord")))))
    return {
        "poi_id": h.get("poi_id", ""),
        "item_id": h.get("item_id", ""),
        "locked": h.get("locked", False),
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
        "poi_id": a.get("poi_id", ""),
        "item_id": a.get("item_id", ""),
        "locked": a.get("locked", False),
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
        "poi_id": m.get("poi_id", ""),
        "item_id": m.get("item_id", ""),
        "locked": m.get("locked", False),
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
            "location": {"longitude": 0.0, "latitude": 0.0},
            "reason": reasons[i] if i < len(reasons) else f"位于{city}，与行程路线契合",
            "estimated_cost": 0,
            "visit_duration": 120 if item_type == "attraction" else None,
            "recommended_dish": "" if item_type == "meal" else None,
        })
    return result

def _normalize_location(loc) -> dict:
    """规范化坐标: [lng, lat] 或 {lng/lat} 或 {longitude/latitude}"""
    try:
        if isinstance(loc, (list, tuple)) and len(loc) >= 2:
            return {"longitude": float(loc[0]), "latitude": float(loc[1])}
        if isinstance(loc, dict):
            lng = loc.get("longitude", loc.get("lng", loc.get("lon", loc.get("x"))))
            lat = loc.get("latitude", loc.get("lat", loc.get("y")))
            if lng is not None and lat is not None:
                return {"longitude": float(lng), "latitude": float(lat)}
    except (TypeError, ValueError):
        pass
    return {"longitude": 0.0, "latitude": 0.0}


_langgraph_planner: Optional[LangGraphTripPlanner] = None


def get_langgraph_planner() -> LangGraphTripPlanner:
    global _langgraph_planner
    if _langgraph_planner is None:
        _langgraph_planner = LangGraphTripPlanner()
    return _langgraph_planner
