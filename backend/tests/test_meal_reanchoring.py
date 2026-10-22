import unittest
from unittest.mock import AsyncMock, patch

from app.models.schemas import Meal, Location
from app.agents.langgraph_planner import LangGraphTripPlanner
from test_schedule import sample


class MealReanchoringTests(unittest.IsolatedAsyncioTestCase):
    def setup_plan(self, **options):
        plan = sample(2)
        meal = Meal(type='dinner', name='', restaurant='远处家常菜饭店', poi_id='old', route_matched=True,
                    location=Location(longitude=121.0, latitude=30.24), **options)
        plan.days[0].meals = [meal]
        plan.days[0].identify_visits()
        return plan, meal

    async def test_new_trip_repair_reanchors_only_auto_selected_remote_meal(self):
        planner = LangGraphTripPlanner.__new__(LangGraphTripPlanner)
        plan, old = self.setup_plan()
        anchor = plan.days[0].attractions[-1].location
        candidate = {'poi_id': 'new', 'name': '近处家常菜饭店', 'address': '合成测试地址', 'coordinates': [anchor.longitude, anchor.latitude]}
        with patch('app.agents.langgraph_planner.search_nearby_restaurants', new=AsyncMock(return_value=[candidate])) as query:
            await planner._align_meals_to_route(plan, repair=True)
        self.assertEqual(len(plan.days[0].meals), 1)  # do not fill other unrequested slots during repair
        self.assertEqual(plan.days[0].meals[0].poi_id, 'new')
        self.assertTrue(plan.days[0].meals[0].route_matched)
        query.assert_awaited_once()

    async def test_locked_explicit_or_non_auto_meals_are_never_reanchored(self):
        planner = LangGraphTripPlanner.__new__(LangGraphTripPlanner)
        for kind in ['locked', 'protected', 'manual']:
            plan, meal = self.setup_plan()
            if kind == 'locked': meal.locked = True
            if kind == 'manual': meal.route_matched = False
            before = plan.days[0].meals[0].model_dump()
            protected = {meal.item_id} if kind == 'protected' else set()
            with patch('app.agents.langgraph_planner.search_nearby_restaurants', new=AsyncMock()) as query:
                await planner._align_meals_to_route(plan, repair=True, protected_ids=protected)
            query.assert_not_awaited()
            self.assertEqual(plan.days[0].meals[0].model_dump(), before)

    async def test_already_nearby_auto_meal_keeps_original_identity(self):
        planner = LangGraphTripPlanner.__new__(LangGraphTripPlanner)
        plan, meal = self.setup_plan()
        meal.location = plan.days[0].attractions[-1].location.model_copy()
        with patch('app.agents.langgraph_planner.search_nearby_restaurants', new=AsyncMock()) as query:
            await planner._align_meals_to_route(plan, repair=True)
        query.assert_not_awaited()
        self.assertEqual(plan.days[0].meals[0].item_id, meal.item_id)

    async def test_failed_search_does_not_remove_a_meal_to_hide_route_failure(self):
        planner = LangGraphTripPlanner.__new__(LangGraphTripPlanner)
        plan, meal = self.setup_plan()
        before = meal.model_dump()
        with patch('app.agents.langgraph_planner.search_nearby_restaurants', new=AsyncMock(return_value=[])):
            await planner._align_meals_to_route(plan, repair=True)
        self.assertEqual([m.model_dump() for m in plan.days[0].meals], [before])
