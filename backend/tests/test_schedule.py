import copy
import unittest
from unittest.mock import AsyncMock, patch
from app.models.schemas import TripPlan, Location, Attraction
from app.services.schedule_validator import validate_schedule
from app.services.amap_rest import get_route
from test_travel_agent import PLAN, REQUEST, FakeModel, make_agent
from langchain_core.messages import AIMessage
import json


def sample(count=2):
    raw = copy.deepcopy(PLAN)
    for i in range(1, count):
        item = copy.deepcopy(raw["days"][0]["attractions"][0])
        item["name"] = f"景点{i}"
        item["location"]["longitude"] += i / 100
        raw["days"][0]["attractions"].append(item)
    return TripPlan(**raw)


class ScheduleTests(unittest.IsolatedAsyncioTestCase):
    async def test_verified_route_and_buffer_included(self):
        plan = sample()
        provider = AsyncMock(return_value={"duration_minutes": 25, "distance_meters": 1800})
        await validate_schedule(plan, REQUEST, provider)
        schedule = plan.days[0].schedule
        self.assertEqual(schedule.status, "feasible")
        self.assertEqual(schedule.minimum_minutes, 220)
        self.assertEqual(schedule.items[1].start_time, "11:10")
        self.assertEqual(schedule.segments[0].duration_minutes, 25)
        self.assertEqual(provider.call_args.args[3], "transit")
        self.assertIsNone(plan.budget)

    async def test_failure_is_unknown_and_never_zero(self):
        plan = sample()
        await validate_schedule(plan, REQUEST, AsyncMock(side_effect=RuntimeError("offline")))
        schedule = plan.days[0].schedule
        self.assertEqual(schedule.status, "unverified")
        self.assertIsNone(schedule.segments[0].duration_minutes)
        self.assertIsNone(schedule.items[1].start_time)
        self.assertIsNone(schedule.items[1].end_time)
        self.assertEqual(schedule.minimum_minutes, 195)

    async def test_overtime_keeps_all_stops(self):
        plan = sample()
        plan.days[0].end_time = "11:00"
        await validate_schedule(plan, REQUEST, AsyncMock(return_value={"duration_minutes": 30, "distance_meters": 1000}))
        self.assertEqual(plan.days[0].schedule.status, "conflict")
        self.assertEqual(len(plan.days[0].attractions), 2)
        self.assertTrue(any("超出" in text for text in plan.days[0].schedule.conflicts))

    async def test_fixed_appointment_late_and_wait(self):
        plan = sample()
        plan.days[0].attractions[1].fixed_start_time = "10:00"
        provider = AsyncMock(return_value={"duration_minutes": 20, "distance_meters": 1000})
        await validate_schedule(plan, REQUEST, provider)
        self.assertTrue(any("晚到" in text for text in plan.days[0].schedule.conflicts))
        plan.days[0].attractions[1].fixed_start_time = "14:00"
        await validate_schedule(plan, REQUEST, provider)
        self.assertEqual(plan.days[0].schedule.items[1].start_time, "14:00")
        self.assertEqual(plan.days[0].schedule.minimum_minutes, 390)

    async def test_missing_coordinates_unknown_and_no_provider_call(self):
        plan = sample()
        plan.days[0].attractions[1].location.longitude = 0
        plan.days[0].attractions[1].location.latitude = 0
        provider = AsyncMock()
        await validate_schedule(plan, REQUEST, provider)
        provider.assert_not_awaited()
        self.assertEqual(plan.days[0].schedule.status, "unverified")

    async def test_meals_and_hotel_in_actual_order(self):
        plan = sample()
        day = plan.days[0]
        from app.models.schemas import Meal, Hotel
        day.hotel = Hotel(name="酒店", location=Location(longitude=120.1, latitude=30.2))
        day.meals = [Meal(type=k, name=k, location=day.hotel.location) for k in ["breakfast", "lunch", "dinner", "snack"]]
        await validate_schedule(plan, REQUEST, AsyncMock(return_value={"duration_minutes": 5, "distance_meters": 100}))
        self.assertEqual([i.id for i in day.schedule.items], [day.hotel.item_id+"-start", day.meals[0].item_id, day.attractions[0].item_id, day.meals[1].item_id, day.attractions[1].item_id, day.meals[3].item_id, day.meals[2].item_id, day.hotel.item_id+"-end"])

    async def test_route_parser_modes_seconds_rounding_and_same_coordinate(self):
        a, b = Location(longitude=120, latitude=30), Location(longitude=120.1, latitude=30)
        for mode, key, path in [("walking", "paths", "direction/walking"), ("driving", "paths", "direction/driving"), ("transit", "transits", "direction/transit/integrated")]:
            with patch("app.services.amap_rest._get_json", new=AsyncMock(return_value={"route": {key: [{"duration": "121", "distance": "750"}]}})) as mock:
                result = await get_route(a, b, "杭州", mode)
                self.assertEqual((result["duration_minutes"], result["distance_meters"]), (3, 750))
                self.assertEqual(mock.call_args.args[0], path)
        with patch("app.services.amap_rest._get_json", new=AsyncMock()) as mock:
            self.assertEqual((await get_route(a, a, "杭州", "walking"))["duration_minutes"], 0)
            mock.assert_not_awaited()
        with patch("app.services.amap_rest._get_json", new=AsyncMock(return_value={"route": {"paths": []}})):
            with self.assertRaises(RuntimeError):
                await get_route(a, b, "杭州", "walking")

    async def test_agent_retries_conflict_then_returns_flagged_draft_with_required_place(self):
        from app.models.schemas import ReferenceCandidate, ReferencePlace
        request = REQUEST.model_copy(update={"daily_end_time": "09:30", "reference_candidates": [ReferenceCandidate(id="required", name="西湖博物馆", priority="required", status="matched", place=ReferencePlace(name="西湖博物馆", longitude=120.15, latitude=30.24))]})
        model = FakeModel([AIMessage(content=json.dumps({"changed": True, "reply": "初稿", "plan": PLAN})) for _ in range(3)])
        agent = make_agent(model)
        agent._check_schedule = validate_schedule
        result, reply, *_ = await agent.run(request, TripPlan(**PLAN), "调整第一天")
        self.assertEqual(model.calls, 3)
        self.assertEqual(result.days[0].attractions[0].name, "西湖博物馆")
        self.assertEqual(result.days[0].schedule.status, "conflict")
        self.assertIn("仍有时间冲突", reply)

    def test_grounding_does_not_silently_drop_fifth_place(self):
        from app.agents.langgraph_planner import LangGraphTripPlanner
        plan = sample(5)
        pois = [{"name": p.name, "address": p.address, "coordinates": [p.location.longitude, p.location.latitude]} for p in plan.days[0].attractions]
        LangGraphTripPlanner.__new__(LangGraphTripPlanner)._ground_selected_places(plan, {"attractions_info": json.dumps(pois), "hotels_info": "[]", "restaurants_info": "[]"})
        self.assertEqual(len(plan.days[0].attractions), 5)

    async def test_revision_preserves_fixed_time_and_user_price(self):
        current = sample(1)
        current.days[0].attractions[0].fixed_start_time = "11:00"
        current.days[0].attractions[0].ticket_price = 0
        current.days[0].attractions[0].cost_status = "confirmed"
        current.days[0].attractions[0].cost_source = "用户填写"
        current.days[0].start_time = "10:00"
        model = FakeModel([AIMessage(content=json.dumps({"changed": True, "reply": "调整", "plan": PLAN}))])
        result, *_ = await make_agent(model).run(REQUEST, current, "调整第一天")
        self.assertEqual(result.days[0].attractions[0].fixed_start_time, "11:00")
        self.assertEqual(result.days[0].attractions[0].ticket_price, 0)
        self.assertEqual(result.days[0].start_time, "10:00")


