"""多智能体旅行规划系统"""

import json
from typing import Dict, Any, List
from hello_agents import SimpleAgent
from hello_agents.tools import MCPTool
from ..services.llm_service import get_llm
from ..models.schemas import TripRequest, TripPlan, DayPlan, Attraction, Meal, WeatherInfo, Location, Hotel
from ..config import get_settings

# ============ Agent提示词 ============

ATTRACTION_AGENT_PROMPT = """你是景点搜索专家。你的任务是根据城市和用户偏好搜索合适的景点。

**重要提示:**
你必须使用工具来搜索景点!不要自己编造景点信息!

**工具调用格式:**
使用maps_text_search工具时,必须严格按照以下格式:
`[TOOL_CALL:amap_maps_text_search:keywords=景点关键词,city=城市名]`

**示例:**
用户: "搜索北京的历史文化景点"
你的回复: [TOOL_CALL:amap_maps_text_search:keywords=历史文化,city=北京]

用户: "搜索上海的公园"
你的回复: [TOOL_CALL:amap_maps_text_search:keywords=公园,city=上海]

**注意:**
1. 必须使用工具,不要直接回答
2. 格式必须完全正确,包括方括号和冒号
3. 参数用逗号分隔
"""

WEATHER_AGENT_PROMPT = """你是天气查询专家。你的任务是查询指定城市的天气信息。

**重要提示:**
你必须使用工具来查询天气!不要自己编造天气信息!

**工具调用格式:**
使用maps_weather工具时,必须严格按照以下格式:
`[TOOL_CALL:amap_maps_weather:city=城市名]`

**示例:**
用户: "查询北京天气"
你的回复: [TOOL_CALL:amap_maps_weather:city=北京]

用户: "上海的天气怎么样"
你的回复: [TOOL_CALL:amap_maps_weather:city=上海]

**注意:**
1. 必须使用工具,不要直接回答
2. 格式必须完全正确,包括方括号和冒号
"""

HOTEL_AGENT_PROMPT = """你是酒店推荐专家。你的任务是根据城市和景点位置推荐合适的酒店。

**重要提示:**
你必须使用工具来搜索酒店!不要自己编造酒店信息!

**工具调用格式:**
使用maps_text_search工具搜索酒店时,必须严格按照以下格式:
`[TOOL_CALL:amap_maps_text_search:keywords=酒店,city=城市名]`

**示例:**
用户: "搜索北京的酒店"
你的回复: [TOOL_CALL:amap_maps_text_search:keywords=酒店,city=北京]

**注意:**
1. 必须使用工具,不要直接回答
2. 格式必须完全正确,包括方括号和冒号
3. 关键词使用"酒店"或"宾馆"
"""

