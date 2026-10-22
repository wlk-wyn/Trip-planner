"""Weather-aware indoor suggestions and multi-day optimization after drag edits."""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from app.models.schemas import TripPlan
from app.services.planning_runs import start_run, read_run, execute, _tasks
from app.services.trip_repository import TripRepository
from app.services.weather_advice import classify, weather_advice
from test_travel_agent import REQUEST, PLAN
from test_saved_trips import OWNER


def two_day_plan():
    raw = copy.deepcopy(PLAN)
    raw["end_date"] = "2026-10-04"
    park = copy.deepcopy(raw["days"][0]["attractions"][0])
    park.update(name="太子湾公园", category="风景名胜;公园广场;公园", poi_id="park")
    raw["days"][0]["attractions"].append(park)
    second = copy.deepcopy(raw["days"][0])
    second.update(date="2026-10-04", day_index=1)
    raw["days"].append(second)
    return TripPlan(**raw)


MUSEUM = {"poi_id": "m1", "name": "浙江省博物馆", "address": "孤山路25号", "coordinates": [120.14, 30.25], "category": "科教文化服务;博物馆"}


class WeatherAdviceTests(unittest.IsolatedAsyncioTestCase):
    def test_classification_prefers_amap_category_then_name(self):
        plan = two_day_plan()
        museum, park = plan.days[0].attractions
        self.assertEqual(classify(museum), "indoor")
        self.assertEqual(classify(park), "outdoor")
        park.category = "景点"; park.name = "某某中心"
        self.assertEqual(classify(park), "unknown")

    async def test_wet_day_offers_nearby_indoor_candidates_only_for_unlocked_outdoor(self):
        plan = two_day_plan()
        plan.days[1].attractions[1].locked = True
        forecast = AsyncMock(return_value=[{"date": "2026-10-03", "dayweather": "小雨", "nightweather": "阴", "daytemp": "20", "nighttemp": "15"},
                                           {"date": "2026-10-04", "dayweather": "中雨", "nightweather": "雨"}])
        searcher = AsyncMock(return_value=[{**MUSEUM, "poi_id": "B001", "name": "西湖博物馆"}, MUSEUM])
        result = await weather_advice(plan, searcher=searcher, forecaster=forecast)
        first, second = result["days"]
        self.assertTrue(first["wet"])
        self.assertEqual([s["name"] for s in first["suggestions"]], ["太子湾公园"])
        # Places already in the trip are not suggested again.
        self.assertEqual([c["name"] for c in first["suggestions"][0]["candidates"]], ["浙江省博物馆"])
        self.assertEqual(first["suggestions"][0]["candidates"][0]["poi_id"], "m1")
        self.assertEqual(second["suggestions"], [])  # locked visit is left alone
        self.assertTrue(result["forecast_available"])

    async def test_dry_or_unforecast_days_get_no_suggestions(self):
        plan = two_day_plan()
        forecast = AsyncMock(return_value=[{"date": "2026-10-03", "dayweather": "晴", "nightweather": "多云"}])
        searcher = AsyncMock()
        result = await weather_advice(plan, searcher=searcher, forecaster=forecast)
        self.assertEqual([d["suggestions"] for d in result["days"]], [[], []])
        self.assertIsNone(result["days"][1]["weather"])
        self.assertIn("1/2 天有预报", result["note"])
        searcher.assert_not_awaited()


class MultiDayOptimizeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = TripRepository(Path(self.tmp.name) / "trips.sqlite3")
        self.trip = self.repo.create(OWNER, REQUEST.model_dump(), two_day_plan().model_dump())

    async def asyncTearDown(self):
        import asyncio
        for task in list(_tasks.values()): task.cancel()
        await asyncio.gather(*_tasks.values(), return_exceptions=True)
        self.tmp.cleanup()

    async def test_changed_days_are_optimized_in_one_version(self):
        body = dict(kind="optimize", date="", dates=["2026-10-03", "2026-10-04"], base_version=1, request_id=str(uuid4()))

        async def fake(day, request, city, progress=None):
            await progress("核实交通", 1, 2)
            day = day.model_copy(deep=True); day.attractions.reverse()
            return day, {"status": "feasible_under_assumptions", "note": "ok", "queries": 3}
        with patch("app.services.planning_runs.launch"):
            run = start_run(self.repo, OWNER, self.trip["id"], body)
        with patch("app.services.schedule_solver.optimize_day", new=fake):
            await execute(self.repo, OWNER, run["id"])
        result = read_run(self.repo, OWNER, run["id"])
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["result"]["version"], 2)
        self.assertEqual(result["result"]["optimization"]["queries"], 6)
        self.assertEqual(len(result["result"]["optimization"]["days"]), 2)
        self.assertEqual([a["name"] for a in result["result"]["plan"]["days"][1]["attractions"]], ["太子湾公园", "西湖博物馆"])


if __name__ == "__main__":
    unittest.main()


class FollowUpTests(unittest.IsolatedAsyncioTestCase):
    async def test_consecutive_wet_days_prefer_different_venues(self):
        plan = two_day_plan()
        forecast = AsyncMock(return_value=[{"date": d.date, "dayweather": "中雨"} for d in plan.days])
        other = {**MUSEUM, "poi_id": "m2", "name": "杭州博物馆"}
        result = await weather_advice(plan, searcher=AsyncMock(return_value=[MUSEUM, other]), forecaster=forecast)
        firsts = [d["suggestions"][0]["candidates"][0]["name"] for d in result["days"] if d["suggestions"]]
        self.assertEqual(len(set(firsts)), len(firsts))

    async def test_unfilled_meal_slot_gets_a_wider_second_search(self):
        from app.agents.langgraph_planner import LangGraphTripPlanner
        plan = TripPlan(**PLAN)
        far = {"poi_id": "r9", "name": "湖边小馆", "address": "北山路", "coordinates": [120.175, 30.24]}  # ~2.4 km east
        calls = []

        async def nearby(lng, lat, keyword="", radius=1800, category=""):
            calls.append(radius)
            return [far]
        with patch("app.agents.langgraph_planner.search_nearby_restaurants", new=nearby):
            await LangGraphTripPlanner.__new__(LangGraphTripPlanner)._align_meals_to_route(plan)
        self.assertIn(3500, calls)
        self.assertIn("湖边小馆", [m.restaurant for m in plan.days[0].meals])