class CostTests(unittest.TestCase):
    def test_legacy_zero_unknown_positive_estimate_and_explicit_free(self):
        raw = copy.deepcopy(PLAN["days"][0]["attractions"][0])
        raw["ticket_price"] = 0
        self.assertIsNone(Attraction(**raw).ticket_price)
        raw["cost_status"] = "confirmed"
        self.assertEqual(Attraction(**raw).ticket_price, 0)
        raw.pop("cost_status")
        raw["ticket_price"] = 50
        self.assertEqual(Attraction(**raw).cost_status, "estimated")


class ScheduleApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_revalidate_is_versioned_and_rejects_stale_write(self):
        import tempfile
        from pathlib import Path
        from fastapi import FastAPI
        from httpx import AsyncClient, ASGITransport
        from app.api.routes import saved_trips
        from app.services.trip_repository import TripRepository
        from test_saved_trips import OWNER
        with tempfile.TemporaryDirectory() as directory:
            repo = TripRepository(Path(directory) / "trips.sqlite3")
            trip = repo.create(OWNER, REQUEST.model_dump(), PLAN)
            app = FastAPI()
            app.include_router(saved_trips.router)
            app.dependency_overrides[saved_trips.repository] = lambda: repo
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers={"X-Trip-Owner": OWNER}) as client:
                result = await client.post(f"/trips/{trip['id']}/validate", json={"base_version": 1})
                self.assertEqual(result.status_code, 200)
                updated = result.json()
                self.assertEqual(updated["version"], 2)
                self.assertEqual(updated["plan"]["days"][0]["schedule"]["status"], "feasible")
                stale = await client.post(f"/trips/{trip['id']}/validate", json={"base_version": 1})
                self.assertEqual(stale.status_code, 409)
                raw = copy.deepcopy(updated["plan"])
                raw["days"][0]["end_time"] = "09:30"
                saved = await client.post(f"/trips/{trip['id']}/versions", json={"base_version": 2, "origin": "manual", "plan": raw})
                self.assertEqual(saved.status_code, 200)
                self.assertEqual(saved.json()["plan"]["days"][0]["schedule"]["status"], "conflict")
                restored = await client.post(f"/trips/{trip['id']}/restore", json={"base_version": 3, "target_version": 2})
                self.assertEqual(restored.status_code, 200)
                self.assertEqual(restored.json()["plan"]["days"][0]["schedule"]["status"], "feasible")
