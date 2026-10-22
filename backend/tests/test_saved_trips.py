import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from langchain_core.messages import AIMessage
from app.api.routes import saved_trips
from app.models.schemas import ReferenceSource, ReferenceCandidate, ReferencePlace, TripRequest, TripPlan
from app.services.reference_service import analyze_references
from app.services.trip_repository import TripRepository, VersionConflict
from app.agents.travel_agent import check_reference_constraints
from test_travel_agent import PLAN, REQUEST, make_agent

OWNER = "1fa9fe7d-3432-4cd7-ab80-7e7c290e160a"
OTHER = "2fa9fe7d-3432-4cd7-ab80-7e7c290e160a"


class RepositoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "trips.sqlite3"
        self.repo = TripRepository(self.path)
        self.trip = self.repo.create(OWNER, REQUEST.model_dump(), PLAN)

    def tearDown(self):
        self.tmp.cleanup()

    def test_survives_new_repository_and_preserves_multiple_trips(self):
        second = self.repo.create(OWNER, REQUEST.model_dump(), PLAN)
        reopened = TripRepository(self.path)
        self.assertEqual(reopened.read(OWNER, self.trip["id"])["plan"], PLAN)
        self.assertEqual(len(reopened.list(OWNER)), 2)
        self.assertNotEqual(second["id"], self.trip["id"])

    def test_atomic_versions_restore_and_stale_write(self):
        changed = copy.deepcopy(PLAN)
        changed["days"][0]["attractions"][0]["visit_duration"] = 45
        updated = self.repo.commit(OWNER, self.trip["id"], 1, changed, "manual")
        self.assertEqual(updated["version"], 2)
        with self.assertRaises(VersionConflict):
            self.repo.commit(OWNER, self.trip["id"], 1, PLAN, "diy")
        restored = self.repo.restore(OWNER, self.trip["id"], 2, 1)
        self.assertEqual(restored["plan"], PLAN)
        self.assertEqual(restored["version"], 3)
        self.assertEqual(len(self.repo.versions(OWNER, self.trip["id"])), 3)

    def test_owner_isolation(self):
        self.assertEqual(self.repo.list(OTHER), [])
        with self.assertRaises(KeyError):
            self.repo.restore(OTHER, self.trip["id"], 1, 1)
        with self.assertRaises(KeyError):
            self.repo.versions(OTHER, self.trip["id"])

    def test_question_saves_chat_without_new_version(self):
        result = self.repo.commit(OWNER, self.trip["id"], 1, PLAN, "agent", [{"role": "assistant", "content": "回答"}])
        self.assertEqual(result["version"], 1)
        self.assertEqual(len(self.repo.versions(OWNER, self.trip["id"])), 1)
        self.assertEqual(result["chat"][0]["content"], "回答")


class ReferenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_source_provenance_merge_and_hallucination_filter(self):
        sources = [ReferenceSource(id="a", text="去西湖博物馆"), ReferenceSource(id="b", text="西湖博物馆很适合参观")]
        model = AsyncMock()
        model.ainvoke.return_value = AIMessage(content=json.dumps({"places": [
            {"name": "西湖博物馆", "source_ids": ["a", "b"]}, {"name": "假地点", "source_ids": ["a"]}]}))
        poi = {"poi_id": "real-1", "name": "西湖博物馆", "address": "南山路89号", "coordinates": [120.15, 30.24]}
        with patch("app.services.reference_service.search_pois", new=AsyncMock(return_value=[poi])):
            result = await analyze_references("杭州", sources, model)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].source_ids, ["a", "b"])
        self.assertEqual(result[0].place.poi_id, "real-1")

    async def test_same_name_branches_need_confirmation_and_map_failure_retains_candidate(self):
        source = ReferenceSource(id="a", text="想去咖啡馆")
        model = AsyncMock()
        model.ainvoke.return_value = AIMessage(content='{"places":[{"name":"咖啡馆","source_ids":["a"]}]}')
        pois = [{"poi_id": str(i), "name": "咖啡馆", "address": str(i), "coordinates": [120.1+i/100, 30.2]} for i in range(2)]
        with patch("app.services.reference_service.search_pois", new=AsyncMock(return_value=pois)):
            result = await analyze_references("杭州", [source], model)
        self.assertEqual(result[0].status, "ambiguous")
        self.assertIsNone(result[0].place)
        with patch("app.services.reference_service.search_pois", new=AsyncMock(side_effect=RuntimeError())):
            result = await analyze_references("杭州", [source], model)
        self.assertEqual(result[0].status, "unmatched")
        self.assertEqual(result[0].source_ids, ["a"])

    def test_required_and_ignored_constraints(self):
        request = REQUEST.model_copy(update={"reference_candidates": [ReferenceCandidate(id="x", name="不在行程", priority="required")]})
        with self.assertRaisesRegex(ValueError, "必去"):
            check_reference_constraints(TripPlan(**PLAN), request)
        request.reference_candidates = [ReferenceCandidate(id="x", name="西湖博物馆", priority="ignored")]
        with self.assertRaisesRegex(ValueError, "忽略"):
            check_reference_constraints(TripPlan(**PLAN), request)

    async def test_revision_keeps_reference_content_in_model_context(self):
        model = AsyncMock()
        model.bind_tools = lambda tools, **kwargs: model
        model.ainvoke.return_value = AIMessage(content='{"changed":false,"reply":"保留攻略","plan":null}')
        request = REQUEST.model_copy(update={"reference_content": "攻略提到早上参观"})
        await make_agent(model).run(request, TripPlan(**PLAN), "参考原攻略")
        self.assertTrue(any("攻略提到早上参观" in str(m.content) for m in model.ainvoke.await_args.args[0]))


    def test_required_place_rejects_same_name_wrong_branch(self):
        candidate = ReferenceCandidate(id="a", name="西湖博物馆", priority="required", status="matched",
            place=ReferencePlace(poi_id="branch", name="西湖博物馆", longitude=121.1, latitude=31.1))
        request = REQUEST.model_copy(update={"reference_candidates": [candidate]})
        with self.assertRaisesRegex(ValueError, "必去"):
            check_reference_constraints(TripPlan(**PLAN), request)

    def test_grounding_keeps_selected_same_name_poi(self):
        agent = make_agent(AsyncMock())
        plan = TripPlan(**PLAN)
        plan.days[0].attractions[0].poi_id = "selected"
        evidence = {"attractions_info": json.dumps([
            {"poi_id": "selected", "name": "西湖博物馆", "address": "正确分馆", "coordinates": [120.15, 30.24]},
            {"poi_id": "other", "name": "西湖博物馆", "address": "其他分馆", "coordinates": [121.1, 31.1]}])}
        agent.planner._ground_selected_places(plan, evidence)
        self.assertEqual(plan.days[0].attractions[0].address, "正确分馆")
        self.assertEqual(plan.days[0].attractions[0].poi_id, "selected")

    async def test_named_day_revision_preserves_other_day_exactly(self):
        from test_travel_agent import FakeModel
        original = copy.deepcopy(PLAN)
        original["end_date"] = "2026-10-04"
        second = copy.deepcopy(original["days"][0])
        second.update(date="2026-10-04", day_index=1)
        original["days"].append(second)
        current = TripPlan(**original)
        current.days[0].description = "用户手动写的备注"
        updated = copy.deepcopy(original)
        for day in updated["days"]:
            day["attractions"][0]["visit_duration"] = 45
        model = FakeModel([AIMessage(content=json.dumps({"changed": True, "reply": "已调整", "plan": updated}))])
        request = REQUEST.model_copy(update={"end_date": "2026-10-04", "travel_days": 2})
        result, *_ = await make_agent(model).run(request, current, "第二天轻松一点")
        self.assertEqual(result.days[0], current.days[0])
        self.assertEqual(result.days[1].attractions[0].visit_duration, 45)


class SavedTripApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = TripRepository(Path(self.tmp.name) / "api.sqlite3")
        app = FastAPI()
        app.include_router(saved_trips.router, prefix="/api")
        app.dependency_overrides[saved_trips.repository] = lambda: self.repo
        self.client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers={"X-Trip-Owner": OWNER})

    async def asyncTearDown(self):
        await self.client.aclose()
        self.tmp.cleanup()

    async def test_closure_preview_save_and_restore_preserve_order_and_version_safety(self):
        from app.models.schemas import Meal, Location
        from app.services.conflict_fix import propose_fixes
        from app.services.schedule_validator import validate_schedule, stops_for_day
        from test_weather_and_arrange import two_day_plan
        plan = two_day_plan()
        day = plan.days[0]
        day.attractions[0].open_hours = '周六闭馆'
        day.meals = [Meal(type='lunch', name='合成午餐', restaurant='测试餐厅',
                          location=Location(longitude=120.15, latitude=30.24))]
        async def validate(candidate, request):
            return await validate_schedule(candidate, request, AsyncMock(return_value={
                'duration_minutes': 5, 'distance_meters': 100}))
        await validate(plan, REQUEST)
        original_order = [stop['id'] for stop in stops_for_day(day)]
        removed = day.attractions[0].item_id
        expected_order = [identity for identity in original_order if identity != removed]
        original = self.repo.create(OWNER, REQUEST.model_dump(), plan.model_dump())
        url = '/api/trips/' + original['id']
        async def preview(candidate, request, index):
            return await propose_fixes(candidate, request, index, validate)
        with patch('app.services.conflict_fix.propose_fixes', side_effect=preview), patch.object(saved_trips, 'validate_schedule', side_effect=validate):
            response = await self.client.post(url + '/days/' + day.date + '/fixes', json={'base_version': 1})
            self.assertEqual(response.status_code, 200, response.text)
            proposal = next(p for p in response.json()['proposals'] if p['feasible'])
            self.assertEqual(self.repo.read(OWNER, original['id'])['version'], 1)
            edited = plan.model_dump()
            edited['days'][0] = proposal['day']
            # A preview is not trusted as a saved validation result.
            edited['days'][0]['schedule']['items'] = []
            saved = await self.client.post(url + '/versions', json={'base_version': 1, 'origin': 'fix', 'plan': edited})
            self.assertEqual(saved.status_code, 200, saved.text)
            current = saved.json()
            self.assertEqual(current['version'], 2)
            changed = current['plan']['days'][0]
            self.assertEqual(changed['visit_order'], expected_order)
            self.assertEqual([item['id'] for item in changed['schedule']['items']], expected_order)
            self.assertEqual(current['plan']['days'][1], original['plan']['days'][1])
            stale = await self.client.post(url + '/versions', json={'base_version': 1, 'origin': 'fix', 'plan': edited})
            self.assertEqual(stale.status_code, 409)
            restored = await self.client.post(url + '/restore', json={'base_version': 2, 'target_version': 1})
            self.assertEqual(restored.status_code, 200, restored.text)
            self.assertEqual(restored.json()['version'], 3)
            self.assertEqual(restored.json()['plan'], original['plan'])

    async def test_create_edit_restore_and_owner_access(self):
        response = await self.client.post("/api/trips", json={"request": REQUEST.model_dump(), "plan": PLAN})
        self.assertEqual(response.status_code, 200)
        trip = response.json()
        url = "/api/trips/" + trip["id"]
        edited = copy.deepcopy(trip["plan"])
        edited["overall_suggestions"] = "新建议"
        response = await self.client.post(url + "/versions", json={"plan": edited, "base_version": 1, "origin": "diy"})
        self.assertEqual(response.json()["version"], 2)
        stale = await self.client.post(url + "/versions", json={"plan": edited, "base_version": 1})
        self.assertEqual(stale.status_code, 409)
        other = await self.client.get(url, headers={"X-Trip-Owner": OTHER})
        self.assertEqual(other.status_code, 404)
        restored = await self.client.post(url + "/restore", json={"base_version": 2, "target_version": 1})
        self.assertEqual(restored.json()["plan"], trip["plan"])
        self.assertEqual(restored.json()["version"], 3)

    async def test_cost_preview_is_read_only_owner_scoped_and_rejects_stale_version(self):
        trip = (await self.client.post('/api/trips', json={'request': REQUEST.model_dump(), 'plan': PLAN})).json()
        url = '/api/trips/' + trip['id']
        body = {'base_version': 1, 'context': {'party_size': 2, 'room_count': 1, 'destination_budget': 1000}}
        response = await self.client.post(url + '/cost-preview', json=body)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['cost_report']['context']['party_size'], 2)
        self.assertEqual(response.json()['version'], 1)
        self.assertEqual((await self.client.get(url)).json(), trip)
        checks = (await self.client.get(url + '/checks')).json()
        self.assertEqual(checks['cost_report']['context']['party_size'], 1)  # preview is not a hidden save
        other = await self.client.post(url + '/cost-preview', json=body, headers={'X-Trip-Owner': OTHER})
        self.assertEqual(other.status_code, 404)
        body['base_version'] = 2
        self.assertEqual((await self.client.post(url + '/cost-preview', json=body)).status_code, 409)
        body['context']['party_size'] = True
        self.assertEqual((await self.client.post(url + '/cost-preview', json=body)).status_code, 422)

    async def test_manual_hours_change_has_user_origin_and_does_not_inherit_a_map_timestamp(self):
        from app.services.fact_evidence import fingerprint
        trip = (await self.client.post('/api/trips', json={'request': REQUEST.model_dump(), 'plan': PLAN})).json()
        url = '/api/trips/' + trip['id']
        edited = copy.deepcopy(trip['plan'])
        place = edited['days'][0]['attractions'][0]
        place['open_hours'] = '10:00-16:00'
        place['hours_evidence'] = {'source': 'amap', 'recorded_at': '2099-01-01T00:00:00Z',
                                   'poi_id': '', 'value_hash': fingerprint(place['open_hours'])}
        with patch('app.api.routes.saved_trips.validate_schedule', new=AsyncMock()):
            response = await self.client.post(url + '/versions', json={'plan': edited, 'base_version': 1})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['plan']['days'][0]['attractions'][0]['hours_evidence']['source'], 'user')
        report = (await self.client.get(url + '/checks')).json()['fact_report']
        self.assertEqual(report['items'][0]['hours']['status'], 'user_provided')

    async def test_budget_checks_are_read_only_versioned_and_owner_scoped(self):
        trip = (await self.client.post('/api/trips', json={'request': REQUEST.model_dump(), 'plan': PLAN})).json()
        url = '/api/trips/' + trip['id']
        result = await self.client.get(url + '/checks')
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()['version'], 1)
        self.assertEqual(result.json()['days'][0]['status'], 'unchecked')
        self.assertEqual((await self.client.get(url)).json()['plan'], trip['plan'])
        self.assertEqual((await self.client.get(url + '/checks', headers={'X-Trip-Owner': OTHER})).status_code, 404)

    async def test_saved_chat_uses_original_sources_and_keeps_plan_on_error(self):
        request = REQUEST.model_copy(update={"reference_sources": [ReferenceSource(id="a", text="原攻略")], "reference_content": "原攻略"})
        trip = (await self.client.post("/api/trips", json={"request": request.model_dump(), "plan": PLAN})).json()
        agent = AsyncMock()
        agent.run.return_value = (TripPlan(**trip["plan"]), "回答", False, [])
        with patch("app.api.routes.saved_trips.get_travel_agent", return_value=agent):
            response = await self.client.post(f"/api/trips/{trip['id']}/chat", json={"message": "攻略是什么", "base_version": 1})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(agent.run.await_args.args[0].reference_sources[0].text, "原攻略")
        self.assertEqual(response.json()["version"], 1)
        agent.run.side_effect = RuntimeError("failed")
        with patch("app.api.routes.saved_trips.get_travel_agent", return_value=agent):
            response = await self.client.post(f"/api/trips/{trip['id']}/chat", json={"message": "修改", "base_version": 1})
        self.assertEqual(response.status_code, 502)
        self.assertEqual(self.repo.read(OWNER, trip["id"])["version"], 1)


if __name__ == "__main__":
    unittest.main()
