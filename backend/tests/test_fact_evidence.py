import asyncio
import json
import unittest
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch
from langchain_core.messages import ToolMessage, AIMessage

from app.agents.langgraph_planner import LangGraphTripPlanner
from app.models.schemas import Meal
from app.services import amap_rest
from app.services.fact_evidence import fact_state, record_fact, needs_refresh, reconcile_manual_facts, fact_report
from app.services.place_facts import apply_provider_facts, enrich_plan
from test_schedule import sample
from test_place_facts import fake_llm

NOW = datetime(2026, 10, 16, 8, tzinfo=timezone.utc)


class EvidenceTests(unittest.IsolatedAsyncioTestCase):
    def test_model_projection_references_only_exact_repeated_candidates(self):
        from app.agents.travel_agent import model_visible_messages, _evidence
        poi = {'poi_id':'same-id','name':'合成博物馆','address':'测试市测试区测试街道一号',
               'coordinates':[120.15,30.24],'category':'科教文化服务;博物馆',
               'open_hours':'09:00-17:00','avg_cost':0,'rating':0,'tel':None,
               'photos':['https://example.com/photo.jpg'],'observed_at':NOW.isoformat()}
        def tool(identity, rows, kind='attractions'):
            return ToolMessage(content=json.dumps({'kind':kind,'keyword':identity,'pois':rows},ensure_ascii=False),tool_call_id=identity,name='search_attractions')
        messages = [tool('search-1',[poi]),tool('search-2',[dict(poi)]),
                    tool('search-3',[{**poi,'open_hours':'10:00-18:00'}]),
                    tool('search-4',[{**poi,'coordinates':[121.15,30.24]}]),
                    tool('search-5',[poi],kind='hotels')]
        before = [m.content for m in messages]
        from app.services.observability import usage_scope
        with usage_scope() as usage:
            visible = model_visible_messages(messages)
        rows = [json.loads(m.content)['pois'][0] for m in visible]
        self.assertEqual(rows[1],{'poi_id':'same-id','details_from_tool_call':'search-1'})
        self.assertEqual(rows[0]['avg_cost'],0)
        self.assertEqual(rows[0]['rating'],0)
        self.assertNotIn('photos',rows[0]);self.assertNotIn('tel',rows[0])
        for index in (2,3,4):self.assertIn('name',rows[index])
        self.assertEqual([m.content for m in messages],before)
        evidence=json.loads(_evidence(messages,None)['attractions_info'])[0]
        self.assertEqual(evidence['photos'],poi['photos'])
        self.assertEqual(evidence['observed_at'],NOW.isoformat())
        self.assertEqual(usage['events']['model_poi_references'],1)
        self.assertEqual(usage['events']['model_tool_source_characters'],sum(map(len,before)))
        self.assertLess(usage['events']['model_tool_input_characters'],usage['events']['model_tool_source_characters'])

    def test_projection_does_not_lengthen_tiny_duplicates_or_change_non_poi_messages(self):
        from app.agents.travel_agent import model_visible_messages
        payload=json.dumps({'kind':'attractions','pois':[{'poi_id':'p'},{'poi_id':'p'}]})
        tiny=ToolMessage(content=payload,tool_call_id='long-tool-call-identity',name='search_attractions')
        invalid=ToolMessage(content='not json',tool_call_id='invalid',name='search_attractions')
        weather=ToolMessage(content='{"kind":"weather","forecasts":[]}',tool_call_id='weather',name='check_weather')
        visible=model_visible_messages([tiny,invalid,weather])
        self.assertEqual(json.loads(visible[0].content)['pois'],[{'poi_id':'p'},{'poi_id':'p'}])
        self.assertIs(visible[1],invalid);self.assertIs(visible[2],weather)

    def meal(self):
        return Meal(name='', restaurant='餐厅', type='lunch', poi_id='r1', open_hours='09:00-20:00',
                    estimated_cost=50, cost_status='estimated')

    def test_age_thresholds_and_value_and_poi_binding(self):
        place = self.meal()
        self.assertEqual(fact_state(place, 'hours', NOW)['status'], 'unknown_age')
        record_fact(place, 'hours', 'amap', (NOW-timedelta(days=6)).isoformat())
        self.assertEqual(fact_state(place, 'hours', NOW)['status'], 'recent_reference')
        self.assertFalse(needs_refresh(place, 'hours', NOW))
        record_fact(place, 'hours', 'amap', (NOW-timedelta(days=7)).isoformat())
        self.assertEqual(fact_state(place, 'hours', NOW)['status'], 'stale_reference')
        self.assertTrue(needs_refresh(place, 'hours', NOW))
        record_fact(place, 'price', 'amap', (NOW-timedelta(days=1)).isoformat())
        self.assertEqual(fact_state(place, 'price', NOW)['status'], 'stale_reference')
        place.estimated_cost = 60
        self.assertEqual(fact_state(place, 'price', NOW)['status'], 'unknown_age')
        place.poi_id = 'other-branch'
        self.assertEqual(fact_state(place, 'hours', NOW)['status'], 'unknown_age')

    def test_missing_invalid_future_or_naive_observation_never_looks_recent(self):
        place = self.meal()
        record_fact(place, 'hours', 'amap')
        self.assertIsNone(place.hours_evidence)
        for stamp in ('bad', '2026-10-16T08:00:00', (NOW+timedelta(seconds=1)).isoformat()):
            record_fact(place, 'hours', 'amap', stamp)
            self.assertEqual(fact_state(place, 'hours', NOW)['status'], 'unknown_age')
        place.open_hours = None
        self.assertEqual(fact_state(place, 'hours', NOW)['status'], 'missing')

    def test_model_and_user_records_never_become_map_verification(self):
        place = self.meal()
        for source, expected in [('model', 'model_estimate'), ('user', 'user_provided')]:
            record_fact(place, 'price', source, (NOW-timedelta(days=30)).isoformat())
            self.assertEqual(fact_state(place, 'price', NOW)['status'], expected)
            self.assertFalse(needs_refresh(place, 'price', NOW))

    def test_manual_edit_cannot_reuse_or_forge_provider_evidence(self):
        plan = sample(1)
        place = plan.days[0].attractions[0]
        place.poi_id = 'a1'; place.identify_place(); place.open_hours = '09:00-17:00'
        record_fact(place, 'hours', 'amap', NOW.isoformat())
        same = plan.model_copy(deep=True)
        same.days[0].attractions[0].hours_evidence.recorded_at = (NOW+timedelta(days=5)).isoformat()
        reconcile_manual_facts(same, plan)
        self.assertEqual(same.days[0].attractions[0].hours_evidence, place.hours_evidence)
        changed = plan.model_copy(deep=True)
        changed.days[0].attractions[0].open_hours = '10:00-16:00'
        reconcile_manual_facts(changed, plan)
        self.assertEqual(changed.days[0].attractions[0].hours_evidence.source, 'user')
        self.assertEqual(plan.days[0].attractions[0].open_hours, '09:00-17:00')

    async def test_poi_cache_preserves_original_observation_time(self):
        raw = {'pois': [{'id': 'r1', 'name': '餐厅', 'location': '120,30', 'biz_ext': {'open_time': '09:00-20:00'}}]}
        with patch.object(amap_rest, '_poi_cache', OrderedDict()), patch.object(amap_rest, '_get_json', new=AsyncMock(return_value=raw)) as query:
            first = await amap_rest.search_pois('杭州', '餐厅')
            original = first[0]['observed_at']
            first[0]['observed_at'] = 'tampered'
            second = await amap_rest.search_pois('杭州', '餐厅')
            self.assertEqual(second[0]['observed_at'], original)
            detail = await amap_rest.place_detail('r1')
            again = await amap_rest.place_detail('r1')
            self.assertEqual(detail['observed_at'], again['observed_at'])
            self.assertEqual(query.await_count, 2)

    async def test_refresh_updates_old_hours_and_price_without_overwriting_user_price(self):
        plan = sample(1)
        plan.days[0].attractions[0].ticket_price = 0
        plan.days[0].attractions[0].cost_status = 'confirmed'
        meal = self.meal(); plan.days[0].meals = [meal]
        old = (datetime.now(timezone.utc)-timedelta(days=10)).isoformat()
        record_fact(meal, 'hours', 'amap', old); record_fact(meal, 'price', 'amap', old)
        stamp = datetime.now(timezone.utc).isoformat()
        lookup = AsyncMock(return_value={'poi_id': 'r1', 'open_hours': '10:00-18:00', 'avg_cost': 88, 'observed_at': stamp})
        await enrich_plan(plan, fake_llm('{}'), lookup)
        self.assertEqual((meal.open_hours, meal.estimated_cost), ('10:00-18:00', 88))
        self.assertEqual(fact_state(meal, 'hours')['recorded_at'], stamp)
        meal.cost_status = 'confirmed'; meal.estimated_cost = 42
        record_fact(meal, 'price', 'user')
        record_fact(meal, 'hours', 'amap', old)
        await enrich_plan(plan, fake_llm('{}'), lookup)
        self.assertEqual(meal.estimated_cost, 42)

    async def test_failed_wrong_branch_or_missing_time_does_not_freshen_old_information(self):
        plan = sample(1); meal = self.meal(); plan.days[0].meals = [meal]
        old = (datetime.now(timezone.utc)-timedelta(days=10)).isoformat()
        record_fact(meal, 'hours', 'amap', old)
        for lookup in (AsyncMock(side_effect=RuntimeError('offline')), AsyncMock(return_value={'poi_id': 'wrong', 'open_hours': '10:00-18:00'})):
            summary = await enrich_plan(plan, fake_llm('{}'), lookup)
            self.assertEqual(summary['failed'], 1)
            self.assertEqual(meal.hours_evidence.recorded_at, old)
        lookup = AsyncMock(return_value={'poi_id': 'r1', 'open_hours': '10:00-18:00'})
        await enrich_plan(plan, fake_llm('{}'), lookup)
        self.assertEqual(fact_state(meal, 'hours')['status'], 'unknown_age')

    async def test_explicit_user_hours_survive_refresh_for_a_missing_price(self):
        plan = sample(1); meal = self.meal(); plan.days[0].meals = [meal]
        meal.estimated_cost, meal.cost_status = None, 'unknown'
        record_fact(meal, 'hours', 'user')
        lookup = AsyncMock(return_value={'poi_id': 'r1', 'open_hours': '10:00-18:00', 'avg_cost': 88,
                                        'observed_at': datetime.now(timezone.utc).isoformat()})
        await enrich_plan(plan, fake_llm('{}'), lookup)
        self.assertEqual(meal.open_hours, '09:00-20:00')
        self.assertEqual(meal.hours_evidence.source, 'user')
        self.assertEqual(meal.estimated_cost, 88)

    async def test_detail_queries_are_deduplicated_and_concurrency_is_bounded(self):
        plan = sample(1)
        plan.days[0].meals = [self.meal().model_copy(update={'poi_id': f'r{i}'}) for i in range(9)]
        plan.days[0].meals.append(plan.days[0].meals[0].model_copy(deep=True))
        active = peak = calls = 0
        async def lookup(id):
            nonlocal active, peak, calls
            active += 1; calls += 1; peak = max(peak, active)
            await asyncio.sleep(.001)
            active -= 1
            return {'poi_id': id}
        await enrich_plan(plan, fake_llm('{}'), lookup)
        self.assertEqual(calls, 9)
        self.assertLessEqual(peak, 4)

    def test_grounding_drops_model_business_claims_not_returned_by_provider(self):
        plan = sample(1); place = plan.days[0].attractions[0]
        place.open_hours = '全天24小时'
        place.poi_id = 'a1'
        record_fact(place, 'hours', 'model')
        planner = LangGraphTripPlanner.__new__(LangGraphTripPlanner)
        poi = {'name': place.name, 'poi_id': 'a1', 'address': place.address, 'coordinates': [120.15, 30.24]}
        planner._ground_selected_places(plan, {'attractions_info': json.dumps([poi])})
        self.assertIsNone(place.open_hours)
        self.assertIsNone(place.hours_evidence)
        self.assertEqual(fact_report(plan)['missing'], 2)

    def test_nonfinite_provider_business_values_are_missing(self):
        parsed = amap_rest._parse_pois({'pois': [{'id': 'r1', 'location': '120,30', 'biz_ext': {'cost': 'inf', 'rating': True}}]})
        self.assertNotIn('avg_cost', parsed[0])
        self.assertNotIn('rating', parsed[0])

    def test_model_projection_keeps_business_values_and_full_server_evidence(self):
        from app.agents.travel_agent import model_visible_messages
        payload = {'kind': 'attractions', 'pois': [{'poi_id': 'a1', 'name': '景点', 'open_hours': '09:00-17:00',
                    'observed_at': NOW.isoformat(), 'avg_cost': 50}]}
        message = ToolMessage(content=json.dumps(payload, ensure_ascii=False), tool_call_id='search-1', name='search_attractions')
        original = message.content
        projected = model_visible_messages([message])[0]
        self.assertEqual(message.content, original)
        self.assertEqual(projected.tool_call_id, 'search-1')
        data = json.loads(projected.content)['pois'][0]
        self.assertNotIn('observed_at', data)
        self.assertEqual(data['open_hours'], '09:00-17:00')
        self.assertEqual(data['avg_cost'], 50)
        self.assertEqual(json.loads(message.content)['pois'][0]['observed_at'], NOW.isoformat())

    async def test_agent_retains_server_observation_after_model_input_is_projected(self):
        from test_travel_agent import FakeModel, make_agent, PLAN, POI, REQUEST
        class TrackingModel(FakeModel):
            async def ainvoke(self, messages):
                self.last_input = messages
                return await super().ainvoke(messages)
        model = TrackingModel([
            AIMessage(content='', tool_calls=[{'name': 'search_attractions', 'args': {'keyword': '博物馆'}, 'id': 'search-1'}]),
            AIMessage(content=json.dumps({'changed': True, 'reply': '初稿', 'plan': PLAN}, ensure_ascii=False))])
        poi = {**POI, 'poi_id': 'a1', 'open_hours': '09:00-17:00', 'observed_at': NOW.isoformat()}
        with patch('app.agents.travel_agent.search_pois', new=AsyncMock(return_value=[poi])):
            plan, *_ = await make_agent(model).run(REQUEST)
        tools = [m for m in model.last_input if isinstance(m, ToolMessage)]
        self.assertTrue(tools)
        self.assertNotIn('observed_at', tools[0].content)
        self.assertEqual(plan.days[0].attractions[0].hours_evidence.recorded_at, NOW.isoformat())


if __name__ == '__main__':
    unittest.main()
