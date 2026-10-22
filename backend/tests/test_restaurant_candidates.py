import unittest
from unittest.mock import AsyncMock, patch
from app.models.schemas import Meal
from app.services.restaurant_candidates import automatic_restaurant
from app.agents.langgraph_planner import LangGraphTripPlanner
from test_schedule import sample


class RestaurantCandidateTests(unittest.IsolatedAsyncioTestCase):
    def test_extra_access_and_closure_labels_are_not_automatic_choices(self):
        for name in ('人民大会堂宴会厅','某酒店宴会中心','单位职工食堂','园区员工食堂',
                     '北京一零一中学食堂','清华大学紫荆园食堂','某学院校园餐厅','新晟品轩画框','某某广告制作'):
            self.assertFalse(automatic_restaurant({'name':name},'dinner'))
        self.assertFalse(automatic_restaurant({'name':'餐厅','open_hours':'暂停营业'},'dinner'))
        for name in ('木马食堂','家常菜饭店','某酒店自助餐厅','南门铜锅涮肉','画框主题餐厅'):
            self.assertTrue(automatic_restaurant({'name':name},'dinner'))

    def test_initial_selector_uses_the_same_access_filter_as_repairs(self):
        day=sample(1).days[0];anchor=day.attractions[0].location
        def poi(id,name):return {'poi_id':id,'name':name,'address':'测试地址','coordinates':[anchor.longitude,anchor.latitude]}
        choices=[poi('restricted','某酒店宴会厅'),poi('public','家常菜饭店')]
        pending=LangGraphTripPlanner._place_nearby_meals([(day,'dinner',anchor)],[choices],set(),2)
        self.assertEqual(pending,[])
        self.assertEqual(day.meals[0].poi_id,'public')

    async def test_explicit_user_venue_is_preserved_instead_of_auto_replaced(self):
        plan=sample(1);day=plan.days[0]
        venue=Meal(type='dinner',name='',restaurant='明确选择的宴会厅',location=day.attractions[0].location)
        day.meals=[venue];day.identify_visits()
        planner=LangGraphTripPlanner.__new__(LangGraphTripPlanner)
        with patch('app.agents.langgraph_planner.search_nearby_restaurants',new=AsyncMock(return_value=[])):
            await planner._align_meals_to_route(plan,protected_ids={venue.item_id})
        self.assertIn(venue,day.meals)
