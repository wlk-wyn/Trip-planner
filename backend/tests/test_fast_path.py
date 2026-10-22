"""Compact model output, new-trip background runs and patch-based conflict repair."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from langchain_core.messages import AIMessage

from app.agents.travel_agent import expand_compact_plan, _evidence
from app.models.schemas import TripPlan, DaySchedule
from app.services.planning_runs import start_create_run, read_run, execute, _tasks
from app.services.trip_repository import TripRepository
from test_travel_agent import REQUEST, PLAN, make_agent, FakeModel
from test_saved_trips import OWNER, OTHER

POI = {"poi_id": "B001", "name": "西湖博物馆", "address": "南山路89号", "coordinates": [120.15, 30.24]}
COMPACT = {"overall_suggestions": "慢慢逛。", "days": [
    {"date": "2026-10-03", "description": "博物馆", "attractions": [{"poi_id": "B001", "visit_duration": 90, "description": "看展"}], "meals": []}]}


def tool_call():
    return AIMessage(content="", tool_calls=[{"name": "search_attractions", "args": {"keyword": "博物馆"}, "id": "call-1"}])


class CompactPlanTests(unittest.IsolatedAsyncioTestCase):
    def test_ids_are_filled_from_search_results(self):
        evidence = {"by_id": {"attractions": {"B001": POI}, "hotels": {}, "restaurants": {}}}
        plan = expand_compact_plan(COMPACT, evidence, REQUEST)
        attraction = plan["days"][0]["attractions"][0]
        self.assertEqual(attraction["name"], "西湖博物馆")
        self.assertEqual(attraction["location"], {"longitude": 120.15, "latitude": 30.24})
        self.assertEqual((plan["city"], plan["start_date"]), ("杭州", "2026-10-03"))

    def test_unknown_id_is_rejected_not_invented(self):
        evidence = {"by_id": {"attractions": {}, "hotels": {}, "restaurants": {}}}
        with self.assertRaisesRegex(ValueError, "不在本次搜索结果中"):
            expand_compact_plan(COMPACT, evidence, REQUEST)

    async def test_agent_accepts_compact_plan(self):
        model = FakeModel([tool_call(), AIMessage(content=json.dumps({"changed": True, "reply": "好了", "plan": COMPACT}, ensure_ascii=False))])
        with patch("app.agents.travel_agent.search_pois", new=AsyncMock(return_value=[POI])):
            plan, reply, _, _ = await make_agent(model).run(REQUEST)
        self.assertEqual(plan.days[0].attractions[0].poi_id, "B001")
        self.assertEqual(plan.days[0].attractions[0].address, "南山路89号")

    async def test_conflict_repair_asks_for_patch_and_applies_it(self):
        two = {"overall_suggestions": "", "days": [{"date": "2026-10-03", "attractions": [
            {"poi_id": "B001", "visit_duration": 90}, {"poi_id": "B002", "visit_duration": 600}]}]}
        second = {**POI, "poi_id": "B002", "name": "灵隐寺", "coordinates": [120.10, 30.24]}

        async def check(plan, request, progress=None):
            for day in plan.days:
                long_visit = any(a.visit_duration > 300 for a in day.attractions)
                day.schedule = DaySchedule(status="conflict" if long_visit else "feasible", window_start="09:00", window_end="21:00",
                                           minimum_minutes=0, available_minutes=720, conflicts=["超出可用时间"] if long_visit else [])
        scripted = [tool_call(), AIMessage(content=json.dumps({"changed": True, "reply": "初稿", "plan": two}, ensure_ascii=False))]
        prompts = []

        async def ainvoke(messages):
            prompts.append(messages[-1].content)
            if len(prompts) <= len(scripted):
                return scripted[len(prompts) - 1]
            # The repair prompt lists the draft's visit IDs; reply with a one-operation patch.
            item_id = json.loads(messages[-1].content.split("冲突日期的当前景点：")[1])[0]["attractions"][1]["item_id"]
            return AIMessage(content=json.dumps({"changed": True, "reply": "缩短停留", "patch": {"operations": [
                {"action": "update_visit", "date": "2026-10-03", "kind": "attraction", "item_id": item_id, "visit_duration": 120}]}}))
        model = FakeModel([])
        model.ainvoke = ainvoke
        agent = make_agent(model)
        agent._check_schedule = check
        with patch("app.agents.travel_agent.search_pois", new=AsyncMock(side_effect=lambda city, keyword, limit: [POI, second])):
            plan, reply, _, _ = await agent.run(REQUEST)
        self.assertIn("只返回 patch", prompts[-1])
        self.assertEqual([a.visit_duration for a in plan.days[0].attractions], [90, 120])
        self.assertEqual(reply, "缩短停留")


class CreateRunTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        ticket_patch = patch('app.services.planning_runs._estimate_tickets', new=AsyncMock())
        ticket_patch.start()
        self.addCleanup(ticket_patch.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = TripRepository(Path(self.tmp.name) / "trips.sqlite3")

    async def asyncTearDown(self):
        import asyncio
        tasks = list(_tasks.values())
        for task in tasks: task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.tmp.cleanup()

    async def test_create_run_saves_trip_once_and_is_owner_scoped(self):
        request_id = str(uuid4())
        with patch("app.services.planning_runs.launch"):
            run = start_create_run(self.repo, OWNER, REQUEST.model_dump(mode="json"), request_id)
            again = start_create_run(self.repo, OWNER, REQUEST.model_dump(mode="json"), request_id)
        self.assertEqual(run["id"], again["id"])
        with self.assertRaises(KeyError):
            read_run(self.repo, OTHER, request_id)
        stages = []
        fake = AsyncMock(return_value=TripPlan(**PLAN))

        async def generate(request, run_id, progress):
            await progress("核实交通 1/1 段", 1, 1)
            stages.append(read_run(self.repo, OWNER, run_id)["stage"])
            return await fake()
        with patch("app.services.planning_runs._generate", new=generate):
            await execute(self.repo, OWNER, request_id)
            await execute(self.repo, OWNER, request_id)
        result = read_run(self.repo, OWNER, request_id)
        self.assertEqual(stages, ["核实交通 1/1 段"])
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["trip_id"], result["result"]["id"])
        self.assertEqual(len(self.repo.list(OWNER)), 1)
        self.assertEqual(fake.await_count, 1)

    async def test_failed_generation_reports_reason(self):
        request_id = str(uuid4())
        with patch("app.services.planning_runs.launch"):
            start_create_run(self.repo, OWNER, REQUEST.model_dump(mode="json"), request_id)
        with patch("app.services.planning_runs._generate", new=AsyncMock(side_effect=ValueError("请先确认必去地点"))):
            await execute(self.repo, OWNER, request_id)
        result = read_run(self.repo, OWNER, request_id)
        self.assertEqual((result["status"], result["error"]), ("failed", "请先确认必去地点"))
        self.assertEqual(self.repo.list(OWNER), [])


if __name__ == "__main__":
    unittest.main()


class CreateRunApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_post_then_stream_events_until_trip_is_saved(self):
        from fastapi import FastAPI
        from httpx import ASGITransport, AsyncClient
        from app.api.routes import saved_trips
        with tempfile.TemporaryDirectory() as directory:
            repo = TripRepository(Path(directory) / "api.sqlite3")
            app = FastAPI()
            app.include_router(saved_trips.router, prefix="/api")
            app.dependency_overrides[saved_trips.repository] = lambda: repo

            async def generate(request, run_id, progress):
                await progress("查找真实地点")
                return TripPlan(**PLAN)
            with patch("app.services.planning_runs._generate", new=generate), patch("app.services.planning_runs._estimate_tickets", new=AsyncMock()):
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers={"X-Trip-Owner": OWNER}) as client:
                    started = await client.post("/api/trips/plan-runs", json={"request": REQUEST.model_dump(mode="json"), "request_id": str(uuid4())})
                    self.assertEqual(started.status_code, 202)
                    events = []
                    async with client.stream("GET", f"/api/trips/runs/{started.json()['id']}/events") as stream:
                        async for line in stream.aiter_lines():
                            if line.startswith("data: "):
                                events.append(json.loads(line[6:]))
                    other = await client.get(f"/api/trips/plan-runs/{started.json()['id']}", headers={"X-Trip-Owner": OTHER})
            self.assertEqual(events[-1]["status"], "succeeded")
            self.assertEqual(events[-1]["result"]["plan"]["city"], "杭州")
            self.assertEqual(other.status_code, 404)
