import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from app.models.schemas import Location
from app.services.amap_rest import AMapServiceError, get_route, _get_json
from app.services.schedule_validator import validate_schedule
from test_schedule import sample, REQUEST


class RouteReliabilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_empty_transit_uses_real_short_walk(self):
        responses = [{"route": {"transits": []}}, {"route": {"paths": [{"duration": "480", "distance": "560"}]}}]
        with patch("app.services.amap_rest._get_json", new=AsyncMock(side_effect=responses)) as query:
            result = await get_route(Location(longitude=116, latitude=40), Location(longitude=116.015, latitude=40), "北京", "transit")
        self.assertEqual(result["mode"], "walking")
        self.assertEqual(result["duration_minutes"], 8)
        self.assertEqual(query.call_args.args[0], "direction/walking")

    async def test_walkable_hop_skips_the_transit_request(self):
        # ~345 m apart, as Amap returns zero transit plans for such hops.
        with patch("app.services.amap_rest._get_json", new=AsyncMock(return_value={"route": {"paths": [{"duration": "300", "distance": "380"}]}})) as query:
            result = await get_route(Location(longitude=116.273042, latitude=39.99815), Location(longitude=116.275179, latitude=39.999617), "北京", "transit")
        self.assertEqual(query.await_count, 1)
        self.assertEqual(query.call_args.args[0], "direction/walking")
        self.assertEqual((result["mode"], result["note"]), ("walking", "距离较近，按步行安排"))

    async def test_long_walk_is_not_silently_substituted(self):
        with patch("app.services.amap_rest._get_json", new=AsyncMock(side_effect=[{"route": {"transits": []}}, {"route": {"paths": [{"duration": "3600", "distance": "5000"}]}}])):
            with self.assertRaises(AMapServiceError) as error:
                await get_route(Location(longitude=116, latitude=40), Location(longitude=116.1, latitude=40), "北京", "transit")
        self.assertEqual(error.exception.code, "no_transit")

    async def query_with_responses(self, payloads):
        client = AsyncMock()
        def response(payload):
            return SimpleNamespace(raise_for_status=lambda: None, json=lambda: payload)
        client.get.side_effect = [response(p) for p in payloads]
        with patch("app.services.amap_rest._shared_client", return_value=client), patch("app.services.amap_rest.get_settings", return_value=SimpleNamespace(amap_api_key="test-key")), patch("app.services.amap_rest._pace_route_request", new=AsyncMock()), patch("app.services.amap_rest.asyncio.sleep", new=AsyncMock()):
            try:
                result = await _get_json("direction/walking", {})
            except AMapServiceError as exc:
                result = exc
        return result, client.get.await_count

    async def test_qps_failure_retries_then_returns_provider_data(self):
        result, count = await self.query_with_responses([{"status": "0", "infocode": "10021"}, {"status": "1", "route": {"paths": []}}])
        self.assertEqual(count, 2)
        self.assertEqual(result["status"], "1")

    async def test_permanent_failure_not_retried_or_leaked(self):
        result, count = await self.query_with_responses([{"status": "0", "infocode": "10001", "info": "test-key"}])
        self.assertEqual(count, 1)
        self.assertEqual(result.code, "10001")
        self.assertNotIn("test-key", str(result))

    async def test_retries_are_bounded(self):
        result, count = await self.query_with_responses([{"status": "0", "infocode": "10021"}] * 3)
        self.assertEqual(count, 3)
        self.assertEqual(result.code, "10021")

    async def test_actual_mode_and_failure_reason_reach_schedule(self):
        plan = sample()
        await validate_schedule(plan, REQUEST, AsyncMock(return_value={"duration_minutes": 8, "distance_meters": 560, "mode": "walking", "note": "公交无方案，实际查询步行"}))
        self.assertTrue(all(seg.mode == "walking" for seg in plan.days[0].schedule.segments))
        await validate_schedule(plan, REQUEST, AsyncMock(side_effect=AMapServiceError("10021", "高德交通限流（代码10021）", True)))
        self.assertIn("10021", plan.days[0].schedule.segments[0].note)
        self.assertIsNone(plan.days[0].schedule.segments[0].duration_minutes)


class RouteDetailTests(unittest.IsolatedAsyncioTestCase):
    async def test_transit_legs_fare_and_polyline_are_kept(self):
        transit = {"duration": "1800", "distance": "9000", "cost": "4.0", "segments": [
            {"walking": {"distance": "300", "duration": "240", "steps": [{"polyline": "116.40,39.90;116.401,39.901"}]},
             "bus": {"buslines": [{"name": "地铁2号线内环(积水潭--积水潭)", "type": "地铁线路", "via_num": "3",
                                   "departure_stop": {"name": "前门"}, "arrival_stop": {"name": "西直门"},
                                   "distance": "8000", "duration": "1200", "polyline": "116.401,39.901;116.35,39.94"},
                                  {"name": "地铁4号线(天宫院--安河桥北)"}]},
             "entrance": {"name": "A口"}, "exit": [], "railway": {"spaces": []}},
            {"walking": {"distance": "200", "duration": "180", "steps": []}, "bus": {"buslines": []}, "entrance": [], "exit": []}]}
        response = {"route": {"taxi_cost": "45", "transits": [transit]}}
        with patch("app.services.amap_rest._get_json", new=AsyncMock(return_value=response)):
            result = await get_route(Location(longitude=116.40, latitude=39.90), Location(longitude=116.35, latitude=39.94), "北京", "transit")
        self.assertEqual((result["fare"], result["taxi_cost"]), (4.0, 45.0))
        self.assertEqual([leg["kind"] for leg in result["legs"]], ["walk", "subway", "walk"])
        ride = result["legs"][1]
        self.assertEqual((ride["line"], ride["direction"], ride["stops"], ride["entrance"], ride["alternatives"]),
                         ("地铁2号线内环", "", 4, "A口", ["地铁4号线"]))
        self.assertEqual(result["polyline"].split(";")[0], "116.40000,39.90000")

    def test_polyline_is_thinned_keeping_endpoints(self):
        from app.services.amap_rest import _compact, POLYLINE_POINTS
        points = [f"116.{i:05d},39.90000" for i in range(1000)]
        compact = _compact(points).split(";")
        self.assertEqual(len(compact), POLYLINE_POINTS)
        self.assertEqual((compact[0], compact[-1]), ("116.00000,39.90000", "116.00999,39.90000"))
