"""高德地图MCP服务封装

已移除 hello-agents 依赖，改用 mcp_manager (MultiServerMCPClient) 调用高德MCP工具。
与 langgraph_planner 共用同一套 MCP 连接池 (P5优化)。
"""

import json
import re
from typing import List, Dict, Any, Optional

from ..services.mcp_manager import get_mcp_manager
from ..models.schemas import Location, POIInfo, WeatherInfo


def _extract_json(text: str) -> Optional[dict]:
    """从文本中提取 JSON 对象

    MCP 工具返回的可能是纯 JSON 字符串，也可能是包含 JSON 的文本。
    依次尝试: 直接解析 → 正则提取 → 失败返回 None
    """
    if not text or not text.strip():
        return None
    text = text.strip()
    # 1. 直接尝试 JSON 解析
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # 2. 正则提取最大的 JSON 对象
    match = re.search(r'\{[\s\S]*\}', text)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    # 3. 尝试提取 JSON 数组
    match = re.search(r'\[[\s\S]*\]', text)
    if match:
        try:
            arr = json.loads(match.group())
            if isinstance(arr, list) and arr:
                return {"items": arr}
        except json.JSONDecodeError:
            pass
    return None


def _parse_location(loc_str: str) -> Optional[Location]:
    """解析坐标字符串 '116.397428,39.916392' -> Location"""
    if not loc_str or not isinstance(loc_str, str):
        return None
    parts = loc_str.split(",")
    if len(parts) == 2:
        try:
            return Location(longitude=float(parts[0]), latitude=float(parts[1]))
        except ValueError:
            pass
    return None


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
            # MCP 工具可能返回 list / str / ToolMessage
            if isinstance(result, list):
                # list[{'type':'text','text':'...'}]
                parts = []
                for part in result:
                    if isinstance(part, dict) and "text" in part:
                        parts.append(str(part["text"]))
                    elif isinstance(part, str):
                        parts.append(part)
                return "\n".join(parts)
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
        """搜索POI

        解析高德 maps_text_search 返回的 JSON:
        { "pois": [ { "id", "name", "type", "address", "location", "tel" } ] }
        """
        try:
            result = await self._call_tool("maps_text_search", {
                "keywords": keywords,
                "city": city,
                "citylimit": str(citylimit).lower()
            })
            print(f"POI搜索结果(原始, 前200字符): {result[:200]}...")

            data = _extract_json(result)
            if not data:
                print(f"⚠️ POI搜索结果无法解析为JSON")
                return []

            pois_raw = data.get("pois", [])
            if isinstance(pois_raw, str):
                try:
                    pois_raw = json.loads(pois_raw)
                except json.JSONDecodeError:
                    pois_raw = []

            pois: List[POIInfo] = []
            for p in pois_raw[:20]:  # 限制20条
                if not isinstance(p, dict):
                    continue
                name = p.get("name", "")
                if not name:
                    continue
                loc = _parse_location(p.get("location", ""))
                if loc is None:
                    # 坐标缺失的POI跳过(影响酒店周边搜索)
                    continue
                try:
                    poi = POIInfo(
                        id=str(p.get("id", "")),
                        name=name,
                        type=str(p.get("type", "景点")),
                        address=str(p.get("address", "")),
                        location=loc,
                        tel=p.get("tel") or None,
                    )
                    pois.append(poi)
                except Exception as e:
                    print(f"   ⚠️ 跳过POI '{name}': {e}")
                    continue

            print(f"   ✅ 解析POI成功: {len(pois)} 条")
            return pois
        except Exception as e:
            print(f"❌ POI搜索失败: {e}")
            return []

    async def get_weather(self, city: str) -> List[WeatherInfo]:
        """查询天气

        解析高德 maps_weather 返回的 JSON:
        { "forecasts": [ { "casts": [ { "date", "dayweather", "nightweather",
          "daytemp", "nighttemp", "winddir", "windpower" } ] } ] }
        """
        try:
            result = await self._call_tool("maps_weather", {"city": city})
            print(f"天气查询结果(原始, 前200字符): {result[:200]}...")

            data = _extract_json(result)
            if not data:
                print(f"⚠️ 天气查询结果无法解析为JSON")
                return []

            forecasts = data.get("forecasts", [])
            if isinstance(forecasts, str):
                try:
                    forecasts = json.loads(forecasts)
                except json.JSONDecodeError:
                    forecasts = []

            weathers: List[WeatherInfo] = []
            for fc in forecasts:
                if not isinstance(fc, dict):
                    continue
                casts = fc.get("casts", [])
                if isinstance(casts, str):
                    try:
                        casts = json.loads(casts)
                    except json.JSONDecodeError:
                        casts = []
                for c in casts:
                    if not isinstance(c, dict):
                        continue
                    try:
                        w = WeatherInfo(
                            date=str(c.get("date", "")),
                            day_weather=str(c.get("dayweather", "")),
                            night_weather=str(c.get("nightweather", "")),
                            day_temp=c.get("daytemp", 0),
                            night_temp=c.get("nighttemp", 0),
                            wind_direction=str(c.get("winddir", "")),
                            wind_power=str(c.get("windpower", "")),
                        )
                        weathers.append(w)
                    except Exception as e:
                        print(f"   ⚠️ 跳过天气记录: {e}")
                        continue

            print(f"   ✅ 解析天气成功: {len(weathers)} 天")
            return weathers
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
        """规划路线

        解析高德 maps_direction_* 返回的 JSON:
        { "route": { "paths": [ { "distance", "duration", "steps" } ] } }
        """
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
            print(f"路线规划结果(原始, 前200字符): {result[:200]}...")

            data = _extract_json(result)
            if not data:
                print(f"⚠️ 路线规划结果无法解析为JSON")
                return {"raw": result[:500]} if result else {}

            route = data.get("route", {})
            if isinstance(route, str):
                try:
                    route = json.loads(route)
                except json.JSONDecodeError:
                    route = {}

            paths = route.get("paths", []) if isinstance(route, dict) else []
            if isinstance(paths, str):
                try:
                    paths = json.loads(paths)
                except json.JSONDecodeError:
                    paths = []

            if not paths:
                return {"raw": result[:500]} if result else {}

            # 取第一条路径
            path = paths[0] if isinstance(paths[0], dict) else {}
            try:
                distance = float(path.get("distance", 0))
            except (ValueError, TypeError):
                distance = 0
            try:
                duration = int(float(path.get("duration", 0)))
            except (ValueError, TypeError):
                duration = 0

            return {
                "distance": distance,
                "duration": duration,
                "route_type": route_type,
                "description": f"距离{distance/1000:.1f}公里, 预计{duration//60}分钟"
            }
        except Exception as e:
            print(f"❌ 路线规划失败: {e}")
            return {}

    async def geocode(self, address: str, city: Optional[str] = None) -> Optional[Location]:
        """地理编码(地址转坐标)

        解析高德 maps_geo 返回的 JSON:
        { "geocodes": [ { "location": "116.397428,39.916392" } ] }
        """
        try:
            arguments = {"address": address}
            if city:
                arguments["city"] = city
            result = await self._call_tool("maps_geo", arguments)
            print(f"地理编码结果(原始, 前200字符): {result[:200]}...")

            data = _extract_json(result)
            if not data:
                print(f"⚠️ 地理编码结果无法解析为JSON")
                return None

            geocodes = data.get("geocodes", [])
            if isinstance(geocodes, str):
                try:
                    geocodes = json.loads(geocodes)
                except json.JSONDecodeError:
                    geocodes = []

            if not geocodes or not isinstance(geocodes[0], dict):
                return None

            loc_str = geocodes[0].get("location", "")
            loc = _parse_location(loc_str)
            if loc:
                print(f"   ✅ 地理编码成功: {address} -> {loc_str}")
            return loc
        except Exception as e:
            print(f"❌ 地理编码失败: {e}")
            return None

    async def get_poi_detail(self, poi_id: str) -> Dict[str, Any]:
        """获取POI详情"""
        try:
            result = await self._call_tool("maps_search_detail", {"id": poi_id})
            print(f"POI详情结果(原始, 前200字符): {result[:200]}...")

            data = _extract_json(result)
            if data:
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