PLANNER_AGENT_PROMPT = """你是行程规划专家。你的任务是根据景点信息和天气信息,生成详细的旅行计划。

**路线优化原则:**
1. 同一天的景点按地理位置就近排列,减少不必要的往返
2. 优先选择步行或公共交通可达的组合
3. 考虑景点游览时间 + 交通时间,确保一天行程合理不赶
4. 相邻景点间距离尽量在5公里以内,通勤时间控制在30分钟内

**美食推荐要求:**
1. 每餐必须推荐具体餐厅名称和地址,附带经纬度坐标
2. 推荐该餐厅的招牌菜(recommended_dish)
3. 餐厅尽量安排在景点附近(步行可达优先)
4. 标注预估人均消费

请严格按照以下JSON格式返回旅行计划:
```json
{
  "city": "城市名称",
  "start_date": "YYYY-MM-DD",
  "end_date": "YYYY-MM-DD",
  "days": [
    {
      "date": "YYYY-MM-DD",
      "day_index": 0,
      "description": "第1天行程概述(说明景点顺序安排的合理性)",
      "transportation": "交通方式",
      "accommodation": "住宿类型",
      "route_notes": "当日路线优化说明(为什么这样安排,距离/时间优势)",
      "hotel": {
        "name": "酒店名称",
        "address": "酒店地址",
        "location": {"longitude": 116.397128, "latitude": 39.916527},
        "price_range": "300-500元",
        "rating": "4.5",
        "distance": "距离景点2公里",
        "type": "经济型酒店",
        "estimated_cost": 400
      },
      "attractions": [
        {
          "name": "景点名称",
          "address": "详细地址",
          "location": {"longitude": 116.397128, "latitude": 39.916527},
          "visit_duration": 120,
          "description": "景点详细描述",
          "category": "景点类别",
          "ticket_price": 60
        }
      ],
      "meals": [
        {
          "type": "breakfast",
          "name": "早餐推荐名称",
          "restaurant": "餐厅名称",
          "address": "餐厅详细地址",
          "location": {"longitude": 116.397128, "latitude": 39.916527},
          "description": "推荐理由",
          "recommended_dish": "招牌菜名",
          "estimated_cost": 30
        },
        {
          "type": "lunch",
          "name": "午餐推荐名称",
          "restaurant": "餐厅名称",
          "address": "餐厅详细地址",
          "location": {"longitude": 116.397128, "latitude": 39.916527},
          "description": "推荐理由",
          "recommended_dish": "招牌菜名",
          "estimated_cost": 60
        },
        {
          "type": "dinner",
          "name": "晚餐推荐名称",
          "restaurant": "餐厅名称",
          "address": "餐厅详细地址",
          "location": {"longitude": 116.397128, "latitude": 39.916527},
          "description": "推荐理由",
          "recommended_dish": "招牌菜名",
          "estimated_cost": 90
        }
      ]
    }
  ],
  "weather_info": [
    {
      "date": "YYYY-MM-DD",
      "day_weather": "晴",
      "night_weather": "多云",
      "day_temp": 25,
      "night_temp": 15,
      "wind_direction": "南风",
      "wind_power": "1-3级"
    }
  ],
  "overall_suggestions": "总体建议(含路线策略说明)",
  "budget": {
    "total_attractions": 180,
    "total_hotels": 1200,
    "total_meals": 540,
    "total_transportation": 200,
    "total": 2120
  }
}
```

**重要提示:**
1. weather_info数组必须包含每一天的天气信息
2. 温度必须是纯数字(不要带°C等单位)
3. 每天安排2-3个景点,按最优路线排序
4. 景点间的交通时间必须合理
5. 每天必须包含早中晚三餐,每餐必须有具体餐厅名、地址、坐标、招牌菜
6. 提供实用的旅行建议和路线优化说明
7. **必须包含预算信息**:
   - 景点门票价格(ticket_price)
   - 餐饮预估费用(estimated_cost)
   - 酒店预估费用(estimated_cost)
   - 预算汇总(budget)包含各项总费用
"""


