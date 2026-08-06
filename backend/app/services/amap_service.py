"""高德地图MCP服务封装

已移除 hello-agents 依赖，改用 mcp_manager (MultiServerMCPClient) 调用高德MCP工具。
与 langgraph_planner 共用同一套 MCP 连接池 (P5优化)。
"""

import json
import re
from typing import List, Dict, Any, Optional

from ..services.mcp_manager import get_mcp_manager
from ..models.schemas import Location, POIInfo, WeatherInfo


class AmapService:
    """高德地图服务封装类"""

    async def _call_tool(self, tool_name: str, arguments: dict) -> str:
        """调用高德MCP工具，返回结果字符串"""
        manager = get_mcp_manager()
        await manager.ensure_connected()
        tool = manager.get_tool(tool_name)
        if tool is None:
            print(f"⚠️ 未找到MCP工具: {tool_name}")
            return ""
        try:
            result = await tool.ainvoke(arguments)
            manager.record_request(success=True)
            # 提取文本内容
            if isinstance(result, str):
                return result
            content = getattr(result, "content", str(result))
            if isinstance(content, list):
                parts = []
                for part in content:
                    if isinstance(part, dict) and "text" in part:
                        parts.append(str(part["text"]))
                    elif isinstance(part, str):
                        parts.append(part)
                return "\n".join(parts)
            return str(content) if content else ""
        except Exception as e:
            print(f"❌ 工具 {tool_name} 调用失败: {e}")
            manager.record_request(success=False)
            return ""

    async def search_poi(self, keywords: str, city: str, citylimit: bool = True) -> List[POIInfo]:
        """搜索POI"""
        try:
            result = await self._call_tool("maps_text_search", {
                "keywords": keywords,
                "city": city,
                "citylimit": str(citylimit).lower()
            })
            print(f"POI搜索结果: {result[:200]}...")
            # TODO: 解析实际的POI数据
            return []
        except Exception as e:
            print(f"❌ POI搜索失败: {e}")
            return []

    async def get_weather(self, city: str) -> List[WeatherInfo]:
        """查询天气"""
        try:
            result = await self._call_tool("maps_weather", {"city": city})
            print(f"天气查询结果: {result[:200]}...")
            # TODO: 解析实际的天气数据
            return []
        except Exception as e:
            print(f"❌ 天气查询失败: {e}")
            return []

    async def plan_route(
        self,
        origin_address: str,
        destination_address: str,
        origin_city: Optional[str] = None,
        destination_city: Optional[str] = None,
        route_type: str = "walking"
    ) -> Dict[str, Any]:
        """规划路线"""
        try:
            tool_map = {
                "walking": "maps_direction_walking_by_address",
                "driving": "maps_direction_driving_by_address",
                "transit": "maps_direction_transit_integrated_by_address"
            }
            tool_name = tool_map.get(route_type, "maps_direction_walking_by_address")
            arguments = {
                "origin_address": origin_address,
                "destination_address": destination_address
            }
            if origin_city:
                arguments["origin_city"] = origin_city
            if destination_city:
                arguments["destination_city"] = destination_city
            result = await self._call_tool(tool_name, arguments)
            print(f"路线规划结果: {result[:200]}...")
            # TODO: 解析实际的路线数据
            return {}
        except Exception as e:
            print(f"❌ 路线规划失败: {e}")
            return {}

    async def geocode(self, address: str, city: Optional[str] = None) -> Optional[Location]:
        """地理编码(地址转坐标)"""
        try:
            arguments = {"address": address}
            if city:
                arguments["city"] = city
            result = await self._call_tool("maps_geo", arguments)
            print(f"地理编码结果: {result[:200]}...")
            # TODO: 解析实际的坐标数据
            return None
        except Exception as e:
            print(f"❌ 地理编码失败: {e}")
            return None

    async def get_poi_detail(self, poi_id: str) -> Dict[str, Any]:
        """获取POI详情"""
        try:
            result = await self._call_tool("maps_search_detail", {"id": poi_id})
            print(f"POI详情结果: {result[:200]}...")
            # 尝试从结果中提取JSON
            json_match = re.search(r'\{.*\}', result, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group())
                return data
            return {"raw": result}
        except Exception as e:
            print(f"❌ 获取POI详情失败: {e}")
            return {}


# 全局服务实例
_amap_service: Optional[AmapService] = None


def get_amap_service() -> AmapService:
    """获取高德地图服务实例(单例模式)"""
    global _amap_service
    if _amap_service is None:
        _amap_service = AmapService()
    return _amap_service