class MultiAgentTripPlanner:
    """多智能体旅行规划系统"""

    def __init__(self):
        """初始化多智能体系统"""
        print("🔄 开始初始化多智能体旅行规划系统...")

        try:
            settings = get_settings()
            self.llm = get_llm()

            # 创建共享的MCP工具(只创建一次)
            print("  - 创建共享MCP工具...")
            self.amap_tool = MCPTool(
                name="amap",
                description="高德地图服务",
                server_command=["uvx", "amap-mcp-server"],
                env={"AMAP_MAPS_API_KEY": settings.amap_api_key},
                auto_expand=True
            )
            self.amap_tool.expandable=True

            # 创建景点搜索Agent
            print("  - 创建景点搜索Agent...")
            self.attraction_agent = SimpleAgent(
                name="景点搜索专家",
                llm=self.llm,
                system_prompt=ATTRACTION_AGENT_PROMPT
            )
            self.attraction_agent.add_tool(self.amap_tool)

            # 创建天气查询Agent
            print("  - 创建天气查询Agent...")
            self.weather_agent = SimpleAgent(
                name="天气查询专家",
                llm=self.llm,
                system_prompt=WEATHER_AGENT_PROMPT
            )
            self.weather_agent.add_tool(self.amap_tool)

            # 创建酒店推荐Agent
            print("  - 创建酒店推荐Agent...")
            self.hotel_agent = SimpleAgent(
                name="酒店推荐专家",
                llm=self.llm,
                system_prompt=HOTEL_AGENT_PROMPT
            )
            self.hotel_agent.add_tool(self.amap_tool)

            # 创建行程规划Agent(不需要工具)
            print("  - 创建行程规划Agent...")
            self.planner_agent = SimpleAgent(
                name="行程规划专家",
                llm=self.llm,
                system_prompt=PLANNER_AGENT_PROMPT
            )

            print(f"✅ 多智能体系统初始化成功")
            print(f"   景点搜索Agent: {len(self.attraction_agent.list_tools())} 个工具")
            print(f"   天气查询Agent: {len(self.weather_agent.list_tools())} 个工具")
            print(f"   酒店推荐Agent: {len(self.hotel_agent.list_tools())} 个工具")

        except Exception as e:
            print(f"❌ 多智能体系统初始化失败: {str(e)}")
            import traceback
            traceback.print_exc()
            raise
    
    def plan_trip(self, request: TripRequest) -> TripPlan:
        """
        使用多智能体协作生成旅行计划

        Args:
            request: 旅行请求

        Returns:
            旅行计划
        """
        try:
            print(f"\n{'='*60}")
            print(f"🚀 开始多智能体协作规划旅行...")
            print(f"目的地: {request.city}")
            print(f"日期: {request.start_date} 至 {request.end_date}")
            print(f"天数: {request.travel_days}天")
            print(f"偏好: {', '.join(request.preferences) if request.preferences else '无'}")
            print(f"{'='*60}\n")

            # 步骤1: 景点搜索Agent搜索景点
            print("📍 步骤1: 搜索景点...")
            attraction_query = self._build_attraction_query(request)
            attraction_response = self.attraction_agent.run(attraction_query)
            print(f"景点搜索结果: {attraction_response[:200]}...\n")

            # 步骤1b: 严格/混合模式下，额外搜索参考内容中提到的具体景点
            should_search_ref = request.reference_mode in ("strict", "hybrid") and request.reference_content and request.reference_content.strip()
            is_strict = request.reference_mode == "strict"
            is_hybrid = request.reference_mode == "hybrid"
            ref_attraction_response = ""
            if should_search_ref:
                mode_label = "严格" if is_strict else "混合"
                print(f"📍 步骤1b: 搜索攻略中提到的具体景点（{mode_label}模式）...")
                ref_query = self._build_reference_search_query(request, "attraction")
                if ref_query:
                    ref_attraction_response = self.attraction_agent.run(ref_query)
                    print(f"攻略景点搜索结果: {ref_attraction_response[:200]}...\n")

            # 步骤2: 天气查询Agent查询天气
            print("🌤️  步骤2: 查询天气...")
            weather_query = f"请查询{request.city}的天气信息"
            weather_response = self.weather_agent.run(weather_query)
            print(f"天气查询结果: {weather_response[:200]}...\n")

            # 步骤3: 酒店推荐Agent搜索酒店
            print("🏨 步骤3: 搜索酒店...")
            hotel_query = f"请搜索{request.city}的{request.accommodation}酒店"
            if should_search_ref:
                hotel_query += f"\n请优先搜索参考内容中提到的酒店: {request.reference_content[:500]}"
            hotel_response = self.hotel_agent.run(hotel_query)
            print(f"酒店搜索结果: {hotel_response[:200]}...\n")

            # 合并搜索结果
            combined_attractions = attraction_response
            if ref_attraction_response:
                combined_attractions = attraction_response + "\n\n【攻略指定景点】\n" + ref_attraction_response

            # 步骤4: 行程规划Agent整合信息生成计划
            print("📋 步骤4: 生成行程计划...")
            planner_query = self._build_planner_query(request, combined_attractions, weather_response, hotel_response)
            planner_response = self.planner_agent.run(planner_query)
            print(f"行程规划结果: {planner_response[:300]}...\n")

            # 解析最终计划
            trip_plan = self._parse_response(planner_response, request)

            print(f"{'='*60}")
            print(f"✅ 旅行计划生成完成!")
            print(f"{'='*60}\n")

            return trip_plan

        except Exception as e:
            print(f"❌ 生成旅行计划失败: {str(e)}")
            import traceback
            traceback.print_exc()
            return self._create_fallback_plan(request)

    def get_alternatives(self, city: str, day_index: int, replace_type: str,
                         current_name: str, current_category: str = "", context: str = "") -> list:
        """获取AI优化的备选项(景点或餐厅)"""
        try:
            if replace_type == "attraction":
                search_keywords = current_category or "景点"
                query = (
                    f"请搜索{city}的{search_keywords}相关景点，排除'{current_name}'。\n"
                    f"当前行程上下文: {context}\n"
                    f"[TOOL_CALL:amap_maps_text_search:keywords={search_keywords},city={city}]"
                )
                response = self.attraction_agent.run(query)
            elif replace_type == "meal":
                query = (
                    f"请搜索{city}的特色餐厅或美食，排除'{current_name}'。\n"
                    f"当前行程上下文: {context}\n"
                    f"[TOOL_CALL:amap_maps_text_search:keywords=餐厅美食,city={city}]"
                )
                response = self.attraction_agent.run(query)
            else:
                return []

            alternatives = self._parse_alternatives(response, replace_type, city)
            return alternatives[:5]

        except Exception as e:
            print(f"⚠️ 获取备选项失败: {str(e)}")
            return []

    def _parse_alternatives(self, response: str, item_type: str, city: str) -> list:
        """解析Agent返回的搜索结果，提取备选项"""
        import re
        import json

        alternatives = []
        json_match = re.search(r'\[[\s\S]*\]', response)
        if json_match:
            try:
                items = json.loads(json_match.group())
                for item in items:
                    if isinstance(item, dict) and item.get("name"):
                        alternatives.append({
                            "name": item.get("name", ""),
                            "address": item.get("address", f"{city}市"),
                            "location": item.get("location", {"longitude": 116.4, "latitude": 39.9}),
                            "category": item.get("category", ""),
                            "rating": item.get("rating", None),
                            "reason": item.get("reason", f"位于{city}，与行程路线契合"),
                            "estimated_cost": item.get("estimated_cost", 0) if item_type == "meal" else item.get("ticket_price", 0),
                            "visit_duration": item.get("visit_duration", 120) if item_type == "attraction" else None,
                            "recommended_dish": item.get("recommended_dish", "") if item_type == "meal" else None,
                        })
                if alternatives:
                    return alternatives
            except json.JSONDecodeError:
                pass

        # 降级: 正则提取名称
        name_pattern = r'(?:[0-9]+[.、]\s*)?([^\n,，0-9]{3,30}?)(?:[：:]\s*[^\n]*)?(?:\n|$)'
        matches = re.findall(name_pattern, response)
        seen = set()
        for name in matches:
            name = name.strip().rstrip('。，,')
            if len(name) >= 3 and name not in seen and name not in city:
                seen.add(name)
                alternatives.append({
                    "name": name,
                    "address": f"{city}市",
                    "location": {"longitude": 116.4, "latitude": 39.9},
                    "category": "",
                    "reason": f"位于{city}，与行程路线契合",
                    "estimated_cost": 0,
                    "visit_duration": 120 if item_type == "attraction" else None,
                    "recommended_dish": "" if item_type == "meal" else None,
                })
            if len(alternatives) >= 5:
                break

        if not alternatives:
            for i in range(3):
                alternatives.append({
                    "name": f"{city}备选{i + 1}",
                    "address": f"{city}市",
                    "location": {"longitude": 116.4 + i * 0.01, "latitude": 39.9 + i * 0.01},
                    "category": "",
                    "reason": f"AI推荐{city}地区{'景点' if item_type == 'attraction' else '餐厅'}",
                    "estimated_cost": 0,
                    "visit_duration": 120 if item_type == "attraction" else None,
                    "recommended_dish": "" if item_type == "meal" else None,
                })

        return alternatives

    def _build_attraction_query(self, request: TripRequest) -> str:
        """构建景点搜索查询 - 直接包含工具调用"""
        keywords = []
        if request.preferences:
            keywords = request.preferences[0]
        else:
            keywords = "景点"

        query = f"请使用amap_maps_text_search工具搜索{request.city}的{keywords}相关景点。\n[TOOL_CALL:amap_maps_text_search:keywords={keywords},city={request.city}]"
        return query

    def _build_reference_search_query(self, request: TripRequest, search_type: str) -> str:
        """
        从参考内容中提取关键地点名，构建针对性搜索查询
        让Agent直接搜索小红书里提到的具体景点/餐厅
        """
        import re
        content = request.reference_content[:2000]
        city = request.city

        # 尝试提取【】「」""中的名称，或常见地点关键词后的名称
        patterns = [
            r'【(.+?)】',           # 【西湖】
            r'「(.+?)」',           # 「雷峰塔」
            r'"(.*?)"',             # "断桥残雪"
            r'《(.+?)》',           # 《灵隐寺》
            r'(?:推荐|打卡|必去|值得去)[：:]?\s*(.{2,20}?)(?:[，。,、\s]|$)',  # 推荐xxx
            r'(?:吃|尝|品)[：:]?\s*(.{2,20}?)(?:[，。,、\s]|$)',              # 吃xxx
        ]

        names = []
        for pattern in patterns[:4]:  # 先看括号内的精确地名
            found = re.findall(pattern, content)
            names.extend([n.strip() for n in found if len(n.strip()) >= 2 and len(n.strip()) <= 20])

        # 去重，去城市名本身
        seen = set()
        unique_names = []
        for n in names:
            if n not in seen and n != city and city not in n:
                seen.add(n)
                unique_names.append(n)

        if not unique_names:
            return ""

        if search_type == "attraction":
            # 逐个搜索参考内容中的具体景点
            searches = []
            for name in unique_names[:5]:  # 最多搜5个
                searches.append(f"[TOOL_CALL:amap_maps_text_search:keywords={name},city={city}]")
            return f"请搜索参考攻略中提到的以下具体地点，各搜一次:\n" + "\n".join(searches)

        return ""

    def _build_planner_query(self, request: TripRequest, attractions: str, weather: str, hotels: str = "") -> str:
        """构建行程规划查询"""
        query = f"""请根据以下信息生成{request.city}的{request.travel_days}天旅行计划:

**基本信息:**
- 城市: {request.city}
- 日期: {request.start_date} 至 {request.end_date}
- 天数: {request.travel_days}天
- 交通方式: {request.transportation}
- 住宿: {request.accommodation}
- 偏好: {', '.join(request.preferences) if request.preferences else '无'}

**景点信息:**
{attractions}

**天气信息:**
{weather}

**酒店信息:**
{hotels}

**路线规划要求:**
1. 每天安排2-3个景点,按地理位置就近排序,形成最优游览路径
2. 景点间距离尽量在5公里以内,交通耗时控制在30分钟内
3. 考虑景点开放时间和游览时长,合理安排先后顺序
4. 在每日route_notes中说明路线安排的逻辑(距离优势/时间优势/便捷性)
5. 同一天不要安排方向相反的景点,避免折返

**美食推荐要求:**
1. 每餐必须给出具体的餐厅名称、地址和坐标
2. 餐厅尽量选在景点旁边(步行5-10分钟内可达)
3. 推荐每家餐厅的招牌菜(recommended_dish)
4. 标注合理的人均消费
"""
        if request.free_text_input:
            query += f"\n**额外要求:** {request.free_text_input}"

        # 注入小红书/截图参考内容
        if request.reference_content and request.reference_content.strip():
            is_strict = request.reference_mode == "strict"
            is_hybrid = request.reference_mode == "hybrid"
            query += f"""

**📌 参考攻略内容:**
{request.reference_content.strip()}
"""
            if is_strict:
                query += """
**⚠️ 严格模式: 以下攻略内容是行程的蓝图，请直接按攻略内容制定行程。**
- 攻略中提到的景点全部安排进行程，「攻略指定景点」搜索结果中有的直接使用
- 攻略中推荐的餐厅就是当天的餐饮安排，直接采用
- 攻略中的路线顺序作为默认顺序，仅在地理上明显不合理时才微调
- 景点/餐厅description开头标注「📋 攻略推荐」，表明来源于参考攻略
"""
            elif is_hybrid:
                query += """
**🔗 混合模式: 以攻略内容为主体框架，AI负责补充和完善。**
- 攻略中提到的景点和餐厅优先安排（搜得到的直接用），作为行程的主骨架
- AI可以在此基础上补充：同类优质景点、沿途美食、合理的时间安排
- 如果攻略只提到2个景点，AI可以补充第3个同路线上的合适景点
- AI负责补充：天气建议、交通方式、酒店推荐、具体路线规划
- 攻略内容标记「📋 攻略」，AI补充内容标记「🤖 AI推荐」
- 路线顺序以攻略为基准，AI可微调优化（调整范围不超过1个位置的交换）
"""
            else:
                query += """
**💡 灵感参考模式: 以下攻略内容仅供参考风格和灵感，请不要直接采用其中的具体地点。**
- 攻略中的具体景点和餐厅仅作为风格参考（如"攻略偏好老街小巷"→搜索同类地点）
- 实际行程中的景点、餐厅请基于地图搜索结果独立选择，不必与攻略一致
- 如果攻略中有好的旅行技巧（如最佳游览时间、排队避坑等），可以吸收到建议中
- 不要刻意安排攻略中提到的地点，让AI根据偏好和路线优化原则自主决定
"""

        return query
    
    def _parse_response(self, response: str, request: TripRequest) -> TripPlan:
        """
        解析Agent响应
        
        Args:
            response: Agent响应文本
            request: 原始请求
            
        Returns:
            旅行计划
        """
        try:
            # 尝试从响应中提取JSON
            # 查找JSON代码块
            if "```json" in response:
                json_start = response.find("```json") + 7
                json_end = response.find("```", json_start)
                json_str = response[json_start:json_end].strip()
            elif "```" in response:
                json_start = response.find("```") + 3
                json_end = response.find("```", json_start)
                json_str = response[json_start:json_end].strip()
            elif "{" in response and "}" in response:
                # 直接查找JSON对象
                json_start = response.find("{")
                json_end = response.rfind("}") + 1
                json_str = response[json_start:json_end]
            else:
                raise ValueError("响应中未找到JSON数据")
            
            # 解析JSON
            data = json.loads(json_str)
            
            # 转换为TripPlan对象
            trip_plan = TripPlan(**data)
            
            return trip_plan
            
        except Exception as e:
            print(f"⚠️  解析响应失败: {str(e)}")
            print(f"   将使用备用方案生成计划")
            return self._create_fallback_plan(request)
    
    def _create_fallback_plan(self, request: TripRequest) -> TripPlan:
        """创建备用计划(当Agent失败时)"""
        from datetime import datetime, timedelta
        
        # 解析日期
        start_date = datetime.strptime(request.start_date, "%Y-%m-%d")
        
        # 创建每日行程
        days = []
        for i in range(request.travel_days):
            current_date = start_date + timedelta(days=i)
            
            day_plan = DayPlan(
                date=current_date.strftime("%Y-%m-%d"),
                day_index=i,
                description=f"第{i+1}天行程",
                transportation=request.transportation,
                accommodation=request.accommodation,
                attractions=[
                    Attraction(
                        name=f"{request.city}景点{j+1}",
                        address=f"{request.city}市",
                        location=Location(longitude=116.4 + i*0.01 + j*0.005, latitude=39.9 + i*0.01 + j*0.005),
                        visit_duration=120,
                        description=f"这是{request.city}的著名景点",
                        category="景点"
                    )
                    for j in range(2)
                ],
                meals=[
                    Meal(type="breakfast", name=f"第{i+1}天早餐", description="当地特色早餐"),
                    Meal(type="lunch", name=f"第{i+1}天午餐", description="午餐推荐"),
                    Meal(type="dinner", name=f"第{i+1}天晚餐", description="晚餐推荐")
                ]
            )
            days.append(day_plan)
        
        return TripPlan(
            city=request.city,
            start_date=request.start_date,
            end_date=request.end_date,
            days=days,
            weather_info=[],
            overall_suggestions=f"这是为您规划的{request.city}{request.travel_days}日游行程,建议提前查看各景点的开放时间。"
        )


# 全局多智能体系统实例
_multi_agent_planner = None


def get_trip_planner_agent() -> MultiAgentTripPlanner:
    """获取多智能体旅行规划系统实例(单例模式)"""
    global _multi_agent_planner

    if _multi_agent_planner is None:
        _multi_agent_planner = MultiAgentTripPlanner()

    return _multi_agent_planner

